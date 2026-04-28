import json
import logging
import time
from datetime import datetime, timezone

from prometheus_client import start_http_server

from portfolio.config import settings
from portfolio.db import init_pool_with_retry, ping_db
from portfolio.kafka_client import build_dlq_consumer, publish_ingress_job
from portfolio.metrics import dlq_replay_total, registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def replay_one(raw: str) -> bool:
    job_payload = raw if isinstance(raw, dict) else json.loads(raw)
    replay_count = int(job_payload.get("replay_count", 0))
    if replay_count >= settings.dlq_replay_max_count:
        logging.warning(
            "Kafka DLQ replay skipped request_id=%s reason=max_replay_count replay_count=%s",
            job_payload.get("request_id"),
            replay_count,
        )
        dlq_replay_total.labels(result="skipped_max_replay").inc()
        return False

    job_payload["replay_count"] = replay_count + 1
    job_payload["replayed_at"] = now_iso()
    job_payload["retry_count"] = 0
    job_payload["next_retry_at"] = None

    publish_ingress_job(job_payload["room_id"], job_payload)
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
                moved = 0
                for messages in records.values():
                    for message in messages:
                        replay_one(message.value)
                        consumer.commit()
                        moved += 1
                if moved > 0:
                    logging.info("Kafka DLQ replay moved=%s", moved)
                time.sleep(settings.dlq_replay_interval_seconds)
        except Exception as exc:  # noqa: BLE001
            logging.exception("Kafka DLQ replay loop failed: %s", exc)
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
