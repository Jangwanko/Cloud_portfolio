import json
import logging
import time
from datetime import datetime, timezone

from portfolio.config import settings
from portfolio.db import init_pool_with_retry, ping_db
from portfolio.queues import ingress_partition_queue
from portfolio.redis_client import (
    get_redis,
    init_redis_with_retry,
    ping_redis,
    reconnect_redis,
    update_queue_depth,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def replay_one(raw: str) -> bool:
    job_payload = json.loads(raw)
    job_payload["replay_count"] = int(job_payload.get("replay_count", 0)) + 1
    job_payload["replayed_at"] = now_iso()
    job_payload["retry_count"] = 0
    job_payload["next_retry_at"] = None

    queue_name = ingress_partition_queue(int(job_payload["room_id"]))
    redis_client = get_redis()
    redis_client.rpush(queue_name, json.dumps(job_payload))
    update_queue_depth(queue_name)
    return True


def main() -> None:
    init_pool_with_retry(settings.startup_retries, settings.startup_retry_delay)
    init_redis_with_retry(settings.startup_retries, settings.startup_retry_delay)
    logging.info(
        "DLQ replayer started. enabled=%s dlq=%s batch=%s interval=%s",
        settings.dlq_replay_enabled,
        settings.ingress_dlq,
        settings.dlq_replay_batch_size,
        settings.dlq_replay_interval_seconds,
    )

    while True:
        if not settings.dlq_replay_enabled:
            time.sleep(1)
            continue

        if not ping_db() or not ping_redis():
            time.sleep(1)
            continue

        try:
            redis_client = get_redis()
        except Exception:  # noqa: BLE001
            time.sleep(1)
            continue

        moved = 0
        for _ in range(settings.dlq_replay_batch_size):
            try:
                raw = redis_client.rpop(settings.ingress_dlq)
            except Exception:  # noqa: BLE001
                try:
                    reconnect_redis()
                except Exception:  # noqa: BLE001
                    pass
                break

            if raw is None:
                break

            try:
                replay_one(raw)
                moved += 1
            except Exception:  # noqa: BLE001
                # Put back if replay failed.
                redis_client.lpush(settings.ingress_dlq, raw)
                break

        update_queue_depth(settings.ingress_dlq)
        if moved > 0:
            logging.info("DLQ replay moved=%s", moved)

        time.sleep(settings.dlq_replay_interval_seconds)


if __name__ == "__main__":
    main()
