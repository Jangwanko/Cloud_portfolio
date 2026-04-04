from contextlib import asynccontextmanager

from fastapi import FastAPI

from portfolio.api import router as api_router
from portfolio.config import settings
from portfolio.db import close_pool, init_pool_with_retry, ping_db, run_schema_migrations
from portfolio.redis_client import init_redis_with_retry, ping_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pool_with_retry(settings.startup_retries, settings.startup_retry_delay)
    init_redis_with_retry(settings.startup_retries, settings.startup_retry_delay)
    run_schema_migrations()
    yield
    close_pool()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.include_router(api_router)


@app.get("/")
def root():
    return {
        "project": "messaging-portfolio",
        "docs": "/docs",
        "health": "/health/ready",
    }


@app.get("/health/live")
def health_live():
    return {"status": "live"}


@app.get("/health/ready")
def health_ready():
    if not ping_db():
        return {"status": "not_ready", "db": "down", "redis": "unknown"}
    if not ping_redis():
        return {"status": "not_ready", "db": "up", "redis": "down"}
    return {"status": "ready", "db": "up", "redis": "up"}
