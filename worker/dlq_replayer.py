import json
import logging
import time
from datetime import datetime, timezone

from kafka.structs import OffsetAndMetadata, TopicPartition
from prometheus_client import start_http_server

from portfolio.config import settings
from portfolio.db import init_pool_with_retry, ping_db
from portfolio.event_envelope import MAX_JSON_WIRE_NESTING_DEPTH, validate_json_structure
from portfolio.kafka_client import build_dlq_consumer, is_invalid_kafka_payload, publish_ingress_job
from portfolio.metrics import dlq_replay_total, registry
from portfolio.state_store import (
    claim_dlq_replay,
    mark_dlq_replay_published,
    release_dlq_replay_claim,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_MAX_POSTGRES_BIGINT = 9_223_372_036_854_775_807


class ReplayDatabaseUnavailable(RuntimeError):
    pass


class ReplayClaimInProgress(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _topic_partition(message) -> TopicPartition:
    return TopicPartition(message.topic, message.partition)


def _has_conflicting_event_aliases(payload: dict) -> bool:
    for canonical, legacy in (
        ("stream_id", "room_id"),
        ("actor_id", "user_id"),
        ("stream_seq", "room_seq"),
    ):
        if canonical not in payload or legacy not in payload:
            continue
        if (
            type(payload[canonical]) is not type(payload[legacy])
            or payload[canonical] != payload[legacy]
        ):
            return True
    return False


def _commit_processed_record(consumer, message) -> None:
    partition = _topic_partition(message)
    consumer.commit(
        offsets={
            partition: OffsetAndMetadata(message.offset + 1, "", -1),
        }
    )


def _process_replay_batch(consumer, records: dict) -> int:
    if not ping_db():
        raise ReplayDatabaseUnavailable("PostgreSQL primary is unavailable")

    failed_partitions: set[TopicPartition] = set()
    moved = 0

    for messages in records.values():
        for message in messages:
            partition = _topic_partition(message)
            if partition in failed_partitions:
                continue

            try:
                replayed = replay_one(message.value)
                _commit_processed_record(consumer, message)
            except ReplayClaimInProgress:
                failed_partitions.add(partition)
                consumer.seek(partition, message.offset)
                dlq_replay_total.labels(result="claim_in_progress").inc()
                logging.info(
                    "Kafka DLQ replay claim is owned by another publisher; partition rewound "
                    "topic=%s partition=%s offset=%s",
                    message.topic,
                    message.partition,
                    message.offset,
                )
                continue
            except Exception as exc:  # noqa: BLE001
                failed_partitions.add(partition)
                try:
                    consumer.seek(partition, message.offset)
                except Exception:  # noqa: BLE001
                    logging.exception(
                        "Kafka DLQ replayer failed to seek topic=%s partition=%s offset=%s",
                        message.topic,
                        message.partition,
                        message.offset,
                    )
                    raise
                dlq_replay_total.labels(result="failure").inc()
                logging.exception(
                    "Kafka DLQ replay failed; partition rewound topic=%s partition=%s "
                    "offset=%s error=%s",
                    message.topic,
                    message.partition,
                    message.offset,
                    exc,
                )
                continue

            if replayed:
                moved += 1

    return moved


def replay_one(raw: str) -> bool:
    try:
        job_payload = raw if isinstance(raw, dict) else json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        dlq_replay_total.labels(result="skipped_invalid_payload").inc()
        logging.error("Skipping malformed DLQ payload that is not valid JSON")
        return False
    if isinstance(job_payload, dict) and is_invalid_kafka_payload(job_payload):
        dlq_replay_total.labels(result="skipped_invalid_payload").inc()
        logging.error("Skipping DLQ payload marked as invalid ingress")
        return False
    try:
        validate_json_structure(job_payload, max_depth=MAX_JSON_WIRE_NESTING_DEPTH)
    except (TypeError, ValueError, RecursionError):
        dlq_replay_total.labels(result="skipped_invalid_payload").inc()
        logging.error("Skipping DLQ payload with an invalid JSON structure")
        return False
    if isinstance(job_payload, dict) and _has_conflicting_event_aliases(job_payload):
        dlq_replay_total.labels(result="skipped_invalid_payload").inc()
        logging.error("Skipping DLQ payload with conflicting canonical/legacy identifiers")
        return False
    if isinstance(job_payload, dict) and "room_id" not in job_payload and "stream_id" in job_payload:
        job_payload["room_id"] = job_payload["stream_id"]
    if (
        not isinstance(job_payload, dict)
        or not isinstance(job_payload.get("request_id"), str)
        or not job_payload.get("request_id")
        or len(job_payload.get("request_id", "")) > 80
        or "\x00" in job_payload.get("request_id", "")
        or isinstance(job_payload.get("room_id"), bool)
        or not isinstance(job_payload.get("room_id"), int)
        or job_payload.get("room_id", 0) <= 0
        or job_payload.get("room_id", 0) > _MAX_POSTGRES_BIGINT
    ):
        dlq_replay_total.labels(result="skipped_invalid_payload").inc()
        logging.error("Skipping malformed DLQ payload with missing request/stream identifiers")
        return False
    replay_count = job_payload.get("replay_count", 0)
    if (
        type(replay_count) is not int
        or replay_count < 0
        or replay_count > _MAX_POSTGRES_BIGINT
    ):
        dlq_replay_total.labels(result="skipped_invalid_payload").inc()
        logging.error("Skipping malformed DLQ payload with invalid replay_count")
        return False
    if replay_count >= settings.dlq_replay_max_count:
        logging.warning(
            "Kafka DLQ replay skipped request_id=%s reason=max_replay_count replay_count=%s",
            job_payload.get("request_id"),
            replay_count,
        )
        dlq_replay_total.labels(result="skipped_max_replay").inc()
        return False
    if replay_count >= _MAX_POSTGRES_BIGINT:
        dlq_replay_total.labels(result="skipped_max_replay").inc()
        logging.warning(
            "Kafka DLQ replay skipped request_id=%s reason=replay_count_overflow",
            job_payload.get("request_id"),
        )
        return False

    request_id = job_payload["request_id"]
    claim_state, owner_token = claim_dlq_replay(request_id, replay_count)
    if claim_state == "persisted":
        dlq_replay_total.labels(result="skipped_persisted").inc()
        return False
    if claim_state == "published":
        dlq_replay_total.labels(result="skipped_published").inc()
        return False
    if claim_state != "claimed" or owner_token is None:
        raise ReplayClaimInProgress(
            f"Replay claim is in progress request_id={request_id} generation={replay_count}"
        )

    job_payload["replay_count"] = replay_count + 1
    job_payload["replayed_at"] = now_iso()
    job_payload["retry_count"] = 0
    job_payload["next_retry_at"] = None

    try:
        publish_ingress_job(job_payload["room_id"], job_payload)
    except Exception:
        try:
            release_dlq_replay_claim(request_id, replay_count, owner_token)
        except Exception as release_error:  # noqa: BLE001
            logging.warning(
                "Automatic DLQ replay claim release failed request_id=%s error=%s",
                request_id,
                release_error,
            )
        raise
    try:
        if not mark_dlq_replay_published(request_id, replay_count, owner_token):
            logging.warning("Automatic DLQ replay claim was no longer owned request_id=%s", request_id)
    except Exception as mark_error:  # noqa: BLE001
        logging.warning(
            "Automatic DLQ replay published but claim finalization failed request_id=%s error=%s",
            request_id,
            mark_error,
        )
    dlq_replay_total.labels(result="replayed").inc()
    return True


def run_kafka_replayer_loop() -> None:
    logging.info(
        "Kafka DLQ replayer started. enabled=%s dlq_topic=%s interval=%s",
        settings.dlq_replay_enabled,
        settings.kafka_dlq_topic,
        settings.dlq_replay_interval_seconds,
    )

    while True:
        if not settings.dlq_replay_enabled:
            time.sleep(1)
            continue

        if not ping_db():
            time.sleep(1)
            continue

        try:
            consumer = build_dlq_consumer()
        except Exception as exc:  # noqa: BLE001
            logging.exception("Kafka DLQ consumer init failed: %s", exc)
            time.sleep(2)
            continue

        try:
            while True:
                records = consumer.poll(timeout_ms=1000, max_records=settings.dlq_replay_batch_size)
                moved = _process_replay_batch(consumer, records)
                if moved > 0:
                    logging.info("Kafka DLQ replay moved=%s", moved)
                time.sleep(settings.dlq_replay_interval_seconds)
        except ReplayDatabaseUnavailable:
            logging.warning("Kafka DLQ replay paused because PostgreSQL primary is unavailable")
        except Exception as exc:  # noqa: BLE001
            logging.exception("Kafka DLQ replay loop failed: %s", exc)
        finally:
            try:
                consumer.close()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(2)


def main() -> None:
    init_pool_with_retry(settings.startup_retries, settings.startup_retry_delay)
    start_http_server(settings.dlq_replayer_metrics_port, registry=registry)
    run_kafka_replayer_loop()


if __name__ == "__main__":
    main()
