from collections import Counter
from datetime import datetime, timezone
import json
import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from psycopg2 import InterfaceError, OperationalError
from psycopg2.errors import UniqueViolation
from psycopg2.pool import PoolError

from portfolio.auth import authenticate_user, create_access_token, get_current_user, hash_password
from portfolio.config import settings
from portfolio.db import get_conn, get_cursor
from portfolio.event_envelope import MAX_JSON_WIRE_NESTING_DEPTH, validate_json_structure
from portfolio.kafka_client import (
    list_recent_topic_messages,
    publish_ingress_job,
    reset_topic,
    is_invalid_kafka_payload,
)
from portfolio.metrics import observe_api_stage
from portfolio.order_events import classify_order_event
from portfolio.schemas import (
    DemoResetRequest,
    DemoResetResponse,
    DlqListResponse,
    DlqReplayRequest,
    DlqReplayResponse,
    DlqSummaryResponse,
    EventCreate,
    EventAcceptedResponse,
    GenericEventListResponse,
    GenericEventRequestStatusResponse,
    GenericEventAcceptedResponse,
    GenericEventCreate,
    EventListResponse,
    EventRequestStatusResponse,
    EventResponse,
    LoginRequest,
    OrderEventAcceptedResponse,
    OrderEventCreate,
    ReadReceiptCreate,
    ReadReceiptResponse,
    StreamCreate,
    StreamPersistenceSummaryResponse,
    StreamResponse,
    TokenResponse,
    UnreadCountResponse,
    UserCreate,
    UserResponse,
)
from portfolio.state_store import (
    claim_dlq_replay,
    load_request_status,
    mark_dlq_replay_published,
    release_dlq_replay_claim,
)

router = APIRouter(prefix="/v1", tags=["events"])
generic_router = APIRouter(prefix="/v2", tags=["generic-events"])
_MAX_POSTGRES_BIGINT = 9_223_372_036_854_775_807


def _queue_unavailable_detail() -> str:
    return "Kafka unavailable"


def _load_request_status(request_id: str) -> dict | None:
    return load_request_status(request_id)


def _ensure_demo_reset_allowed() -> None:
    if not settings.demo_reset_enabled:
        raise HTTPException(status_code=403, detail="Demo reset is disabled in this environment")


def _reset_demo_event_data(cur) -> dict:
    # Freeze writers while the demo-only reset counts and removes event state.
    cur.execute(
        """
        LOCK TABLE idempotency_keys, messages, rooms, request_statuses,
                   notification_attempts, intake_idempotency_keys
        IN ACCESS EXCLUSIVE MODE
        """
    )
    cur.execute(
        """
        /*NO LOAD BALANCE*/
        SELECT
            (SELECT COUNT(*) FROM messages) AS deleted_messages,
            (SELECT COUNT(*) FROM rooms) AS reset_streams,
            (SELECT COUNT(*) FROM request_statuses) AS reset_request_statuses
        """
    )
    counts = cur.fetchone()

    cur.execute("TRUNCATE TABLE notification_attempts RESTART IDENTITY")
    cur.execute("TRUNCATE TABLE idempotency_keys")
    cur.execute("TRUNCATE TABLE intake_idempotency_keys")
    cur.execute("TRUNCATE TABLE request_statuses")
    # Preserve room ID monotonicity. An accepted ingress record that was still
    # queued at reset time must never target a newly-created room that happened
    # to reuse the old numeric ID.
    cur.execute("TRUNCATE TABLE rooms CASCADE")

    return {
        "deleted_messages": int(counts["deleted_messages"]),
        "reset_streams": int(counts["reset_streams"]),
        "reset_request_statuses": int(counts["reset_request_statuses"]),
    }


def _reset_demo_kafka_dlq() -> str:
    reset_topic(
        settings.kafka_dlq_topic,
        partitions=settings.kafka_topic_partitions,
        replication_factor=settings.kafka_topic_replication_factor,
        configs={"min.insync.replicas": str(settings.kafka_min_insync_replicas)},
    )
    return settings.kafka_dlq_topic


def _externalize_request_status(payload: dict) -> dict:
    status = dict(payload)
    if "reason" in status and "failed_reason" not in status:
        status["failed_reason"] = status.pop("reason")
    if "message_id" in status:
        status["event_id"] = status.pop("message_id")
    if "room_id" in status:
        status["stream_id"] = status.pop("room_id")
    if "room_seq" in status:
        status["stream_seq"] = status.pop("room_seq")
    if "actor_id" not in status and "user_id" in status:
        status["actor_id"] = status["user_id"]
    return status


