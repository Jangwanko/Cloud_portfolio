from contextlib import asynccontextmanager
import time

from fastapi import FastAPI, Request

from portfolio.api import router as api_router
from portfolio.config import settings
from portfolio.db import close_pool, init_pool_with_retry, ping_db, run_alembic_migrations
from portfolio.metrics import api_request_latency_seconds, api_requests_total, metrics_response
from portfolio.queues import ingress_partition_queues
from portfolio.redis_client import init_redis_with_retry, ping_redis, update_queue_depth


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
        "project": "messaging-portfolio",
        "docs": "/docs",
        "health": "/health/ready",
        "metrics": "/metrics",
    }


@app.get("/metrics")
def metrics():
    if ping_redis():
        for queue in ingress_partition_queues():
            update_queue_depth(queue)
        update_queue_depth(settings.ingress_dlq)
        update_queue_depth(settings.notification_queue)
    return metrics_response()


@app.get("/health/live")
def health_live():
    return {"status": "live"}


@app.get("/health/ready")
def health_ready():
    db_ok = ping_db()
    redis_ok = ping_redis()
    if redis_ok:
        for queue in ingress_partition_queues():
            update_queue_depth(queue)
        update_queue_depth(settings.ingress_dlq)
        update_queue_depth(settings.notification_queue)

    if not db_ok:
        return {"status": "not_ready", "db": "down", "redis": "unknown"}
    if not redis_ok:
        return {"status": "not_ready", "db": "up", "redis": "down"}
    return {"status": "ready", "db": "up", "redis": "up"}
