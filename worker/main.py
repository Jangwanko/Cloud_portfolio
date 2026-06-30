import json
import logging
import time
from datetime import datetime, timezone

from prometheus_client import start_http_server
from psycopg2 import InterfaceError, OperationalError

from portfolio.config import settings
from portfolio.db import get_conn, get_cursor, init_pool_with_retry, reconnect_pool
from portfolio.kafka_client import (
    build_ingress_consumer,
    build_notification_consumer,
    publish_dlq_job,
    publish_message_snapshot,
    publish_notification_job,
    publish_request_status,
)
from portfolio.metrics import (
    dlq_events_total,
    event_persist_lag_seconds,
    health_status,
    observe_worker_stage,
    queue_wait_seconds,
    registry,
    worker_failures_total,
    worker_last_success_timestamp,
    worker_processed_total,
    worker_processing_seconds,
)
from portfolio.state_store import store_request_status, upsert_request_status

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class RoomSequenceGapError(RuntimeError):
    pass


def request_status_key(request_id: str) -> str:
    return f"message_request_status:{request_id}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _message_row_response(row: dict) -> dict:
    created_at = row["created_at"]
    return {
        "id": row["id"],
        "request_id": row["request_id"],
        "status": "persisted",
        "room_id": row["room_id"],
        "room_seq": row["room_seq"],
        "user_id": row["user_id"],
        "event_type": row.get("event_type"),
        "category": row.get("category"),
        "payment_id": row.get("payment_id"),
        "body": row["body"],
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
    }


def _persist_message_with_cursor(job_payload: dict, cur) -> dict:
    route = job_payload["route"]
    request_id = job_payload["request_id"]
    room_id = job_payload["room_id"]
    user_id = job_payload["user_id"]
    body = job_payload["body"]
    event_type = job_payload.get("event_type")
    category = job_payload.get("category")
    payment_id = job_payload.get("payment_id")
    room_seq_raw = job_payload.get("room_seq")
    room_seq = int(room_seq_raw) if room_seq_raw is not None else None
    x_idempotency_key = job_payload.get("x_idempotency_key")

    if x_idempotency_key:
        cur.execute(
            "SELECT response_json FROM idempotency_keys WHERE route=%s AND idem_key=%s",
            (route, x_idempotency_key),
        )
        cached = cur.fetchone()
        if cached:
            response = cached["response_json"]
            if isinstance(response, dict) and "request_id" not in response:
                response["request_id"] = request_id
            return response

    cur.execute(
        """
        SELECT id, request_id, room_id, user_id, event_type, category, payment_id, body, room_seq, created_at
        FROM messages
        WHERE request_id=%s
        """,
        (request_id,),
    )
    existing = cur.fetchone()
    if existing is not None:
        return _message_row_response(existing)

    cur.execute("SELECT id FROM rooms WHERE id=%s", (room_id,))
    if cur.fetchone() is None:
        raise ValueError("Room not found")

    cur.execute("SELECT id FROM users WHERE id=%s", (user_id,))
    if cur.fetchone() is None:
        raise ValueError("User not found")

    cur.execute(
        "SELECT 1 FROM room_members WHERE room_id=%s AND user_id=%s",
        (room_id, user_id),
    )
    if cur.fetchone() is None:
        raise ValueError("Stream access denied")

    cur.execute(
        """
        INSERT INTO room_sequences (room_id, last_seq)
        VALUES (%s, 0)
        ON CONFLICT (room_id) DO NOTHING
        """,
        (room_id,),
    )
    cur.execute(
        "SELECT last_seq FROM room_sequences WHERE room_id=%s FOR UPDATE",
        (room_id,),
    )
    seq_row = cur.fetchone()
    last_seq = int(seq_row["last_seq"])

    expected_seq = last_seq + 1
    if room_seq is None:
        room_seq = expected_seq

    if room_seq <= last_seq:
        cur.execute(
            """
            SELECT id, request_id, room_id, user_id, event_type, category, payment_id, body, room_seq, created_at
            FROM messages
            WHERE room_id=%s AND room_seq=%s
            """,
            (room_id, room_seq),
        )
        duplicate = cur.fetchone()
        if duplicate is not None:
            return _message_row_response(duplicate)

    if room_seq > expected_seq:
        raise RoomSequenceGapError(
            f"Room sequence gap detected expected={expected_seq} got={room_seq}"
        )

    cur.execute(
        """
        INSERT INTO messages (request_id, room_id, user_id, event_type, category, payment_id, body, room_seq)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, request_id, room_id, user_id, event_type, category, payment_id, body, room_seq, created_at
        """,
        (request_id, room_id, user_id, event_type, category, payment_id, body, room_seq),
    )
    message = cur.fetchone()
    cur.execute(
        """
        UPDATE room_sequences
        SET last_seq=%s, updated_at=NOW()
        WHERE room_id=%s
        """,
        (room_seq, room_id),
    )

    response = _message_row_response(message)

    if x_idempotency_key:
        cur.execute(
            """
            INSERT INTO idempotency_keys (route, idem_key, response_json)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (route, idem_key)
            DO UPDATE SET response_json = EXCLUDED.response_json
            """,
            (route, x_idempotency_key, json.dumps(response)),
        )

    return response