def _request_status_actor_id(payload: dict) -> int:
    if not isinstance(payload, dict):
        raise ValueError("Request status must be an object")
    actor_id = payload.get("actor_id")
    user_id = payload.get("user_id")
    for field_name, value in (("actor_id", actor_id), ("user_id", user_id)):
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            or value > _MAX_POSTGRES_BIGINT
        ):
            raise ValueError(f"Invalid request status {field_name}")
    if actor_id is not None and user_id is not None and actor_id != user_id:
        raise ValueError("Conflicting request status actor_id/user_id")
    owner_id = actor_id if actor_id is not None else user_id
    if owner_id is None:
        raise ValueError("Request status owner is missing")
    return owner_id


def _store_request_and_queue_job(_request_id: str, _request_payload: dict, job_payload: dict) -> None:
    # The 202 response is the caller's accepted-state receipt. Durable request
    # status appears only after the Worker observes this ingress record, so a
    # status poll may briefly return 404 while consumer lag exists.
    with observe_api_stage("kafka_publish"):
        publish_ingress_job(job_payload["room_id"], job_payload)


def _legacy_body_preview(payload: dict) -> str:
    """Keep the pre-envelope storage column readable during the compatibility window."""
    for key in ("message", "text", "description"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value[:1000]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:1000] or "{}"


def _safe_int(value, *, default: int = 0, minimum: int | None = None) -> int:
    if type(value) is not int:
        return default
    parsed = value
    if minimum is not None and parsed < minimum:
        return default
    if parsed > _MAX_POSTGRES_BIGINT:
        return default
    return parsed


def _safe_optional_positive_int(value) -> int | None:
    parsed = _safe_int(value, default=0, minimum=1)
    return parsed if 0 < parsed <= _MAX_POSTGRES_BIGINT else None


def _safe_optional_nonnegative_int(value) -> int | None:
    if type(value) is not int:
        return None
    return value if 0 <= value <= _MAX_POSTGRES_BIGINT else None


def _safe_optional_text(value, *, max_length: int = 500) -> str | None:
    if value is None:
        return None
    try:
        text = value if isinstance(value, str) else repr(value)
    except Exception:  # noqa: BLE001
        return None
    text = text[:max_length]
    try:
        validate_json_structure(text)
    except (TypeError, ValueError, RecursionError):
        return None
    return text


def _selected_event_field(payload: dict, canonical: str, legacy: str):
    return payload[canonical] if canonical in payload else payload.get(legacy)


def _has_conflicting_event_aliases(payload: dict) -> bool:
    for canonical, legacy in (
        ("stream_id", "room_id"),
        ("actor_id", "user_id"),
        ("stream_seq", "room_seq"),
    ):
        if canonical not in payload or legacy not in payload:
            continue
        canonical_value = payload[canonical]
        legacy_value = payload[legacy]
        if type(canonical_value) is not type(legacy_value) or canonical_value != legacy_value:
            return True
    return False


