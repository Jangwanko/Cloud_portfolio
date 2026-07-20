from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import threading
import time
from urllib.parse import urlencode
from urllib.request import urlopen

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg2 import InterfaceError, OperationalError
from psycopg2.pool import PoolError

from portfolio.api import generic_router, router as api_router
from portfolio.auth import is_unsafe_auth_secret
from portfolio.config import settings
from portfolio.db import close_pool, get_postgres_runtime_status, init_pool_with_retry, run_alembic_migrations
from portfolio.kafka_client import ping_kafka
from portfolio.materialized_cache import (
    get_materialized_cache_status,
    start_materialized_cache,
    stop_materialized_cache,
)
from portfolio.metrics import api_request_latency_seconds, api_requests_total, metrics_response
from portfolio.schemas import LiveHealthResponse, ReadinessResponse, RootResponse

_degraded_started_at: float | None = None
_db_startup_ready = False
_db_startup_stop = threading.Event()
_db_startup_thread: threading.Thread | None = None


class RequestBodyLimitMiddleware:
    def __init__(self, app, max_body_bytes: int):
        if type(max_body_bytes) is not int or max_body_bytes < 1:
            raise ValueError("max_body_bytes must be a positive integer")
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        for name, raw_value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                if int(raw_value.decode("ascii")) > self.max_body_bytes:
                    await JSONResponse(
                        status_code=413,
                        content={"detail": "Request body exceeds the configured limit"},
                    )(scope, receive, send)
                    return
            except (UnicodeDecodeError, ValueError):
                break

        body = bytearray()
        disconnected = False
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                disconnected = True
                break
            if message.get("type") != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self.max_body_bytes:
                await JSONResponse(
                    status_code=413,
                    content={"detail": "Request body exceeds the configured limit"},
                )(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                if disconnected:
                    return {"type": "http.disconnect"}
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)


def _initialize_db_startup() -> None:
    global _db_startup_ready

    init_pool_with_retry(settings.startup_retries, settings.startup_retry_delay)
    run_alembic_migrations()
    _db_startup_ready = True


def _retry_db_startup_until_ready() -> None:
    while not _db_startup_stop.is_set() and not _db_startup_ready:
        try:
            _initialize_db_startup()
        except Exception as exc:  # noqa: BLE001
            logging.warning("PostgreSQL startup retry failed: %s", exc)
            _db_startup_stop.wait(settings.startup_retry_delay)


def _start_db_startup_retry() -> None:
    global _db_startup_thread

    if _db_startup_thread is not None and _db_startup_thread.is_alive():
        return
    _db_startup_thread = threading.Thread(
        target=_retry_db_startup_until_ready,
        name="postgres-startup-retry",
        daemon=True,
    )
    _db_startup_thread.start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_startup_ready

    _db_startup_ready = False
    _db_startup_stop.clear()
    try:
        _initialize_db_startup()
    except Exception as exc:  # noqa: BLE001
        logging.warning("API started without PostgreSQL startup readiness: %s", exc)
        _start_db_startup_retry()
    start_materialized_cache()
    yield
    _db_startup_stop.set()
    stop_materialized_cache()
    close_pool()


app = FastAPI(title=settings.app_name, version="2.0.0", lifespan=lifespan)
app.include_router(api_router)
app.include_router(generic_router)
app.mount(
    "/demo",
    StaticFiles(directory=Path(__file__).resolve().parents[1] / "demo", html=True),
    name="demo",
)


async def database_unavailable_handler(_request: Request, exc: Exception):
    logging.warning("Database dependency unavailable: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


app.add_exception_handler(OperationalError, database_unavailable_handler)
app.add_exception_handler(InterfaceError, database_unavailable_handler)
app.add_exception_handler(PoolError, database_unavailable_handler)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_bytes=settings.request_body_max_bytes,
)


