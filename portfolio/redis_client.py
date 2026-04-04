import time

import redis
from redis.sentinel import Sentinel

from portfolio.config import settings
from portfolio.metrics import health_status, queue_depth, redis_reconnect_total

_redis_client: redis.Redis | None = None


def _create_client() -> redis.Redis:
    if settings.redis_sentinel_enabled and settings.redis_sentinel_nodes:
        sentinels = []
        for node in settings.redis_sentinel_nodes.split(","):
            host, port = node.strip().split(":")
            sentinels.append((host, int(port)))
        sentinel = Sentinel(
            sentinels,
            socket_timeout=1,
            sentinel_kwargs={"socket_timeout": 1},
        )
        return sentinel.master_for(
            settings.redis_sentinel_master,
            socket_timeout=1,
            socket_connect_timeout=1,
            decode_responses=True,
        )
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        socket_connect_timeout=1,
        socket_timeout=1,
        decode_responses=True,
    )


def init_redis_with_retry(retries: int, delay_sec: float) -> None:
    global _redis_client
    last_error = None
    for _ in range(retries):
        try:
            client = _create_client()
            client.ping()
            _redis_client = client
            health_status.labels(component="redis").set(1)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            health_status.labels(component="redis").set(0)
            time.sleep(delay_sec)
    raise RuntimeError(f"Redis init failed: {last_error}")


def reconnect_redis() -> redis.Redis:
    global _redis_client
    try:
        client = _create_client()
        client.ping()
        _redis_client = client
    except Exception:  # noqa: BLE001
        redis_reconnect_total.labels(result="failure").inc()
        health_status.labels(component="redis").set(0)
        raise
    redis_reconnect_total.labels(result="success").inc()
    health_status.labels(component="redis").set(1)
    return client


def get_redis() -> redis.Redis:
    if _redis_client is None:
        raise RuntimeError("Redis is not initialized")

    try:
        _redis_client.ping()
    except Exception:
        return reconnect_redis()
    health_status.labels(component="redis").set(1)
    return _redis_client


def ping_redis() -> bool:
    try:
        get_redis().ping()
        health_status.labels(component="redis").set(1)
        return True
    except Exception:
        health_status.labels(component="redis").set(0)
        return False


def update_queue_depth(queue_name: str) -> None:
    global _redis_client
    try:
        if _redis_client is None:
            return
        queue_depth.labels(queue=queue_name).set(_redis_client.llen(queue_name))
    except Exception:
        health_status.labels(component="redis").set(0)