def _summarize_dlq_item(item: dict) -> dict:
    raw_value = item.get("value")
    if isinstance(raw_value, dict):
        value = dict(raw_value)
    else:
        value = {
            "__invalid_kafka_payload__": True,
            "raw_preview": _safe_optional_text(raw_value) or "unrepresentable payload",
        }
    structure_invalid = False
    try:
        validate_json_structure(value, max_depth=MAX_JSON_WIRE_NESTING_DEPTH)
    except (TypeError, ValueError, RecursionError):
        structure_invalid = True
        value = {
            "__invalid_kafka_payload__": True,
            "raw_preview": _safe_optional_text(raw_value) or "unrepresentable payload",
        }
    request_id = value.get("request_id")
    if (
        not isinstance(request_id, str)
        or not request_id
        or len(request_id) > 80
        or "\x00" in request_id
    ):
        request_id = None
    stream_raw = _selected_event_field(value, "stream_id", "room_id")
    user_raw = _selected_event_field(value, "actor_id", "user_id")
    stream_id = _safe_optional_positive_int(stream_raw)
    user_id = _safe_optional_positive_int(user_raw)
    retry_raw = value.get("retry_count", 0)
    replay_raw = value.get("replay_count", 0)
    retry_count = _safe_int(retry_raw, default=0, minimum=0)
    replay_count = _safe_int(replay_raw, default=0, minimum=0)
    invalid_counters = (
        type(retry_raw) is not int
        or not 0 <= retry_raw <= _MAX_POSTGRES_BIGINT
        or type(replay_raw) is not int
        or not 0 <= replay_raw <= _MAX_POSTGRES_BIGINT
    )
    invalid_payload = (
        structure_invalid
        or is_invalid_kafka_payload(value)
        or _has_conflicting_event_aliases(value)
        or request_id is None
        or stream_id is None
        or user_id is None
        or invalid_counters
    )
    return {
        "topic": _safe_optional_text(item.get("topic"), max_length=200),
        "partition": _safe_optional_nonnegative_int(item.get("partition")),
        "offset": _safe_optional_nonnegative_int(item.get("offset")),
        "timestamp": _safe_optional_nonnegative_int(item.get("timestamp")),
        "key": _safe_optional_text(item.get("key"), max_length=500),
        "request_id": request_id,
        "stream_id": stream_id,
        "user_id": user_id,
        "failed_reason": _safe_optional_text(value.get("failed_reason"))
        or ("invalid_dlq_payload" if invalid_payload else None),
        "retry_count": retry_count,
        "replay_count": replay_count,
        "replayable": not invalid_payload and replay_count < settings.dlq_replay_max_count,
        "max_replay_count": settings.dlq_replay_max_count,
        "failed_at": _safe_optional_text(value.get("failed_at"), max_length=100),
        "replayed_at": _safe_optional_text(value.get("replayed_at"), max_length=100),
        "payload": value,
    }


def _parse_dlq_timestamp_seconds(item: dict) -> float | None:
    failed_at = item.get("failed_at")
    if failed_at:
        try:
            normalized = str(failed_at).replace("Z", "+00:00")
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            pass

    timestamp_ms = item.get("timestamp")
    if timestamp_ms is None:
        return None
    try:
        return float(timestamp_ms) / 1000
    except (TypeError, ValueError):
        return None


def _summarize_dlq_items(items: list[dict], now: datetime | None = None, sample_limit: int = 5) -> dict:
    now_ts = (now or datetime.now(timezone.utc)).timestamp()
    reasons: Counter[str] = Counter()
    streams: Counter[int] = Counter()
    oldest_sample_age_seconds: int | None = None
    replayable_count = 0
    blocked_count = 0

    for item in items:
        reason = item.get("failed_reason") or "unknown"
        reasons[str(reason)] += 1

        stream_id = item.get("stream_id")
        normalized_stream_id = _safe_optional_positive_int(stream_id)
        if normalized_stream_id is not None:
            streams[normalized_stream_id] += 1

        if item.get("replayable"):
            replayable_count += 1
        else:
            blocked_count += 1

        event_ts = _parse_dlq_timestamp_seconds(item)
        if event_ts is not None:
            age_seconds = max(0, int(now_ts - event_ts))
            if oldest_sample_age_seconds is None or age_seconds > oldest_sample_age_seconds:
                oldest_sample_age_seconds = age_seconds

    by_stream = [
        {"stream_id": stream_id, "count": count}
        for stream_id, count in sorted(streams.items(), key=lambda entry: (-entry[1], entry[0]))
    ]

    return {
        "total": len(items),
        "replayable": replayable_count,
        "blocked": blocked_count,
        "oldest_sample_age_seconds": oldest_sample_age_seconds,
        "by_reason": dict(sorted(reasons.items())),
        "by_stream": by_stream,
        "recent_samples": items[:sample_limit],
    }


def _list_recent_dlq_messages(limit: int) -> list[dict]:
    try:
        return list_recent_topic_messages(settings.kafka_dlq_topic, limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="Kafka DLQ unavailable") from exc


def _find_dlq_item_by_request_id(request_id: str, user_id: int) -> dict | None:
    for raw_item in _list_recent_dlq_messages(5000):
        item = _summarize_dlq_item(raw_item)
        if item.get("request_id") == request_id and item.get("user_id") == user_id:
            return item
    return None


