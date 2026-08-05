import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Reliable Event Processing API")
    app_env: str = os.getenv("APP_ENV", "local")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    request_body_max_bytes: int = int(os.getenv("REQUEST_BODY_MAX_BYTES", "1048576"))

    db_host: str = os.getenv("DB_HOST", "db")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "portfolio")
    db_user: str = os.getenv("DB_USER", "portfolio")
    db_password: str = os.getenv("DB_PASSWORD", "")
    db_pool_minconn: int = int(os.getenv("DB_POOL_MIN_CONN", "1"))
    db_pool_maxconn: int = int(os.getenv("DB_POOL_MAX_CONN", "20"))
    db_connect_timeout: int = int(os.getenv("DB_CONNECT_TIMEOUT", "3"))

    startup_retries: int = int(os.getenv("STARTUP_RETRIES", "30"))
    startup_retry_delay: float = float(os.getenv("STARTUP_RETRY_DELAY", "2"))
    readiness_degraded_grace_seconds: int = int(
        os.getenv("READINESS_DEGRADED_GRACE_SECONDS", "30")
    )
    demo_reset_enabled: bool = os.getenv(
        "DEMO_RESET_ENABLED",
        "true" if os.getenv("APP_ENV", "local") in {"local", "development", "dev", "test"} else "false",
    ).lower() == "true"
    generic_events_v2_enabled: bool = os.getenv(
        "GENERIC_EVENTS_V2_ENABLED",
        "true" if os.getenv("APP_ENV", "local") in {"local", "development", "dev", "test"} else "false",
    ).lower() == "true"

    ingress_max_retries: int = int(os.getenv("INGRESS_MAX_RETRIES", "3"))
    ingress_retry_base_delay_seconds: float = float(
        os.getenv("INGRESS_RETRY_BASE_DELAY_SECONDS", "2")
    )
    dlq_replay_enabled: bool = os.getenv("DLQ_REPLAY_ENABLED", "true").lower() == "true"
    dlq_replay_interval_seconds: float = float(os.getenv("DLQ_REPLAY_INTERVAL_SECONDS", "0.2"))
    dlq_replay_batch_size: int = int(os.getenv("DLQ_REPLAY_BATCH_SIZE", "5"))
    dlq_replay_max_count: int = int(os.getenv("DLQ_REPLAY_MAX_COUNT", "3"))
    worker_metrics_port: int = int(os.getenv("WORKER_METRICS_PORT", "9101"))
    dlq_replayer_metrics_port: int = int(os.getenv("DLQ_REPLAYER_METRICS_PORT", "9102"))
    postgres_min_ready_standbys: int = int(os.getenv("POSTGRES_MIN_READY_STANDBYS", "2"))
    postgres_min_sync_standbys: int = int(os.getenv("POSTGRES_MIN_SYNC_STANDBYS", "1"))
    postgres_replication_delay_degraded_bytes: int = int(
        os.getenv("POSTGRES_REPLICATION_DELAY_DEGRADED_BYTES", "1048576")
    )
    kafka_bootstrap_servers: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    kafka_topic_partitions: int = int(os.getenv("KAFKA_TOPIC_PARTITIONS", "8"))
    kafka_topic_replication_factor: int = int(os.getenv("KAFKA_TOPIC_REPLICATION_FACTOR", "3"))
    kafka_min_insync_replicas: int = int(os.getenv("KAFKA_MIN_INSYNC_REPLICAS", "2"))
    kafka_ingress_topic: str = os.getenv("KAFKA_INGRESS_TOPIC", "message-ingress")
    kafka_dlq_topic: str = os.getenv("KAFKA_DLQ_TOPIC", "message-ingress-dlq")
    kafka_notification_topic: str = os.getenv(
        "KAFKA_NOTIFICATION_TOPIC", "message-notifications"
    )
    kafka_consumer_group: str = os.getenv("KAFKA_CONSUMER_GROUP", "message-worker")
    kafka_notification_consumer_group: str = os.getenv(
        "KAFKA_NOTIFICATION_CONSUMER_GROUP", "notification-worker"
    )
    prometheus_base_url: str = os.getenv("PROMETHEUS_BASE_URL", "http://prometheus:9090/prometheus")
    k8s_namespace: str = os.getenv("K8S_NAMESPACE", "messaging-app")
    worker_deployment_name: str = os.getenv("WORKER_DEPLOYMENT_NAME", "worker")
    worker_hpa_name: str = os.getenv("WORKER_HPA_NAME", "worker-keda-hpa")
    worker_mode: str = os.getenv("WORKER_MODE", "ingress")

    def __post_init__(self) -> None:
        if not 1 <= self.app_port <= 65_535:
            raise ValueError("APP_PORT must be between 1 and 65535")
        if not 1 <= self.request_body_max_bytes <= 16_777_216:
            raise ValueError("REQUEST_BODY_MAX_BYTES must be between 1 and 16777216")
        if not 1 <= self.db_port <= 65_535:
            raise ValueError("DB_PORT must be between 1 and 65535")
        if self.db_pool_minconn < 1 or self.db_pool_maxconn < self.db_pool_minconn:
            raise ValueError("DB pool bounds must satisfy 1 <= min <= max")
        if self.db_connect_timeout < 1:
            raise ValueError("DB_CONNECT_TIMEOUT must be positive")
        if self.startup_retries < 1 or self.startup_retry_delay <= 0:
            raise ValueError("Startup retry count and delay must be positive")
        if self.readiness_degraded_grace_seconds < 0:
            raise ValueError("READINESS_DEGRADED_GRACE_SECONDS must not be negative")
        if self.ingress_max_retries < 0 or self.ingress_retry_base_delay_seconds < 0:
            raise ValueError("Ingress retry settings must not be negative")
        if self.dlq_replay_interval_seconds <= 0 or self.dlq_replay_batch_size < 1:
            raise ValueError("DLQ replay interval and batch size must be positive")
        if not 1 <= self.dlq_replay_max_count <= 9_223_372_036_854_775_807:
            raise ValueError("DLQ_REPLAY_MAX_COUNT is outside the PostgreSQL BIGINT range")
        for name, port in (
            ("WORKER_METRICS_PORT", self.worker_metrics_port),
            ("DLQ_REPLAYER_METRICS_PORT", self.dlq_replayer_metrics_port),
        ):
            if not 1 <= port <= 65_535:
                raise ValueError(f"{name} must be between 1 and 65535")
        if self.postgres_min_ready_standbys < 0 or self.postgres_min_sync_standbys < 0:
            raise ValueError("PostgreSQL standby minimums must not be negative")
        if self.postgres_replication_delay_degraded_bytes < 0:
            raise ValueError("PostgreSQL replication delay threshold must not be negative")
        if self.kafka_topic_partitions < 1 or self.kafka_topic_replication_factor < 1:
            raise ValueError("Kafka partition and replication settings must be positive")
        if not 1 <= self.kafka_min_insync_replicas <= self.kafka_topic_replication_factor:
            raise ValueError("Kafka min ISR must be within the replication factor")


settings = Settings()
