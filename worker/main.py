import json
import logging
import time

from prometheus_client import start_http_server
from psycopg2 import InterfaceError, OperationalError

from portfolio.config import settings
from portfolio.db import get_conn, get_cursor, init_pool_with_retry, reconnect_pool
from portfolio.metrics import (
    health_status,
    registry,
    worker_failures_total,
    worker_last_success_timestamp,
    worker_processed_total,
    worker_processing_seconds,
)
from portfolio.redis_client import get_redis, init_redis_with_retry, reconnect_redis, update_queue_depth

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def request_status_key(request_id: str) -> str:
    return f"message_request_status:{request_id}"


def persist_message(job_payload: dict) -> dict:
    route = job_payload["route"]
    request_id = job_payload["request_id"]
    room_id = job_payload["room_id"]
    user_id = job_payload["user_id"]
    body = job_payload["body"]
    x_idempotency_key = job_payload.get("x_idempotency_key")

    with get_conn() as conn:
        with get_cursor(conn) as cur:
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
                SELECT id, request_id, room_id, user_id, body, created_at
                FROM messages
                WHERE request_id=%s
                """,
                (request_id,),
            )
            existing = cur.fetchone()
            if existing is not None:
                return {
                    "id": existing["id"],
                    "request_id": existing["request_id"],
                    "status": "persisted",
                    "room_id": existing["room_id"],
                    "user_id": existing["user_id"],
                    "body": existing["body"],
                    "created_at": existing["created_at"].isoformat(),
                }

            cur.execute("SELECT id FROM rooms WHERE id=%s", (room_id,))
            if cur.fetchone() is None:
                raise ValueError("Room not found")

            cur.execute("SELECT id FROM users WHERE id=%s", (user_id,))
            if cur.fetchone() is None:
                raise ValueError("User not found")

            cur.execute(
                """
                INSERT INTO messages (request_id, room_id, user_id, body)
                VALUES (%s, %s, %s, %s)
                RETURNING id, request_id, room_id, user_id, body, created_at
                """,
                (request_id, room_id, user_id, body),
            )
            message = cur.fetchone()

            response = {
                "id": message["id"],
                "request_id": message["request_id"],
                "status": "persisted",
                "room_id": message["room_id"],
                "user_id": message["user_id"],
                "body": message["body"],
                "created_at": message["created_at"].isoformat(),
            }

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

            conn.commit()
            return response


def queue_notification(message_response: dict) -> None:
    redis_client = get_redis()
    redis_client.lpush(
        settings.notification_queue,
        json.dumps(
            {
                "message_id": message_response["id"],
                "room_id": message_response["room_id"],
                "body_preview": message_response["body"][:30],
            }
        ),
    )
    update_queue_depth(settings.notification_queue)


def update_request_status(request_id: str, payload: dict) -> None:
    get_redis().set(request_status_key(request_id), json.dumps(payload))


def store_attempt(payload: dict) -> None:
    last_error = None
    for attempt in range(2):
        try:
            with get_conn() as conn:
                with get_cursor(conn) as cur:
                    cur.execute(
                        """
                        INSERT INTO notification_attempts (message_id, room_id, payload)
                        VALUES (%s, %s, %s::jsonb)
                        """,
                        (payload["message_id"], payload["room_id"], json.dumps(payload)),
                    )
                conn.commit()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == 0:
                reconnect_pool()
                time.sleep(1)
                continue
            raise last_error


def handle_ingress_job(raw: str) -> None:
    job_payload = json.loads(raw)
    request_id = job_payload["request_id"]

    try:
        response = persist_message(job_payload)
        update_request_status(
            request_id,
            {
                "request_id": request_id,
                "status": "persisted",
                "message_id": response["id"],
                "room_id": response["room_id"],
                "user_id": response["user_id"],
                "created_at": response["created_at"],
            },
        )
        queue_notification(response)
    except ValueError as exc:
        update_request_status(
            request_id,
            {
                "request_id": request_id,
                "status": "failed",
                "reason": str(exc),
            },
        )
    except (OperationalError, InterfaceError, RuntimeError):
        get_redis().rpush(settings.ingress_queue, raw)
        update_queue_depth(settings.ingress_queue)
        try:
            reconnect_pool()
        except Exception:  # noqa: BLE001
            pass
        raise


def handle_notification_job(raw: str) -> None:
    payload = json.loads(raw)
    logging.info(
        "Notification processed message_id=%s room_id=%s preview=%s",
        payload.get("message_id"),
        payload.get("room_id"),
        payload.get("body_preview"),
    )
    store_attempt(payload)


def main() -> None:
    init_pool_with_retry(settings.startup_retries, settings.startup_retry_delay)
    init_redis_with_retry(settings.startup_retries, settings.startup_retry_delay)
    start_http_server(settings.worker_metrics_port, registry=registry)

    redis_client = get_redis()
    health_status.labels(component="worker").set(1)
    logging.info(
        "Worker started. ingress_queue=%s notification_queue=%s metrics_port=%s",
        settings.ingress_queue,
        settings.notification_queue,
        settings.worker_metrics_port,
    )

    while True:
        try:
            queue_name, raw = redis_client.brpop(
                [settings.ingress_queue, settings.notification_queue],
                timeout=5,
            ) or (None, None)
        except Exception:
            health_status.labels(component="redis").set(0)
            worker_failures_total.inc()
            time.sleep(1)
            redis_client = reconnect_redis()
            continue

        update_queue_depth(settings.ingress_queue)
        update_queue_depth(settings.notification_queue)

        if not raw or not queue_name:
            continue

        started_at = time.perf_counter()
        try:
            if queue_name == settings.ingress_queue:
                handle_ingress_job(raw)
            else:
                handle_notification_job(raw)
            worker_processed_total.labels(result="success").inc()
            worker_last_success_timestamp.set(time.time())
            health_status.labels(component="worker").set(1)
        except Exception as exc:  # noqa: BLE001
            worker_processed_total.labels(result="failure").inc()
            worker_failures_total.inc()
            health_status.labels(component="worker").set(0)
            logging.exception("Worker failed: %s", exc)
            time.sleep(1)
        finally:
            worker_processing_seconds.observe(time.perf_counter() - started_at)
            update_queue_depth(settings.ingress_queue)
            update_queue_depth(settings.notification_queue)


if __name__ == "__main__":
    main()