def _replay_dlq_payload(item: dict) -> dict:
    if not item.get("replayable"):
        raise HTTPException(status_code=409, detail="DLQ event is blocked by replay guard")

    payload = dict(item.get("payload") or {})
    stream_id = _safe_optional_positive_int(
        _selected_event_field(payload, "stream_id", "room_id")
    )
    if stream_id is None:
        raise HTTPException(status_code=409, detail="DLQ event is missing stream id")

    current_replay_count = payload.get("replay_count", 0)
    if (
        type(current_replay_count) is not int
        or current_replay_count < 0
        or current_replay_count >= min(settings.dlq_replay_max_count, _MAX_POSTGRES_BIGINT)
    ):
        raise HTTPException(status_code=409, detail="DLQ event is blocked by replay guard")
    replay_count = current_replay_count + 1
    replayed_at = datetime.now(timezone.utc).isoformat()
    payload.update(
        {
            "replay_count": replay_count,
            "replayed_at": replayed_at,
            "retry_count": 0,
            "next_retry_at": None,
        }
    )
    try:
        publish_ingress_job(stream_id, payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=_queue_unavailable_detail()) from exc
    return {
        "status": "replay_requested",
        "request_id": str(payload["request_id"]),
        "stream_id": stream_id,
        "replay_count": replay_count,
        "replayed_at": replayed_at,
    }


def _claim_manual_replay(item: dict) -> tuple[int, str]:
    if item.get("replayable") is not True:
        raise HTTPException(status_code=409, detail="DLQ event is blocked by replay guard")
    request_id = str(item["request_id"])
    replay_generation = item.get("replay_count", 0)
    if (
        type(replay_generation) is not int
        or replay_generation < 0
        or replay_generation >= min(settings.dlq_replay_max_count, _MAX_POSTGRES_BIGINT)
    ):
        raise HTTPException(status_code=409, detail="DLQ event is blocked by replay guard")
    claim_state, owner_token = claim_dlq_replay(request_id, replay_generation)
    if claim_state == "persisted":
        raise HTTPException(status_code=409, detail="DLQ event is already persisted")
    if claim_state == "published":
        raise HTTPException(status_code=409, detail="DLQ replay was already requested")
    if claim_state != "claimed" or owner_token is None:
        raise HTTPException(status_code=409, detail="DLQ replay is already in progress")
    return replay_generation, owner_token


def _ensure_room_member(cur, room_id: int, user_id: int) -> None:
    cur.execute(
        """
        /*NO LOAD BALANCE*/
        SELECT 1
        FROM rooms r
        JOIN room_members rm ON rm.room_id = r.id
        WHERE r.id=%s AND rm.user_id=%s
        """,
        (room_id, user_id),
    )
    if cur.fetchone() is None:
        # Missing streams and non-members share the same external response so
        # an authenticated caller cannot use this endpoint as an existence oracle.
        raise HTTPException(status_code=404, detail="Stream not found")


def _message_room_id_for_member(cur, message_id: int, user_id: int) -> int:
    cur.execute(
        """
        /*NO LOAD BALANCE*/
        SELECT m.room_id
        FROM messages m
        JOIN room_members rm ON rm.room_id = m.room_id
        WHERE m.id=%s AND rm.user_id=%s
        """,
        (message_id, user_id),
    )
    row = cur.fetchone()
    if row is None:
        # Missing events and events outside the caller's streams share one response.
        raise HTTPException(status_code=404, detail="Event not found")
    return int(row["room_id"])


@router.post("/users", response_model=UserResponse)
def create_user(payload: UserCreate):
    try:
        with get_conn() as conn:
            with get_cursor(conn) as cur:
                cur.execute(
                    """
                    INSERT INTO users (username, password_hash)
                    VALUES (%s, %s)
                    RETURNING id, username
                    """,
                    (payload.username, hash_password(payload.password)),
                )
                row = cur.fetchone()
            conn.commit()
        return row
    except UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="Username already exists") from exc
    except (OperationalError, InterfaceError, PoolError) as exc:
        raise HTTPException(status_code=503, detail="User store unavailable") from exc


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest):
    try:
        user = authenticate_user(payload.username, payload.password)
    except (OperationalError, InterfaceError, PoolError) as exc:
        raise HTTPException(status_code=503, detail="Authentication store unavailable") from exc
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    access_token = create_access_token(user["id"], user["username"])
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user,
    }


