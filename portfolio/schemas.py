from typing import Any

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=30)
    password: str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: int
    username: str


class StreamCreate(BaseModel):
    name: str = Field(min_length=2, max_length=50)
    member_ids: list[int] = Field(default_factory=list)


class StreamResponse(BaseModel):
    id: int
    name: str
    member_ids: list[int]


class EventCreate(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


class EventResponse(BaseModel):
    id: int
    request_id: str | None = None
    stream_id: int
    stream_seq: int | None = None
    user_id: int
    body: str
    created_at: str


class EventListResponse(BaseModel):
    source: str
    degraded: bool
    snapshot_age_seconds: float | None = None
    items: list[EventResponse]


class EventAcceptedResponse(BaseModel):
    request_id: str
    status: str
    persistence: str
    stream_id: int
    user_id: int
    body: str
    queued_at: str


class EventRequestStatusResponse(BaseModel):
    request_id: str
    status: str
    stream_id: int | None = None
    user_id: int | None = None
    body: str | None = None
    persistence: str | None = None
    queued_at: str | None = None
    event_id: int | None = None
    stream_seq: int | None = None
    created_at: str | None = None
    persisted_at: str | None = None
    failed_reason: str | None = None


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
    username: str = Field(min_length=2, max_length=30)
    password: str = Field(min_length=8, max_length=128)


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
    count: int
    max_replay_count: int
    items: list[DlqItemResponse]


class DlqStreamSummary(BaseModel):
    stream_id: int
    count: int


class DlqSummaryResponse(BaseModel):
    queue_backend: str
    topic: str
    limit: int
    sample_limit: int
    max_replay_count: int
    total: int
    replayable: int
    blocked: int
    oldest_age_seconds: int | None
    by_reason: dict[str, int]
    by_stream: list[DlqStreamSummary]
    recent_samples: list[DlqItemResponse]


class KafkaHealthResponse(BaseModel):
    bootstrap_reachable: bool


class PostgresHealthResponse(BaseModel):
    primary_reachable: bool
    standby_count: int
    sync_standby_count: int


class ReadinessResponse(BaseModel):
    status: str
    reason: list[str]
    grace_remaining_seconds: int | None
    queue_backend: str
    kafka: KafkaHealthResponse
    postgres: PostgresHealthResponse


class LiveHealthResponse(BaseModel):
    status: str


class RootResponse(BaseModel):
    project: str
    docs: str
    health: str
    metrics: str
