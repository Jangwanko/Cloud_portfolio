import time
from contextlib import contextmanager

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.responses import Response


registry = CollectorRegistry()

api_requests_total = Counter(
    "messaging_api_requests_total",
    "Total API requests",
    ["method", "path", "status"],
    registry=registry,
)

api_request_latency_seconds = Histogram(
    "messaging_api_request_latency_seconds",
    "API request latency in seconds",
    ["method", "path"],
    registry=registry,
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)

health_status = Gauge(
    "messaging_health_status",
    "Component health status",
    ["component"],
    registry=registry,
)

db_pool_in_use = Gauge(
    "messaging_db_pool_in_use",
    "DB connections checked out from pool",
    registry=registry,
)

db_reconnect_total = Counter(
    "messaging_db_reconnect_total",
    "DB pool reconnection attempts",
    ["result"],
    registry=registry,
)

db_failure_total = Counter(
    "messaging_db_failure_total",
    "DB failures grouped by reason",
    ["reason"],
    registry=registry,
)

redis_reconnect_total = Counter(
    "messaging_redis_reconnect_total",
    "Redis reconnection attempts",
    ["result"],
    registry=registry,
)

redis_role = Gauge(
    "messaging_redis_role",
    "Current Redis role visibility from the API connection",
    ["role"],
    registry=registry,
)

redis_master_link_status = Gauge(
    "messaging_redis_master_link_status",
    "Redis replica master link status where 1 means healthy and 0 means unhealthy",
    ["replica"],
    registry=registry,
)

redis_connected_replicas = Gauge(
    "messaging_redis_connected_replicas",
    "Number of Redis replicas currently connected to the writable master",
    registry=registry,
)

redis_sentinel_master_ok = Gauge(
    "messaging_redis_sentinel_master_ok",
    "Whether Sentinel can currently identify a writable master",
    registry=registry,
)

redis_sentinel_quorum_ok = Gauge(
    "messaging_redis_sentinel_quorum_ok",
    "Whether known Sentinel peers still satisfy the configured quorum",
    registry=registry,
)

queue_depth = Gauge(
    "messaging_queue_depth",
    "Current queue depth",
    ["queue"],
    registry=registry,
)

worker_processed_total = Counter(
    "messaging_worker_processed_total",
    "Worker processed events",
    ["result"],
    registry=registry,
)

worker_processing_seconds = Histogram(
    "messaging_worker_processing_seconds",
    "Worker processing time in seconds",
    registry=registry,
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)

worker_last_success_timestamp = Gauge(
    "messaging_worker_last_success_timestamp",
    "Unix timestamp of the last successful worker job",
    registry=registry,
)

worker_failures_total = Counter(
    "messaging_worker_failures_total",
    "Worker failures",
    registry=registry,
)

postgres_is_primary = Gauge(
    "messaging_postgres_is_primary",
    "Whether PostgreSQL primary is reachable through pgpool",
    registry=registry,
)

postgres_standby_count = Gauge(
    "messaging_postgres_standby_count",
    "Number of PostgreSQL standby nodes reported as up by pgpool",
    registry=registry,
)

postgres_sync_standby_count = Gauge(
    "messaging_postgres_sync_standby_count",
    "Number of PostgreSQL standbys currently reported as sync or quorum",
    registry=registry,
)

postgres_replication_state_count = Gauge(
    "messaging_postgres_replication_state_count",
    "Count of PostgreSQL standbys by replication state",
    ["state"],
    registry=registry,
)

postgres_replication_sync_state_count = Gauge(
    "messaging_postgres_replication_sync_state_count",
    "Count of PostgreSQL standbys by replication sync state",
    ["sync_state"],
    registry=registry,
)

postgres_replication_delay_bytes_max = Gauge(
    "messaging_postgres_replication_delay_bytes_max",
    "Maximum PostgreSQL replication delay reported by pgpool in bytes",
    registry=registry,
)


def metrics_response() -> Response:
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)


@contextmanager
def observe_api_request(method: str, path: str):
    started_at = time.perf_counter()
    status = "500"

    def set_status(code: int) -> None:
        nonlocal status
        status = str(code)

    try:
        yield set_status
        if status == "500":
            status = "200"
    except Exception:
        api_requests_total.labels(method=method, path=path, status=status).inc()
        api_request_latency_seconds.labels(method=method, path=path).observe(
            time.perf_counter() - started_at
        )
        raise
    else:
        api_requests_total.labels(method=method, path=path, status=status).inc()
        api_request_latency_seconds.labels(method=method, path=path).observe(
            time.perf_counter() - started_at
        )
