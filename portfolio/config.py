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

    redis_host: str = os.getenv("REDIS_HOST", "redis")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_password: str = os.getenv("REDIS_PASSWORD", "")
    redis_sentinel_enabled: bool = os.getenv("REDIS_SENTINEL_ENABLED", "false").lower() == "true"
    redis_sentinel_master: str = os.getenv("REDIS_SENTINEL_MASTER", "mymaster")
    redis_sentinel_nodes: str = os.getenv("REDIS_SENTINEL_NODES", "")
    redis_max_connections: int = int(os.getenv("REDIS_MAX_CONNECTIONS", "200"))
    redis_socket_connect_timeout: float = float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "1"))
    redis_socket_timeout: float = float(os.getenv("REDIS_SOCKET_TIMEOUT", "1"))
    redis_health_check_interval: int = int(os.getenv("REDIS_HEALTH_CHECK_INTERVAL", "15"))

    startup_retries: int = int(os.getenv("STARTUP_RETRIES", "30"))
    startup_retry_delay: float = float(os.getenv("STARTUP_RETRY_DELAY", "2"))

    notification_queue: str = os.getenv("NOTIFICATION_QUEUE", "message_notifications")
    ingress_queue: str = os.getenv("INGRESS_QUEUE", "message_ingress")
    ingress_partitions: int = int(os.getenv("INGRESS_PARTITIONS", "8"))
    ingress_dlq: str = os.getenv("INGRESS_DLQ", "message_ingress_dlq")
    ingress_max_retries: int = int(os.getenv("INGRESS_MAX_RETRIES", "3"))
    ingress_retry_base_delay_seconds: float = float(
        os.getenv("INGRESS_RETRY_BASE_DELAY_SECONDS", "2")
    )
    room_seq_key_prefix: str = os.getenv("ROOM_SEQ_KEY_PREFIX", "room_seq")
    dlq_replay_enabled: bool = os.getenv("DLQ_REPLAY_ENABLED", "true").lower() == "true"
    dlq_replay_interval_seconds: float = float(os.getenv("DLQ_REPLAY_INTERVAL_SECONDS", "0.2"))
    dlq_replay_batch_size: int = int(os.getenv("DLQ_REPLAY_BATCH_SIZE", "5"))
    observer_port: int = int(os.getenv("OBSERVER_PORT", "8081"))
    worker_metrics_port: int = int(os.getenv("WORKER_METRICS_PORT", "9101"))


settings = Settings()
