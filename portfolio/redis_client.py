import time

import redis
from redis.sentinel import Sentinel

from portfolio.config import settings

_redis_client: redis.Redis | None = None


def init_redis_with_retry(retries: int, delay_sec: float) -> None:
    global _redis_client
    last_error = None
    for _ in range(retries):
        try:
            if settings.redis_sentinel_enabled and settings.redis_sentinel_nodes:
                sentinels = []
                for node in settings.redis_sentinel_nodes.split(","):
                    host, port = node.strip().split(":")
                    sentinels.append((host, int(port)))
                sentinel = Sentinel(sentinels, socket_timeout=1)
                client = sentinel.master_for(
                    settings.redis_sentinel_master,
                    socket_timeout=1,
                    decode_responses=True,
                )
            else:
                client = redis.Redis(
                    host=settings.redis_host,
                    port=settings.redis_port,
                    decode_responses=True,
                )
            client.ping()
            _redis_client = client
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(delay_sec)
    raise RuntimeError(f"Redis init failed: {last_error}")


def get_redis() -> redis.Redis:
    if _redis_client is None:
        raise RuntimeError("Redis is not initialized")
    return _redis_client


def ping_redis() -> bool:
    try:
        get_redis().ping()
        return True
    except Exception:
        return False
