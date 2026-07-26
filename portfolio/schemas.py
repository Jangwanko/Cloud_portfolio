import json
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, model_validator

from portfolio.event_envelope import GENERIC_EVENT_TYPE_PATTERN, validate_json_structure


_MAX_POSTGRES_BIGINT = 9_223_372_036_854_775_807


def _unicode_scalar_text(value: str) -> str:
    validate_json_structure(value)
    return value


UnicodeScalarText = Annotated[str, AfterValidator(_unicode_scalar_text)]


class UserCreate(BaseModel):
    username: UnicodeScalarText = Field(min_length=2, max_length=30)
    password: UnicodeScalarText = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: int
    username: str


class StreamCreate(BaseModel):
    name: UnicodeScalarText = Field(min_length=2, max_length=50)
    member_ids: list[
        Annotated[StrictInt, Field(gt=0, le=_MAX_POSTGRES_BIGINT)]
    ] = Field(default_factory=list, max_length=100)


class StreamResponse(BaseModel):
    id: int
    name: str
    member_ids: list[int]


class EventCreate(BaseModel):
    body: UnicodeScalarText = Field(min_length=1, max_length=1000)


class GenericEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(
        min_length=1,
        max_length=50,
        pattern=GENERIC_EVENT_TYPE_PATTERN,
    )
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_envelope_size(self):
        validate_json_structure(self.payload)
        validate_json_structure(self.metadata)
        try:
            payload_bytes = len(
                json.dumps(
                    self.payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
            metadata_bytes = len(
                json.dumps(
                    self.metadata,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("payload and metadata must contain finite JSON values") from exc
        if payload_bytes > 65_536:
            raise ValueError("payload must not exceed 65536 UTF-8 JSON bytes")
        if metadata_bytes > 16_384:
            raise ValueError("metadata must not exceed 16384 UTF-8 JSON bytes")
        return self


class OrderEventCreate(BaseModel):
    event_type: UnicodeScalarText = Field(min_length=1, max_length=50)
    body: UnicodeScalarText = Field(min_length=1, max_length=1000)
    payment_id: UnicodeScalarText | None = Field(default=None, max_length=80)


class OrderEventAcceptedResponse(BaseModel):
    request_id: str
    status: str
    persistence: str
    order_id: int
    stream_id: int
    user_id: int
    event_type: str
    category: str
    body: str
    payment_id: str | None = None
    queued_at: str


class EventResponse(BaseModel):
    id: int
    request_id: str | None = None
    stream_id: int
    stream_seq: int | None = None
    user_id: int
    actor_id: int | None = None
    event_type: str | None = None
    category: str | None = None
    payment_id: str | None = None
    body: str
    schema_version: int = 1
    payload: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class EventListResponse(BaseModel):
    source: str
    degraded: bool
    snapshot_age_seconds: float | None = None
    items: list[EventResponse]


class GenericEventResponse(BaseModel):
    id: int
    request_id: str | None = None
    stream_id: int
    stream_seq: int | None = None
    actor_id: int
    event_type: str
    schema_version: int
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class GenericEventListResponse(BaseModel):
    source: str
    degraded: bool
    snapshot_age_seconds: float | None = None
    items: list[GenericEventResponse]


class EventAcceptedResponse(BaseModel):
    request_id: str
    status: str
    persistence: str
    stream_id: int
    user_id: int
    body: str
    queued_at: str


class GenericEventAcceptedResponse(BaseModel):
    request_id: str
    status: str
    persistence: str
    stream_id: int
    actor_id: int
    event_type: str
    schema_version: int
    payload: dict[str, Any]
    metadata: dict[str, Any]
    queued_at: str


class EventRequestStatusResponse(BaseModel):
    request_id: str
    status: str
    stream_id: int | None = None
    user_id: int | None = None
    actor_id: int | None = None
    body: str | None = None
    event_type: str | None = None
    payload: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    schema_version: int | None = None
    category: str | None = None
    payment_id: str | None = None
    persistence: str | None = None
    queued_at: str | None = None
    event_id: int | None = None
    stream_seq: int | None = None
    created_at: str | None = None
    persisted_at: str | None = None
    failed_reason: str | None = None
    retry_count: int | None = None
    next_retry_at: str | None = None
    failed_at: str | None = None


class GenericEventRequestStatusResponse(BaseModel):
    request_id: str
    status: str
    stream_id: int | None = None
    actor_id: int | None = None
    event_type: str | None = None
    payload: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    schema_version: int | None = None
    persistence: str | None = None
    queued_at: str | None = None
    event_id: int | None = None
    stream_seq: int | None = None
    created_at: str | None = None
    persisted_at: str | None = None
    failed_reason: str | None = None
    retry_count: int | None = None
    next_retry_at: str | None = None
    failed_at: str | None = None


class StreamPersistenceSummaryResponse(BaseModel):
    stream_id: int
    persisted_count: int
    latest_request_id: str | None = None
    latest_event_id: int | None = None
    latest_stream_seq: int | None = None
    latest_created_at: str | None = None


class ReadReceiptCreate(BaseModel):
    pass


class ReadReceiptResponse(BaseModel):
    status: str
    event_id: int
    user_id: int


class UnreadCountResponse(BaseModel):
    stream_id: int
    user_id: int
    unread: int


class LoginRequest(BaseModel):
    username: UnicodeScalarText = Field(min_length=2, max_length=30)
    password: UnicodeScalarText = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user: dict[str, Any]


class DlqItemResponse(BaseModel):
    topic: str | None = None
    partition: int | None = None
    offset: int | None = None
    timestamp: int | None = None
    key: str | None = None
    request_id: str | None = None
    stream_id: int | None = None
    user_id: int | None = None
    failed_reason: str | None = None
    retry_count: int
    replay_count: int
    replayable: bool
    max_replay_count: int
    failed_at: str | None = None
    replayed_at: str | None = None
    payload: dict[str, Any]


class DlqListResponse(BaseModel):
    queue_backend: str
    topic: str
    scope: str
    user_filtered: bool
    count: int
    max_replay_count: int
    items: list[DlqItemResponse]


class DlqStreamSummary(BaseModel):
    stream_id: int
    count: int


class DlqSummaryResponse(BaseModel):
    queue_backend: str
    topic: str
    scope: str
    user_filtered: bool
    limit: int
    sample_limit: int
    max_replay_count: int
    total: int
    replayable: int
    blocked: int
    oldest_sample_age_seconds: int | None
    by_reason: dict[str, int]
    by_stream: list[DlqStreamSummary]
    recent_samples: list[DlqItemResponse]


class DlqReplayRequest(BaseModel):
    request_id: UnicodeScalarText = Field(min_length=1, max_length=80)


class DlqReplayResponse(BaseModel):
    status: str
    request_id: str
    stream_id: int
    replay_count: int
    replayed_at: str


class DemoResetRequest(BaseModel):
    confirmation: UnicodeScalarText = Field(min_length=1, max_length=40)


class DemoResetResponse(BaseModel):
    status: str
    deleted_events: int
    deleted_messages: int
    reset_streams: int
    reset_request_statuses: int
    reset_dlq_topic: str
    cache_invalidation_failures: int
    note: str


class KafkaHealthResponse(BaseModel):
    bootstrap_reachable: bool


class PostgresHealthResponse(BaseModel):
    ha_mode: bool
    primary_reachable: bool
    standby_count: int
    sync_standby_count: int
    max_replication_delay_bytes: int


class MaterializedCacheHealthResponse(BaseModel):
    ready: bool
    hydrated: bool = False
    last_error: str | None = None


class WorkerHealthResponse(BaseModel):
    deployment: str
    desired_replicas: int | None = None
    available_replicas: int | None = None
    hpa_desired_replicas: int | None = None
    max_replicas: int | None = None
    source: str
    error: str | None = None


class ReadinessResponse(BaseModel):
    app_version: str
    status: str
    reason: list[str]
    grace_remaining_seconds: int | None
    queue_backend: str
    kafka: KafkaHealthResponse
    postgres: PostgresHealthResponse
    materialized_cache: MaterializedCacheHealthResponse
    worker: WorkerHealthResponse


class LiveHealthResponse(BaseModel):
    status: str


class RootResponse(BaseModel):
    project: str
    docs: str
    health: str
    metrics: str
