import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Messaging Portfolio API")
    app_env: str = os.getenv("APP_ENV", "local")
    app_port: int = int(os.getenv("APP_PORT", "8000"))

    db_host: str = os.getenv("DB_HOST", "db")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "portfolio")
    db_user: str = os.getenv("DB_USER", "portfolio")
    db_password: str = os.getenv("DB_PASSWORD", "portfolio")

    redis_host: str = os.getenv("REDIS_HOST", "redis")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_sentinel_enabled: bool = os.getenv("REDIS_SENTINEL_ENABLED", "false").lower() == "true"
    redis_sentinel_master: str = os.getenv("REDIS_SENTINEL_MASTER", "mymaster")
    redis_sentinel_nodes: str = os.getenv("REDIS_SENTINEL_NODES", "")

    startup_retries: int = int(os.getenv("STARTUP_RETRIES", "30"))
    startup_retry_delay: float = float(os.getenv("STARTUP_RETRY_DELAY", "2"))

    notification_queue: str = os.getenv("NOTIFICATION_QUEUE", "message_notifications")
    ingress_queue: str = os.getenv("INGRESS_QUEUE", "message_ingress")
    observer_port: int = int(os.getenv("OBSERVER_PORT", "8081"))
    worker_metrics_port: int = int(os.getenv("WORKER_METRICS_PORT", "9101"))


settings = Settings()