@app.middleware("http")
async def collect_http_metrics(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    started_at = time.perf_counter()
    status_code = 500
    try:
        if request.url.path.startswith(("/v1/", "/v2/")) and not _db_startup_ready:
            status_code = 503
            return JSONResponse(
                status_code=status_code,
                content={"detail": "Database schema startup is not complete"},
            )
        if (
            request.url.path.startswith(("/v1/", "/v2/"))
            and settings.app_env not in {"local", "development", "dev", "test"}
            and is_unsafe_auth_secret()
        ):
            status_code = 503
            return JSONResponse(
                status_code=status_code,
                content={"detail": "Authentication secret is not safely configured"},
            )
        response = await call_next(request)
        status_code = response.status_code
        return response
    except RecursionError:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type == "application/json":
            status_code = 422
            logging.warning("Rejected JSON request that exceeded the parser nesting limit")
            return JSONResponse(
                status_code=status_code,
                content={"detail": "JSON nesting is too deep"},
            )
        raise
    except Exception:
        raise
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", None) or "__unmatched__"
        api_requests_total.labels(
            method=request.method,
            path=path,
            status=str(status_code),
        ).inc()
        api_request_latency_seconds.labels(
            method=request.method,
            path=path,
        ).observe(time.perf_counter() - started_at)


@app.get("/", response_model=RootResponse)
def root():
    return {
        "project": "reliable-event-processing-system",
        "docs": "/docs",
        "health": "/health/ready",
        "metrics": "/metrics",
    }


@app.get("/metrics")
def metrics():
    return metrics_response()


@app.get("/health/live", response_model=LiveHealthResponse)
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


def _prometheus_query_value(query: str) -> int | None:
    url = f"{settings.prometheus_base_url.rstrip('/')}/api/v1/query?{urlencode({'query': query})}"
    with urlopen(url, timeout=2) as response:  # noqa: S310 - URL is local cluster configuration.
        payload = json.loads(response.read().decode("utf-8"))

    results = payload.get("data", {}).get("result", [])
    if not results:
        return None
    value = results[0].get("value", [None, None])[1]
    if value is None:
        return None
    return int(float(value))


def _worker_runtime_status() -> dict:
    namespace = settings.k8s_namespace
    deployment = settings.worker_deployment_name
    hpa = settings.worker_hpa_name
    try:
        desired = _prometheus_query_value(
            f'kube_deployment_spec_replicas{{namespace="{namespace}",deployment="{deployment}"}}'
        )
        available = _prometheus_query_value(
            f'kube_deployment_status_replicas_available{{namespace="{namespace}",deployment="{deployment}"}}'
        )
        hpa_desired = _prometheus_query_value(
            f'kube_horizontalpodautoscaler_status_desired_replicas{{namespace="{namespace}",horizontalpodautoscaler="{hpa}"}}'
        )
        hpa_max = _prometheus_query_value(
            f'kube_horizontalpodautoscaler_spec_max_replicas{{namespace="{namespace}",horizontalpodautoscaler="{hpa}"}}'
        )
        return {
            "deployment": deployment,
            "desired_replicas": desired,
            "available_replicas": available,
            "hpa_desired_replicas": hpa_desired,
            "max_replicas": hpa_max,
            "source": "prometheus",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "deployment": deployment,
            "desired_replicas": None,
            "available_replicas": None,
            "hpa_desired_replicas": None,
            "max_replicas": None,
            "source": "unavailable",
            "error": type(exc).__name__,
        }


def _build_readiness_payload() -> tuple[int, dict]:
    postgres_status = get_postgres_runtime_status()
    kafka_reachable = ping_kafka()
    worker_status = _worker_runtime_status()
    cache_status = get_materialized_cache_status()
    reasons: list[str] = []
    status_code = 200
    overall_status = "ready"

    if not _db_startup_ready:
        reasons.append("schema_not_ready")
    if not kafka_reachable:
        reasons.append("kafka_unreachable")
    if not postgres_status["write_available"]:
        reasons.append("postgres_primary_unreachable")
    if postgres_status["ha_mode"]:
        if postgres_status["standby_count"] < settings.postgres_min_ready_standbys:
            reasons.append("postgres_ready_standbys_below_minimum")
        if postgres_status["sync_standby_count"] < settings.postgres_min_sync_standbys:
            reasons.append("postgres_sync_standbys_below_minimum")
        if (
            postgres_status["max_replication_delay_bytes"]
            > settings.postgres_replication_delay_degraded_bytes
        ):
            reasons.append("postgres_replication_delay_high")
    if is_unsafe_auth_secret() and settings.app_env not in {"local", "development", "dev", "test"}:
        reasons.append("unsafe_auth_secret")

    hard_failures = {"schema_not_ready", "kafka_unreachable", "unsafe_auth_secret"}
    if hard_failures.intersection(reasons):
        overall_status = "not_ready"
        status_code = 503
    elif reasons:
        overall_status = "degraded"
        status_code = 200

    grace_remaining_seconds = _degraded_grace_remaining(overall_status)
    payload = {
        "app_version": app.version,
        "status": overall_status,
        "reason": reasons,
        "grace_remaining_seconds": grace_remaining_seconds,
        "queue_backend": "kafka",
        "kafka": {
            "bootstrap_reachable": kafka_reachable,
        },
        "postgres": {
            "ha_mode": postgres_status["ha_mode"],
            "primary_reachable": postgres_status["primary_reachable"],
            "standby_count": postgres_status["standby_count"],
            "sync_standby_count": postgres_status["sync_standby_count"],
            "max_replication_delay_bytes": postgres_status["max_replication_delay_bytes"],
        },
        "materialized_cache": {
            "ready": bool(cache_status["ready"]),
            "hydrated": bool(cache_status["hydrated"]),
            "last_error": cache_status["last_error"],
        },
        "worker": worker_status,
    }
    return status_code, payload


@app.get("/health/ready", response_model=ReadinessResponse)
def health_ready():
    status_code, payload = _build_readiness_payload()
    if status_code >= 400:
        return JSONResponse(status_code=status_code, content=payload)
    return payload