@router.post("/admin/demo/reset-events", response_model=DemoResetResponse)
def reset_demo_events(payload: DemoResetRequest, current_user: dict = Depends(get_current_user)):
    _ensure_demo_reset_allowed()
    if payload.confirmation != "RESET DEMO DB":
        raise HTTPException(status_code=400, detail="Confirmation must be RESET DEMO DB")

    try:
        with get_conn() as conn:
            with get_cursor(conn) as cur:
                try:
                    result = _reset_demo_event_data(cur)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
    except (OperationalError, InterfaceError, PoolError) as exc:
        raise HTTPException(status_code=503, detail="Demo event store unavailable") from exc
    try:
        reset_dlq_topic = _reset_demo_kafka_dlq()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="Demo DB reset completed, but Kafka DLQ reset failed") from exc

    return {
        "status": "reset",
        "deleted_events": result["deleted_messages"],
        "deleted_messages": result["deleted_messages"],
        "reset_streams": result["reset_streams"],
        "reset_request_statuses": result["reset_request_statuses"],
        "reset_dlq_topic": reset_dlq_topic,
        "note": (
            f"Demo event data and DLQ topic reset by user_id={current_user['id']}. "
            "Users were kept."
        ),
    }


@router.post("/streams", response_model=StreamResponse)
def create_stream(payload: StreamCreate, current_user: dict = Depends(get_current_user)):
    try:
        with get_conn() as conn:
            with get_cursor(conn) as cur:
                cur.execute(
                    "INSERT INTO rooms (name) VALUES (%s) RETURNING id, name",
                    (payload.name,),
                )
                room = cur.fetchone()

                requested_member_ids = set(payload.member_ids)
                requested_member_ids.add(int(current_user["id"]))
                candidate_member_ids = sorted(requested_member_ids)
                cur.execute(
                    "/*NO LOAD BALANCE*/ SELECT id FROM users WHERE id = ANY(%s)",
                    (candidate_member_ids,),
                )
                valid_member_ids = sorted(int(row["id"]) for row in cur.fetchall())
                missing_member_ids = sorted(set(candidate_member_ids) - set(valid_member_ids))
                if missing_member_ids:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unknown member_ids: {missing_member_ids}",
                    )
                cur.execute(
                    """
                    INSERT INTO room_members (room_id, user_id)
                    SELECT %s, member_id
                    FROM unnest(%s::bigint[]) AS member_id
                    ON CONFLICT DO NOTHING
                    """,
                    (room["id"], valid_member_ids),
                )

                conn.commit()
    except (OperationalError, InterfaceError, PoolError) as exc:
        raise HTTPException(status_code=503, detail="Stream store unavailable") from exc

    return {
        "id": room["id"],
        "name": room["name"],
        "member_ids": valid_member_ids,
    }


