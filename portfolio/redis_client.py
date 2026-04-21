import time

import redis
from redis.sentinel import Sentinel

from portfolio.config import settings
from portfolio.metrics import (
    health_status,
    queue_depth,
    redis_connected_replicas,
    redis_master_link_status,
    redis_reconnect_total,
    redis_role,
    redis_sentinel_master_ok,
    redis_sentinel_quorum_ok,
)

_redis_client: redis.Redis | None = None
_redis_sentinel: Sentinel | None = None


def _as_str(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _pairs_to_dict(raw) -> dict[str, str]:
    if isinstance(raw, dict):
        return {_as_str(key): _as_str(value) for key, value in raw.items()}

    if isinstance(raw, (list, tuple)):
        items = list(raw)
        parsed: dict[str, str] = {}
        for idx in range(0, len(items) - 1, 2):
            parsed[_as_str(items[idx])] = _as_str(items[idx + 1])
        return parsed

    return {}


def _sentinel_nodes() -> list[tuple[str, int]]:
    nodes: list[tuple[str, int]] = []
    for node in settings.redis_sentinel_nodes.split(","):
        node = node.strip()
        if not node:
            continue
        host, port = node.split(":")
        nodes.append((host, int(port)))
    return nodes


def _build_sentinel() -> Sentinel:
    return Sentinel(
        _sentinel_nodes(),
        socket_timeout=settings.redis_socket_timeout,
        sentinel_kwargs={
            "socket_timeout": settings.redis_socket_timeout,
            "socket_connect_timeout": settings.redis_socket_connect_timeout,
            "password": settings.redis_password or None,
        },
    )


def _create_client() -> redis.Redis:
    global _redis_sentinel
    if settings.redis_sentinel_enabled and settings.redis_sentinel_nodes:
        _redis_sentinel = _build_sentinel()
        return _redis_sentinel.master_for(
            settings.redis_sentinel_master,
            socket_timeout=settings.redis_socket_timeout,
            socket_connect_timeout=settings.redis_socket_connect_timeout,
            max_connections=settings.redis_max_connections,
            health_check_interval=settings.redis_health_check_interval,
            password=settings.redis_password or None,
            decode_responses=True,
        )
    _redis_sentinel = None
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password or None,
        socket_connect_timeout=settings.redis_socket_connect_timeout,
        socket_timeout=settings.redis_socket_timeout,
        max_connections=settings.redis_max_connections,
        health_check_interval=settings.redis_health_check_interval,
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


def get_redis_runtime_status() -> dict:
    status = {
        "master_reachable": False,
        "master_writable": False,
        "sentinel_master_ok": not settings.redis_sentinel_enabled,
        "sentinel_quorum_ok": True,
        "replica_count": 0,
        "replica_links": [],
        "role": "unknown",
        "reasons": [],
    }

    redis_role.clear()
    redis_master_link_status.clear()

    try:
        client = get_redis()
        replication = client.info("replication")
    except Exception as exc:  # noqa: BLE001
        health_status.labels(component="redis").set(0)
        redis_connected_replicas.set(0)
        redis_sentinel_master_ok.set(0)
        redis_sentinel_quorum_ok.set(0 if settings.redis_sentinel_enabled else 1)
        status["reasons"].append(f"redis_connection_error:{type(exc).__name__}")
        return status

    role = str(replication.get("role", "unknown"))
    status["role"] = role
    redis_role.labels(role=role).set(1)
    status["master_reachable"] = role == "master"
    status["master_writable"] = role == "master"

    replica_count = int(replication.get("connected_slaves", 0))
    status["replica_count"] = replica_count
    redis_connected_replicas.set(replica_count)

    replica_links: list[dict] = []
    for idx in range(replica_count):
        raw = replication.get(f"slave{idx}")
        if not raw:
            continue
        parts = _pairs_to_dict(raw)
        if not parts:
            for token in str(raw).split(","):
                if "=" not in token:
                    continue
                key, value = token.split("=", 1)
                parts[key] = value
        replica_name = f"{parts.get('ip', 'unknown')}:{parts.get('port', 'unknown')}"
        link_ok = parts.get("state") == "online"
        replica_links.append({"replica": replica_name, "link_ok": link_ok})
        redis_master_link_status.labels(replica=replica_name).set(1 if link_ok else 0)
    status["replica_links"] = replica_links

    if settings.redis_sentinel_enabled and _redis_sentinel is not None:
        try:
            master_host, _master_port = _redis_sentinel.discover_master(settings.redis_sentinel_master)
            raw_master_meta = _redis_sentinel.sentinels[0].execute_command(
                f"SENTINEL MASTER {settings.redis_sentinel_master}"
            )
            master_meta = _pairs_to_dict(raw_master_meta)
            flags = {
                flag.strip()
                for flag in _as_str(master_meta.get("flags", "")).split(",")
                if flag.strip()
            }
            status["sentinel_master_ok"] = bool(master_host) and not {
                "s_down",
                "o_down",
                "disconnected",
            } & flags

            ckquorum = _redis_sentinel.sentinels[0].execute_command(
                f"SENTINEL CKQUORUM {settings.redis_sentinel_master}"
            )
            status["sentinel_quorum_ok"] = _as_str(ckquorum).startswith("OK")
        except Exception as exc:  # noqa: BLE001
            status["sentinel_master_ok"] = False
            status["sentinel_quorum_ok"] = False
            status["reasons"].append(f"redis_sentinel_error:{type(exc).__name__}")

    redis_sentinel_master_ok.set(1 if status["sentinel_master_ok"] else 0)
    redis_sentinel_quorum_ok.set(1 if status["sentinel_quorum_ok"] else 0)

    if status["master_writable"] and status["sentinel_master_ok"]:
        health_status.labels(component="redis").set(1)
    else:
        health_status.labels(component="redis").set(0)

    return status