def persist_message(job_payload: dict) -> dict:
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            response = _persist_message_with_cursor(job_payload, cur)
        conn.commit()
    return response


def persisted_status_payload(request_id: str, response: dict) -> dict:
    return {
        "request_id": request_id,
        "status": "persisted",
        "message_id": response["id"],
        "room_id": response["room_id"],
        "room_seq": response["room_seq"],
        "user_id": response["user_id"],
        "event_type": response.get("event_type"),
        "category": response.get("category"),
        "payment_id": response.get("payment_id"),
        "created_at": response["created_at"],
    }


def notification_attempt_payload(message_response: dict) -> dict:
    return {
        "message_id": message_response["id"],
        "room_id": message_response["room_id"],
        "body_preview": message_response["body"][:30],
        "event_type": message_response.get("event_type"),
        "category": message_response.get("category"),
    }


def insert_notification_attempt(cur, payload: dict) -> None:
    cur.execute(
        """
        INSERT INTO notification_attempts (message_id, room_id, payload)
        VALUES (%s, %s, %s::jsonb)
        """,
        (payload["message_id"], payload["room_id"], json.dumps(payload)),
    )


def queue_notification(message_response: dict) -> None:
    with observe_worker_stage("notification_enqueue"):
        payload = notification_attempt_payload(message_response)
        store_attempt(payload)


def update_request_status(request_id: str, payload: dict) -> None:
    with observe_worker_stage("request_status_update"):
        try:
            store_request_status(request_id, payload)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Failed to store request status in PostgreSQL request_id=%s error=%s", request_id, exc)
        try:
            publish_request_status(request_id, payload)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Failed to publish request status state request_id=%s error=%s", request_id, exc)


def publish_persisted_message_snapshot(response: dict) -> None:
    snapshot = {
        "id": response["id"],
        "request_id": response["request_id"],
        "stream_id": response["room_id"],
        "stream_seq": response["room_seq"],
        "user_id": response["user_id"],
        "event_type": response.get("event_type"),
        "category": response.get("category"),
        "payment_id": response.get("payment_id"),
        "body": response["body"],
        "created_at": response["created_at"],
    }
    try:
        publish_message_snapshot(response["id"], snapshot)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Failed to publish message snapshot message_id=%s error=%s", response["id"], exc)


def move_to_dlq(job_payload: dict, reason: str) -> None:
    request_id = job_payload["request_id"]
    job_payload["failed_reason"] = reason
    job_payload["failed_at"] = now_iso()
    publish_dlq_job(job_payload["room_id"], job_payload)
    dlq_events_total.labels(reason=reason).inc()
    update_request_status(
        request_id,
        {
            "request_id": request_id,
            "status": "failed_dlq",
            "room_id": job_payload.get("room_id"),
            "user_id": job_payload.get("user_id"),
            "reason": reason,
            "retry_count": int(job_payload.get("retry_count", 0)),
            "failed_at": job_payload["failed_at"],
        },
    )


