import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Event Stream Portfolio API")
    app_env: str = os.getenv("APP_ENV", "local")
    app_port: int = int(os.getenv("APP_PORT", "8000"))

    db_host: str = os.getenv("DB_HOST", "db")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "portfolio")
    db_user: str = os.getenv("DB_USER", "portfolio")
    db_password: str = os.getenv("DB_PASSWORD", "portfolio")
    db_pool_minconn: int = int(os.getenv("DB_POOL_MIN_CONN", "1"))
    db_pool_maxconn: int = int(os.getenv("DB_POOL_MAX_CONN", "20"))
    db_connect_timeout: int = int(os.getenv("DB_CONNECT_TIMEOUT", "3"))

    startup_retries: int = int(os.getenv("STARTUP_RETRIES", "30"))
    startup_retry_delay: float = float(os.getenv("STARTUP_RETRY_DELAY", "2"))
    readiness_degraded_grace_seconds: int = int(
        os.getenv("READINESS_DEGRADED_GRACE_SECONDS", "30")
    )

    ingress_max_retries: int = int(os.getenv("INGRESS_MAX_RETRIES", "3"))
    ingress_retry_base_delay_seconds: float = float(
        os.getenv("INGRESS_RETRY_BASE_DELAY_SECONDS", "2")
    )
    dlq_replay_enabled: bool = os.getenv("DLQ_REPLAY_ENABLED", "true").lower() == "true"
    dlq_replay_interval_seconds: float = float(os.getenv("DLQ_REPLAY_INTERVAL_SECONDS", "0.2"))
    dlq_replay_batch_size: int = int(os.getenv("DLQ_REPLAY_BATCH_SIZE", "5"))
    dlq_replay_max_count: int = int(os.getenv("DLQ_REPLAY_MAX_COUNT", "3"))
    observer_port: int = int(os.getenv("OBSERVER_PORT", "8081"))
    worker_metrics_port: int = int(os.getenv("WORKER_METRICS_PORT", "9101"))
    postgres_min_ready_standbys: int = int(os.getenv("POSTGRES_MIN_READY_STANDBYS", "2"))
    postgres_min_sync_standbys: int = int(os.getenv("POSTGRES_MIN_SYNC_STANDBYS", "0"))
    postgres_replication_delay_degraded_bytes: int = int(
        os.getenv("POSTGRES_REPLICATION_DELAY_DEGRADED_BYTES", "1048576")
    )
    kafka_bootstrap_servers: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    kafka_ingress_topic: str = os.getenv("KAFKA_INGRESS_TOPIC", "message-ingress")
    kafka_dlq_topic: str = os.getenv("KAFKA_DLQ_TOPIC", "message-ingress-dlq")
    kafka_consumer_group: str = os.getenv("KAFKA_CONSUMER_GROUP", "message-worker")
    membership_cache_ttl_seconds: int = int(os.getenv("MEMBERSHIP_CACHE_TTL_SECONDS", "300"))


settings = Settings()
