from contextlib import asynccontextmanager
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from portfolio.api import router as api_router
from portfolio.config import settings
from portfolio.db import close_pool, get_postgres_runtime_status, init_pool_with_retry, run_alembic_migrations
from portfolio.metrics import api_request_latency_seconds, api_requests_total, metrics_response
from portfolio.queues import ingress_partition_queues
from portfolio.redis_client import get_redis_runtime_status, init_redis_with_retry, update_queue_depth

_degraded_started_at: float | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pool_with_retry(settings.startup_retries, settings.startup_retry_delay)
    init_redis_with_retry(settings.startup_retries, settings.startup_retry_delay)
    run_alembic_migrations()
    yield
    close_pool()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.include_router(api_router)


@app.middleware("http")
async def collect_http_metrics(request: Request, call_next):
    path = request.url.path
    if path == "/metrics":
        return await call_next(request)

    started_at = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception:
        raise
    finally:
        api_requests_total.labels(
            method=request.method,
            path=path,
            status=str(status_code),
        ).inc()
        api_request_latency_seconds.labels(
            method=request.method,
            path=path,
        ).observe(time.perf_counter() - started_at)


@app.get("/")
def root():
    return {
        "project": "event-stream-portfolio",
        "docs": "/docs",
        "health": "/health/ready",
        "metrics": "/metrics",
    }


@app.get("/metrics")
def metrics():
    redis_status = get_redis_runtime_status()
    get_postgres_runtime_status()
    if redis_status["master_reachable"]:
        for queue in ingress_partition_queues():
            update_queue_depth(queue)
        update_queue_depth(settings.ingress_dlq)
        update_queue_depth(settings.notification_queue)
    return metrics_response()


@app.get("/health/live")
def health_live():
    return {"status": "live"}


def _degraded_grace_remaining(status: str) -> int | None:
    global _degraded_started_at

    if status != "degraded":
        _degraded_started_at = None
        return None

    now = time.monotonic()
    if _degraded_started_at is None:
        _degraded_started_at = now
    elapsed = int(now - _degraded_started_at)
    return max(0, settings.readiness_degraded_grace_seconds - elapsed)


def _build_readiness_payload() -> tuple[int, dict]:
    redis_status = get_redis_runtime_status()
    postgres_status = get_postgres_runtime_status()

    if redis_status["master_reachable"]:
        for queue in ingress_partition_queues():
            update_queue_depth(queue)
        update_queue_depth(settings.ingress_dlq)
        update_queue_depth(settings.notification_queue)

    reasons: list[str] = []
    status_code = 200
    overall_status = "ready"

    redis_not_ready = (
        (not redis_status["master_writable"])
        or (not redis_status["sentinel_master_ok"])
    )
    if redis_not_ready:
        if not redis_status["master_writable"]:
            reasons.append("redis_master_unreachable")
        if not redis_status["sentinel_master_ok"]:
            reasons.append("redis_sentinel_master_unresolved")
        overall_status = "not_ready"
        status_code = 503
    else:
        if not postgres_status["write_available"]:
            reasons.append("postgres_primary_unreachable")
        if redis_status["replica_count"] < settings.redis_min_ready_replicas:
            reasons.append("redis_replica_count_low")
        if any(not replica["link_ok"] for replica in redis_status["replica_links"]):
            reasons.append("redis_master_link_down")
        if not redis_status["sentinel_quorum_ok"]:
            reasons.append("redis_sentinel_quorum_low")

        if postgres_status["ha_mode"]:
            if postgres_status["standby_count"] < settings.postgres_min_ready_standbys:
                reasons.append("postgres_standby_count_low")
            if postgres_status["sync_standby_count"] < settings.postgres_min_sync_standbys:
                reasons.append("postgres_sync_state_mismatch")
            if any(
                replication_state not in {"streaming"}
                for replication_state in postgres_status["state_counts"]
            ):
                reasons.append("postgres_replication_state_unstable")
            if (
                postgres_status["max_replication_delay_bytes"]
                > settings.postgres_replication_delay_degraded_bytes
            ):
                reasons.append("postgres_replication_lag_high")

        if reasons:
            overall_status = "degraded"

    grace_remaining_seconds = _degraded_grace_remaining(overall_status)
    payload = {
        "status": overall_status,
        "reason": reasons,
        "grace_remaining_seconds": grace_remaining_seconds,
        "redis": {
            "master_reachable": redis_status["master_writable"],
            "replica_count": redis_status["replica_count"],
            "sentinel_master_ok": redis_status["sentinel_master_ok"],
        },
        "postgres": {
            "primary_reachable": postgres_status["write_available"],
            "standby_count": postgres_status["standby_count"],
            "sync_standby_count": postgres_status["sync_standby_count"],
        },
    }
    return status_code, payload


@app.get("/health/ready")
def health_ready():
    status_code, payload = _build_readiness_payload()
    if status_code >= 400:
        return JSONResponse(status_code=status_code, content=payload)
    return payload