def mark_inline_retry(job_payload: dict) -> float:
    retry_count = int(job_payload.get("retry_count", 0)) + 1
    delay = min(
        settings.ingress_retry_base_delay_seconds * (2 ** (retry_count - 1)),
        settings.ingress_retry_max_delay_seconds,
    )
    job_payload["retry_count"] = retry_count
    job_payload["next_retry_at"] = time.time() + delay
    try:
        update_request_status(
            job_payload["request_id"],
            {
                "request_id": job_payload["request_id"],
                "status": "queued",
                "room_id": job_payload.get("room_id"),
                "user_id": job_payload.get("user_id"),
                "room_seq": job_payload.get("room_seq"),
                "retry_count": retry_count,
                "next_retry_at": datetime.fromtimestamp(
                    float(job_payload["next_retry_at"]), tz=timezone.utc
                ).isoformat(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logging.warning("Failed to update inline retry status request_id=%s error=%s", job_payload["request_id"], exc)
    return delay


def store_attempt(payload: dict) -> None:
    last_error = None
    for attempt in range(2):
        try:
            with observe_worker_stage("notification_db_insert"):
                with get_conn() as conn:
                    with get_cursor(conn) as cur:
                        insert_notification_attempt(cur, payload)
                    conn.commit()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == 0:
                reconnect_pool()
                time.sleep(1)
                continue
            raise last_error


def publish_persisted_status(request_id: str, payload: dict) -> None:
    try:
        publish_request_status(request_id, payload)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Failed to publish request status state request_id=%s error=%s", request_id, exc)


def persist_ingress_job(job_payload: dict) -> dict:
    request_id = job_payload["request_id"]

    with get_conn() as conn:
        with get_cursor(conn) as cur:
            with observe_worker_stage("db_persist"):
                response = _persist_message_with_cursor(job_payload, cur)

            status_payload = persisted_status_payload(request_id, response)
            with observe_worker_stage("request_status_update"):
                upsert_request_status(cur, request_id, status_payload)

        conn.commit()

    publish_persisted_status(request_id, status_payload)
    publish_persisted_message_snapshot(response)
    publish_notification_job(response["room_id"], notification_attempt_payload(response))
    return response


def handle_ingress_job(raw: str) -> None:
    job_payload = raw if isinstance(raw, dict) else json.loads(raw)
    request_id = job_payload["request_id"]
    queued_at = job_payload.get("queued_at")

    if queued_at:
        try:
            enqueued_at = datetime.fromisoformat(str(queued_at)).timestamp()
            queue_wait_seconds.observe(max(0, time.time() - enqueued_at))
        except Exception:  # noqa: BLE001
            pass

    while True:
        next_retry_at = job_payload.get("next_retry_at")
        if next_retry_at is not None and float(next_retry_at) > time.time():
            time.sleep(max(0, float(next_retry_at) - time.time()))

        try:
            response = persist_ingress_job(job_payload)
            if queued_at:
                try:
                    accepted_at = datetime.fromisoformat(str(queued_at)).timestamp()
                    event_persist_lag_seconds.observe(max(0, time.time() - accepted_at))
                except Exception:  # noqa: BLE001
                    pass
            return
        except ValueError as exc:
            update_request_status(
                request_id,
                {
                    "request_id": request_id,
                    "status": "failed",
                    "room_id": job_payload.get("room_id"),
                    "user_id": job_payload.get("user_id"),
                    "reason": str(exc),
                },
            )
            return
        except RoomSequenceGapError:
            retry_count = int(job_payload.get("retry_count", 0))
            if retry_count >= settings.ingress_max_retries:
                move_to_dlq(job_payload, "room_sequence_gap")
                return
            delay = mark_inline_retry(job_payload)
            time.sleep(delay)
        except (OperationalError, InterfaceError, RuntimeError) as exc:
            delay = mark_inline_retry(job_payload)
            try:
                reconnect_pool()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(delay)


def handle_notification_job(raw: str) -> None:
    payload = raw if isinstance(raw, dict) else json.loads(raw)
    logging.info(
        "Notification processed message_id=%s room_id=%s preview=%s",
        payload.get("message_id"),
        payload.get("room_id"),
        payload.get("body_preview"),
    )
    store_attempt(payload)


def run_kafka_notification_loop() -> None:
    health_status.labels(component="worker").set(1)
    logging.info(
        "Worker started with Kafka notifications. topic=%s group=%s metrics_port=%s",
        settings.kafka_notification_topic,
        settings.kafka_notification_consumer_group,
        settings.worker_metrics_port,
    )

    while True:
        try:
            consumer = build_notification_consumer()
        except Exception as exc:  # noqa: BLE001
            worker_failures_total.inc()
            health_status.labels(component="worker").set(0)
            logging.exception("Kafka notification consumer init failed: %s", exc)
            time.sleep(2)
            continue

        try:
            while True:
                records = consumer.poll(timeout_ms=1000, max_records=20)
                if not records:
                    continue

                for messages in records.values():
                    for message in messages:
                        started_at = time.perf_counter()
                        try:
                            handle_notification_job(message.value)
                            consumer.commit()
                            worker_processed_total.labels(result="success").inc()
                            worker_last_success_timestamp.set(time.time())
                            health_status.labels(component="worker").set(1)
                        except Exception as exc:  # noqa: BLE001
                            worker_processed_total.labels(result="failure").inc()
                            worker_failures_total.inc()
                            health_status.labels(component="worker").set(0)
                            logging.exception("Kafka notification worker failed: %s", exc)
                            time.sleep(1)
                        finally:
                            worker_processing_seconds.observe(time.perf_counter() - started_at)
        except Exception as exc:  # noqa: BLE001
            worker_failures_total.inc()
            health_status.labels(component="worker").set(0)
            logging.exception("Kafka notification loop failed: %s", exc)
            try:
                consumer.close()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)


def run_kafka_worker_loop() -> None:
    health_status.labels(component="worker").set(1)
    logging.info(
        "Worker started with Kafka ingress. topic=%s group=%s dlq_topic=%s metrics_port=%s",
        settings.kafka_ingress_topic,
        settings.kafka_consumer_group,
        settings.kafka_dlq_topic,
        settings.worker_metrics_port,
    )

    while True:
        try:
            consumer = build_ingress_consumer()
        except Exception as exc:  # noqa: BLE001
            worker_failures_total.inc()
            health_status.labels(component="worker").set(0)
            logging.exception("Kafka consumer init failed: %s", exc)
            time.sleep(2)
            continue

        try:
            while True:
                records = consumer.poll(timeout_ms=1000, max_records=20)
                if not records:
                    continue

                for messages in records.values():
                    for message in messages:
                        started_at = time.perf_counter()
                        try:
                            handle_ingress_job(message.value)
                            consumer.commit()
                            worker_processed_total.labels(result="success").inc()
                            worker_last_success_timestamp.set(time.time())
                            health_status.labels(component="worker").set(1)
                        except Exception as exc:  # noqa: BLE001
                            worker_processed_total.labels(result="failure").inc()
                            worker_failures_total.inc()
                            health_status.labels(component="worker").set(0)
                            logging.exception("Kafka worker failed: %s", exc)
                            time.sleep(1)
                        finally:
                            worker_processing_seconds.observe(time.perf_counter() - started_at)
        except Exception as exc:  # noqa: BLE001
            worker_failures_total.inc()
            health_status.labels(component="worker").set(0)
            logging.exception("Kafka consumer loop failed: %s", exc)
            try:
                consumer.close()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)


def main() -> None:
    init_pool_with_retry(settings.startup_retries, settings.startup_retry_delay)
    start_http_server(settings.worker_metrics_port, registry=registry)
    if settings.worker_mode == "notification":
        run_kafka_notification_loop()
        return
    run_kafka_worker_loop()


if __name__ == "__main__":
    main()
