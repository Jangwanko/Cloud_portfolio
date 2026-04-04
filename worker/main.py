import json
import logging
import time

from portfolio.config import settings
from portfolio.db import get_conn, get_cursor, init_pool_with_retry
from portfolio.redis_client import get_redis, init_redis_with_retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def store_attempt(payload: dict) -> None:
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


def main() -> None:
    init_pool_with_retry(settings.startup_retries, settings.startup_retry_delay)
    init_redis_with_retry(settings.startup_retries, settings.startup_retry_delay)

    redis_client = get_redis()
    queue = settings.notification_queue
    logging.info("Worker started. queue=%s", queue)

    while True:
        _, raw = redis_client.brpop(queue, timeout=5) or (None, None)
        if not raw:
            continue

        try:
            payload = json.loads(raw)
            logging.info(
                "Notification processed message_id=%s room_id=%s preview=%s",
                payload.get("message_id"),
                payload.get("room_id"),
                payload.get("body_preview"),
            )
            store_attempt(payload)
        except Exception as exc:  # noqa: BLE001
            logging.exception("Worker failed: %s", exc)
            time.sleep(1)


if __name__ == "__main__":
    main()