@router.post(
    "/streams/{stream_id}/events",
    response_model=EventAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_event(
    stream_id: Annotated[int, Path(ge=1, le=_MAX_POSTGRES_BIGINT)],
    payload: EventCreate,
    x_idempotency_key: str | None = Header(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[^\x00]+$",
    ),
    current_user: dict = Depends(get_current_user),
):
    actor_user_id = int(current_user["id"])

    route = f"POST:/v1/streams/{stream_id}/events"
    request_id = str(uuid4())

    queued_at = datetime.now(timezone.utc).isoformat()
    accepted_response = {
        "request_id": request_id,
        "status": "accepted",
        "persistence": "queued",
        "stream_id": stream_id,
        "user_id": actor_user_id,
        "body": payload.body,
        "queued_at": queued_at,
    }
    try:
        _store_request_and_queue_job(
            request_id,
            accepted_response,
            {
                "request_id": request_id,
                "route": route,
                "room_id": stream_id,
                "user_id": actor_user_id,
                "body": payload.body,
                "room_seq": None,
                "x_idempotency_key": x_idempotency_key,
                "queued_at": queued_at,
                "retry_count": 0,
                "next_retry_at": None,
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=_queue_unavailable_detail()) from exc
    return accepted_response


@generic_router.post(
    "/streams/{stream_id}/events",
    response_model=GenericEventAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_generic_event(
    stream_id: Annotated[int, Path(ge=1, le=_MAX_POSTGRES_BIGINT)],
    payload: GenericEventCreate,
    x_idempotency_key: str | None = Header(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[^\x00]+$",
    ),
    current_user: dict = Depends(get_current_user),
):
    if not settings.generic_events_v2_enabled:
        raise HTTPException(status_code=503, detail="Generic event v2 intake is not enabled")
    actor_id = int(current_user["id"])
    request_id = str(uuid4())
    queued_at = datetime.now(timezone.utc).isoformat()
    route = f"POST:/v2/streams/{stream_id}/events"
    accepted_response = {
        "request_id": request_id,
        "status": "accepted",
        "persistence": "queued",
        "stream_id": stream_id,
        "actor_id": actor_id,
        "event_type": payload.event_type,
        "schema_version": 2,
        "payload": payload.payload,
        "metadata": payload.metadata,
        "queued_at": queued_at,
    }
    try:
        _store_request_and_queue_job(
            request_id,
            accepted_response,
            {
                **accepted_response,
                # Stable aliases keep the current Worker, DLQ and compacted
                # topic state compatible throughout the expand-contract rollout.
                "route": route,
                "room_id": stream_id,
                "user_id": actor_id,
                "body": _legacy_body_preview(payload.payload),
                "room_seq": None,
                "x_idempotency_key": x_idempotency_key,
                "retry_count": 0,
                "next_retry_at": None,
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=_queue_unavailable_detail()) from exc
    return accepted_response


@router.post(
    "/orders/{order_id}/events",
    response_model=OrderEventAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
    tags=["reference-order-adapter"],
)
def create_order_event(
    order_id: Annotated[int, Path(ge=1, le=_MAX_POSTGRES_BIGINT)],
    payload: OrderEventCreate,
    x_idempotency_key: str | None = Header(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[^\x00]+$",
    ),
    current_user: dict = Depends(get_current_user),
):
    actor_user_id = int(current_user["id"])
    request_id = str(uuid4())
    category = classify_order_event(payload.event_type)
    queued_at = datetime.now(timezone.utc).isoformat()
    route = f"POST:/v1/orders/{order_id}/events"

    accepted_response = {
        "request_id": request_id,
        "status": "accepted",
        "persistence": "queued",
        "order_id": order_id,
        "stream_id": order_id,
        "user_id": actor_user_id,
        "event_type": payload.event_type,
        "category": category,
        "body": payload.body,
        "payment_id": payload.payment_id,
        "queued_at": queued_at,
    }
    metadata = {
        "reference_scenario": "order-lifecycle",
        "classification": category,
        "external_references": (
            {"payment": payload.payment_id} if payload.payment_id else {}
        ),
    }
    try:
        _store_request_and_queue_job(
            request_id,
            accepted_response,
            {
                "request_id": request_id,
                "route": route,
                "room_id": order_id,
                "user_id": actor_user_id,
                "body": payload.body,
                "room_seq": None,
                "x_idempotency_key": x_idempotency_key,
                "queued_at": queued_at,
                "retry_count": 0,
                "next_retry_at": None,
                "schema_version": 1,
                "event_type": payload.event_type,
                "payload": {"text": payload.body},
                "metadata": metadata,
                "category": category,
                "payment_id": payload.payment_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=_queue_unavailable_detail()) from exc
    return accepted_response


@generic_router.get(
    "/event-requests/{request_id}", response_model=GenericEventRequestStatusResponse
)
@router.get("/event-requests/{request_id}", response_model=EventRequestStatusResponse)
def get_event_request_status(
    request_id: Annotated[str, Path(min_length=1, max_length=80, pattern=r"^[^\x00]+$")],
    current_user: dict = Depends(get_current_user),
):
    try:
        status = _load_request_status(request_id)
    except (OperationalError, InterfaceError, PoolError) as exc:
        raise HTTPException(status_code=503, detail="Request status unavailable") from exc

    if status is None:
        # PostgreSQL owns durable request state. A healthy DB miss must not be
        # resurrected by an older compacted-cache value that may be followed by
        # a tombstone later in replay.
        raise HTTPException(status_code=404, detail="Request not found")
    try:
        status_actor_id = _request_status_actor_id(status)
    except ValueError as exc:
        logging.warning("Rejected malformed request status request_id=%s error=%s", request_id, exc)
        raise HTTPException(status_code=403, detail="Request access denied") from exc
    if status_actor_id != int(current_user["id"]):
        raise HTTPException(status_code=403, detail="Request access denied")
    return _externalize_request_status(status)


@router.get(
    "/streams/{stream_id}/persistence-summary",
    response_model=StreamPersistenceSummaryResponse,
)
def get_stream_persistence_summary(
    stream_id: Annotated[int, Path(ge=1, le=_MAX_POSTGRES_BIGINT)],
    current_user: dict = Depends(get_current_user),
):
    user_id = int(current_user["id"])
    try:
        with get_conn() as conn:
            with get_cursor(conn) as cur:
                _ensure_room_member(cur, stream_id, user_id)
                cur.execute(
                    """
                    /*NO LOAD BALANCE*/
                    WITH summary AS (
                        SELECT COUNT(*) AS persisted_count
                        FROM messages
                        WHERE room_id=%s
                    ), latest AS (
                        SELECT id, request_id, room_seq, created_at
                        FROM messages
                        WHERE room_id=%s
                        ORDER BY id DESC
                        LIMIT 1
                    )
                    SELECT
                        summary.persisted_count,
                        latest.id AS latest_event_id,
                        latest.request_id AS latest_request_id,
                        latest.room_seq AS latest_stream_seq,
                        latest.created_at AS latest_created_at
                    FROM summary
                    LEFT JOIN latest ON TRUE
                    """,
                    (stream_id, stream_id),
                )
                row = cur.fetchone()
    except (OperationalError, InterfaceError, PoolError) as exc:
        raise HTTPException(status_code=503, detail="Stream summary unavailable") from exc
    latest_created_at = row.get("latest_created_at")
    return {
        "stream_id": stream_id,
        "persisted_count": int(row["persisted_count"]),
        "latest_request_id": row.get("latest_request_id"),
        "latest_event_id": row.get("latest_event_id"),
        "latest_stream_seq": row.get("latest_stream_seq"),
        "latest_created_at": (
            latest_created_at.isoformat()
            if hasattr(latest_created_at, "isoformat")
            else latest_created_at
        ),
    }


@router.get("/dlq/ingress", response_model=DlqListResponse)
def get_ingress_dlq(
    limit: int = Query(default=20, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    user_id = int(current_user["id"])
    scan_limit = min(5000, max(limit, limit * 10))
    items = _list_recent_dlq_messages(scan_limit)
    summarized_items = [
        item
        for item in (_summarize_dlq_item(raw_item) for raw_item in items)
        if item.get("user_id") == user_id
    ][:limit]
    return {
        "queue_backend": "kafka",
        "topic": settings.kafka_dlq_topic,
        "scope": "recent_log_sample",
        "user_filtered": True,
        "count": len(summarized_items),
        "max_replay_count": settings.dlq_replay_max_count,
        "items": summarized_items,
    }


@router.get("/dlq/ingress/summary", response_model=DlqSummaryResponse)
def get_ingress_dlq_summary(
    limit: int = Query(default=200, ge=1, le=500),
    sample_limit: int = Query(default=5, ge=0, le=20),
    current_user: dict = Depends(get_current_user),
):
    user_id = int(current_user["id"])
    scan_limit = min(5000, max(limit, limit * 10))
    items = _list_recent_dlq_messages(scan_limit)
    summarized_items = [
        item
        for item in (_summarize_dlq_item(raw_item) for raw_item in items)
        if item.get("user_id") == user_id
    ][:limit]
    summary = _summarize_dlq_items(summarized_items, sample_limit=sample_limit)
    return {
        "queue_backend": "kafka",
        "topic": settings.kafka_dlq_topic,
        "scope": "recent_log_sample",
        "user_filtered": True,
        "limit": limit,
        "sample_limit": sample_limit,
        "max_replay_count": settings.dlq_replay_max_count,
        **summary,
    }


@router.post("/dlq/ingress/replay", response_model=DlqReplayResponse)
def replay_ingress_dlq_event(
    payload: DlqReplayRequest,
    current_user: dict = Depends(get_current_user),
):
    item = _find_dlq_item_by_request_id(payload.request_id, int(current_user["id"]))
    if item is None:
        raise HTTPException(status_code=404, detail="DLQ event not found")
    replay_generation, owner_token = _claim_manual_replay(item)
    try:
        result = _replay_dlq_payload(item)
    except Exception:
        try:
            release_dlq_replay_claim(payload.request_id, replay_generation, owner_token)
        except Exception as release_error:  # noqa: BLE001
            logging.warning(
                "DLQ replay claim release failed request_id=%s error=%s",
                payload.request_id,
                release_error,
            )
        raise
    try:
        if not mark_dlq_replay_published(payload.request_id, replay_generation, owner_token):
            logging.warning("DLQ replay publish claim was no longer owned request_id=%s", payload.request_id)
    except Exception as mark_error:  # noqa: BLE001
        logging.warning(
            "DLQ replay was published but claim finalization failed request_id=%s error=%s",
            payload.request_id,
            mark_error,
        )
    return result


def _event_row_to_response(row: dict) -> dict:
    created_at = row["created_at"]
    stream_id = row.get("stream_id", row.get("room_id"))
    stream_seq = row.get("stream_seq", row.get("room_seq"))
    user_id = row.get("user_id", row.get("actor_id"))
    actor_id = row.get("actor_id", user_id)
    payload = row.get("payload")
    if payload is None:
        payload = {"text": row.get("body", "")}
    return {
        "id": row["id"],
        "request_id": row.get("request_id"),
        "stream_id": stream_id,
        "stream_seq": stream_seq,
        "user_id": user_id,
        "actor_id": actor_id,
        "event_type": row.get("event_type") or "legacy.message",
        "category": row.get("category"),
        "payment_id": row.get("payment_id"),
        "body": row.get("body", ""),
        "schema_version": int(row.get("schema_version") or 1),
        "payload": payload,
        "metadata": row.get("metadata") or {},
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
    }


def _event_list_response(items: list[dict]) -> dict:
    return {
        "source": "database",
        "degraded": False,
        "snapshot_age_seconds": None,
        "items": items,
    }


@generic_router.get("/streams/{stream_id}/events", response_model=GenericEventListResponse)
@router.get("/streams/{stream_id}/events", response_model=EventListResponse)
def list_events(
    stream_id: Annotated[int, Path(ge=1, le=_MAX_POSTGRES_BIGINT)],
    limit: int = Query(default=20, ge=1, le=100),
    before_id: int | None = Query(default=None, ge=1, le=_MAX_POSTGRES_BIGINT),
    current_user: dict = Depends(get_current_user),
):
    user_id = int(current_user["id"])
    sql = """
        /*NO LOAD BALANCE*/
        SELECT id, request_id, room_id, room_seq, user_id,
               event_type, category, payment_id, schema_version,
               payload, metadata, body, created_at
        FROM messages
        WHERE room_id=%s
    """
    params: list[int] = [stream_id]

    if before_id is not None:
        sql += " AND id < %s"
        params.append(before_id)

    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)

    try:
        with get_conn() as conn:
            with get_cursor(conn) as cur:
                _ensure_room_member(cur, stream_id, user_id)
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
    except HTTPException:
        raise
    except (OperationalError, InterfaceError, PoolError) as exc:
        raise HTTPException(status_code=503, detail="Stream read unavailable") from exc

    result = [_event_row_to_response(row) for row in rows]
    return _event_list_response(result)


@router.post("/events/{event_id}/read", response_model=ReadReceiptResponse)
def mark_as_read(
    event_id: Annotated[int, Path(ge=1, le=_MAX_POSTGRES_BIGINT)],
    payload: ReadReceiptCreate,
    current_user: dict = Depends(get_current_user),
):
    try:
        with get_conn() as conn:
            with get_cursor(conn) as cur:
                user_id = int(current_user["id"])
                _message_room_id_for_member(cur, event_id, user_id)

                cur.execute(
                    """
                    INSERT INTO read_receipts (message_id, user_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (event_id, user_id),
                )
                conn.commit()
    except (OperationalError, InterfaceError, PoolError) as exc:
        raise HTTPException(status_code=503, detail="Read receipt store unavailable") from exc

    return {"status": "ok", "event_id": event_id, "user_id": user_id}


@router.get("/streams/{stream_id}/unread-count/{user_id}", response_model=UnreadCountResponse)
def unread_count(
    stream_id: Annotated[int, Path(ge=1, le=_MAX_POSTGRES_BIGINT)],
    user_id: Annotated[int, Path(ge=1, le=_MAX_POSTGRES_BIGINT)],
    current_user: dict = Depends(get_current_user),
):
    if int(current_user["id"]) != user_id:
        raise HTTPException(status_code=403, detail="Unread count access denied")
    try:
        with get_conn() as conn:
            with get_cursor(conn) as cur:
                _ensure_room_member(cur, stream_id, user_id)
                cur.execute(
                    """
                    /*NO LOAD BALANCE*/ SELECT COUNT(*) AS unread
                    FROM messages m
                    WHERE m.room_id=%s
                    AND NOT EXISTS (
                        SELECT 1
                        FROM read_receipts rr
                        WHERE rr.message_id = m.id
                        AND rr.user_id = %s
                    )
                    """,
                    (stream_id, user_id),
                )
                row = cur.fetchone()
    except (OperationalError, InterfaceError, PoolError) as exc:
        raise HTTPException(status_code=503, detail="Unread count unavailable") from exc
    return {"stream_id": stream_id, "user_id": user_id, "unread": int(row["unread"])}
