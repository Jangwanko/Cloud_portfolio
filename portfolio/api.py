from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from portfolio.auth import authenticate_user, create_access_token, get_current_user, hash_password
from portfolio.config import settings
from portfolio.db import get_conn, get_cursor
from portfolio.kafka_client import list_recent_topic_messages, publish_ingress_job, publish_stream_snapshot, reset_topic
from portfolio.materialized_cache import (
    cache_stream_snapshot,
    get_cached_request_status,
    is_cached_stream_member,
    list_cached_events,
)
from portfolio.metrics import observe_api_stage
from portfolio.order_events import classify_order_event
from portfolio.schemas import (
    DemoResetRequest,
    DemoResetResponse,
    DlqListResponse,
    DlqSummaryResponse,
    EventCreate,
    EventAcceptedResponse,
    EventListResponse,
    EventRequestStatusResponse,
    EventResponse,
    LoginRequest,
    OrderEventAcceptedResponse,
    OrderEventCreate,
    ReadReceiptCreate,
    ReadReceiptResponse,
    StreamCreate,
    StreamResponse,
    TokenResponse,
    UnreadCountResponse,
    UserCreate,
    UserResponse,
)
from portfolio.state_store import (
    fallback_idem_key,
    load_request_status,
    request_status_key,
)

router = APIRouter(prefix="/v1", tags=["events"])


def _request_status_key(request_id: str) -> str:
    return request_status_key(request_id)


def _fallback_idem_key(route: str, idem_key: str) -> str:
    return fallback_idem_key(route, idem_key)


def _queue_unavailable_detail() -> str:
    return "Kafka unavailable"


def _load_request_status(request_id: str) -> dict | None:
    return load_request_status(request_id)


def _ensure_demo_reset_allowed() -> None:
    allowed_envs = {"local", "k8s", "k8s-ha", "k8s-demo-lite", "development", "dev", "test"}
    if settings.app_env not in allowed_envs:
        raise HTTPException(status_code=403, detail="Demo reset is disabled in this environment")


def _reset_demo_event_data(cur) -> dict:
    cur.execute("SELECT COUNT(*) AS count FROM messages")
    message_count = int(cur.fetchone()["count"])
    cur.execute("SELECT COUNT(*) AS count FROM rooms")
    stream_count = int(cur.fetchone()["count"])
    cur.execute("SELECT COUNT(*) AS count FROM request_statuses")
    request_status_count = int(cur.fetchone()["count"])

    cur.execute("TRUNCATE TABLE notification_attempts RESTART IDENTITY")
    cur.execute("TRUNCATE TABLE idempotency_keys")
    cur.execute("TRUNCATE TABLE intake_idempotency_keys")
    cur.execute("TRUNCATE TABLE request_statuses")
    cur.execute("TRUNCATE TABLE rooms RESTART IDENTITY CASCADE")

    return {
        "deleted_messages": message_count,
        "reset_streams": stream_count,
        "reset_request_statuses": request_status_count,
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
    if "message_id" in status:
        status["event_id"] = status.pop("message_id")
    if "room_id" in status:
        status["stream_id"] = status.pop("room_id")
    if "room_seq" in status:
        status["stream_seq"] = status.pop("room_seq")
    return status


def _store_request_and_queue_job(request_id: str, request_payload: dict, job_payload: dict) -> None:
    with observe_api_stage("kafka_publish"):
        publish_ingress_job(job_payload["room_id"], job_payload)


def _summarize_dlq_item(item: dict) -> dict:
    value = item.get("value") or {}
    replay_count = int(value.get("replay_count", 0) or 0)
    return {
        "topic": item.get("topic"),
        "partition": item.get("partition"),
        "offset": item.get("offset"),
        "timestamp": item.get("timestamp"),
        "key": item.get("key"),
        "request_id": value.get("request_id"),
        "stream_id": value.get("room_id"),
        "user_id": value.get("user_id"),
        "failed_reason": value.get("failed_reason"),
        "retry_count": int(value.get("retry_count", 0) or 0),
        "replay_count": replay_count,
        "replayable": replay_count < settings.dlq_replay_max_count,
        "max_replay_count": settings.dlq_replay_max_count,
        "failed_at": value.get("failed_at"),
        "replayed_at": value.get("replayed_at"),
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
    oldest_age_seconds: int | None = None
    replayable_count = 0
    blocked_count = 0

    for item in items:
        reason = item.get("failed_reason") or "unknown"
        reasons[str(reason)] += 1

        stream_id = item.get("stream_id")
        if stream_id is not None:
            streams[int(stream_id)] += 1

        if item.get("replayable"):
            replayable_count += 1
        else:
            blocked_count += 1

        event_ts = _parse_dlq_timestamp_seconds(item)
        if event_ts is not None:
            age_seconds = max(0, int(now_ts - event_ts))
            if oldest_age_seconds is None or age_seconds > oldest_age_seconds:
                oldest_age_seconds = age_seconds

    by_stream = [
        {"stream_id": stream_id, "count": count}
        for stream_id, count in sorted(streams.items(), key=lambda entry: (-entry[1], entry[0]))
    ]

    return {
        "total": len(items),
        "replayable": replayable_count,
        "blocked": blocked_count,
        "oldest_age_seconds": oldest_age_seconds,
        "by_reason": dict(sorted(reasons.items())),
        "by_stream": by_stream,
        "recent_samples": items[:sample_limit],
    }


def _ensure_room_exists(cur, room_id: int) -> None:
    cur.execute("/*NO LOAD BALANCE*/ SELECT id FROM rooms WHERE id=%s", (room_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Stream not found")


def _ensure_room_member(cur, room_id: int, user_id: int) -> None:
    _ensure_room_exists(cur, room_id)
    cur.execute(
        "/*NO LOAD BALANCE*/ SELECT 1 FROM room_members WHERE room_id=%s AND user_id=%s",
        (room_id, user_id),
    )
    if cur.fetchone() is None:
        raise HTTPException(status_code=403, detail="Stream access denied")


def _message_room_id(cur, message_id: int) -> int:
    cur.execute("SELECT room_id FROM messages WHERE id=%s", (message_id,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return int(row["room_id"])


@router.post("/users", response_model=UserResponse)
def create_user(payload: UserCreate):
    try:
        with get_conn() as conn:
            with get_cursor(conn) as cur:
                try:
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
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    raise HTTPException(status_code=409, detail="Username already exists") from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="User database unavailable") from exc


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest):
    try:
        user = authenticate_user(payload.username, payload.password)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="Auth database unavailable") from exc
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

    with get_conn() as conn:
        with get_cursor(conn) as cur:
            try:
                result = _reset_demo_event_data(cur)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    try:
        reset_dlq_topic = _reset_demo_kafka_dlq()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="Demo DB reset completed, but Kafka DLQ reset failed") from exc

    return {
        "status": "reset",
        "deleted_messages": result["deleted_messages"],
        "reset_streams": result["reset_streams"],
        "reset_request_statuses": result["reset_request_statuses"],
        "reset_dlq_topic": reset_dlq_topic,
        "note": f"Demo event data and DLQ topic reset by user_id={current_user['id']}. Users were kept.",
    }


@router.post("/streams", response_model=StreamResponse)
def create_stream(payload: StreamCreate, current_user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                "INSERT INTO rooms (name) VALUES (%s) RETURNING id, name",
                (payload.name,),
            )
            room = cur.fetchone()

            requested_member_ids = set(payload.member_ids)
            requested_member_ids.add(int(current_user["id"]))
            valid_member_ids: list[int] = []
            for member_id in sorted(requested_member_ids):
                cur.execute("SELECT id FROM users WHERE id=%s", (member_id,))
                if cur.fetchone() is not None:
                    valid_member_ids.append(member_id)
                    cur.execute(
                        """
                        INSERT INTO room_members (room_id, user_id)
                        VALUES (%s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (room["id"], member_id),
                    )

            conn.commit()
            stream_snapshot = {
                "stream_id": int(room["id"]),
                "name": room["name"],
                "member_ids": valid_member_ids,
            }
            cache_stream_snapshot(stream_snapshot)
            try:
                publish_stream_snapshot(int(room["id"]), stream_snapshot)
            except Exception:
                pass
            return {
                "id": room["id"],
                "name": room["name"],
                "member_ids": valid_member_ids,
            }


@router.post("/streams/{stream_id}/events", response_model=EventAcceptedResponse)
def create_event(
    stream_id: int,
    payload: EventCreate,
    x_idempotency_key: str | None = Header(default=None),
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


@router.post("/orders/{order_id}/events", response_model=OrderEventAcceptedResponse)
def create_order_event(
    order_id: int,
    payload: OrderEventCreate,
    x_idempotency_key: str | None = Header(default=None),
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
                "order_id": order_id,
                "event_type": payload.event_type,
                "category": category,
                "payment_id": payload.payment_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=_queue_unavailable_detail()) from exc
    return accepted_response


@router.get("/event-requests/{request_id}", response_model=EventRequestStatusResponse)
def get_event_request_status(request_id: str, current_user: dict = Depends(get_current_user)):
    try:
        status = _load_request_status(request_id)
    except Exception as exc:  # noqa: BLE001
        status = get_cached_request_status(request_id)
        if status is None:
            raise HTTPException(status_code=503, detail="Request status unavailable") from exc

    if status is None:
        status = get_cached_request_status(request_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Request not found")
    status_user_id = status.get("user_id")
    if status_user_id is not None and int(status_user_id) != int(current_user["id"]):
        raise HTTPException(status_code=403, detail="Request access denied")
    return _externalize_request_status(status)


@router.get("/dlq/ingress", response_model=DlqListResponse)
def get_ingress_dlq(
    limit: int = Query(default=20, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    items = list_recent_topic_messages(settings.kafka_dlq_topic, limit)
    summarized_items = [_summarize_dlq_item(item) for item in items]
    return {
        "queue_backend": "kafka",
        "topic": settings.kafka_dlq_topic,
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
    items = list_recent_topic_messages(settings.kafka_dlq_topic, limit)
    summarized_items = [_summarize_dlq_item(item) for item in items]
    summary = _summarize_dlq_items(summarized_items, sample_limit=sample_limit)
    return {
        "queue_backend": "kafka",
        "topic": settings.kafka_dlq_topic,
        "limit": limit,
        "sample_limit": sample_limit,
        "max_replay_count": settings.dlq_replay_max_count,
        **summary,
    }


def _event_row_to_response(row: dict) -> dict:
    created_at = row["created_at"]
    return {
        "id": row["id"],
        "request_id": row["request_id"],
        "stream_id": row["room_id"],
        "stream_seq": row["room_seq"],
        "user_id": row["user_id"],
        "event_type": row["event_type"],
        "category": row["category"],
        "payment_id": row["payment_id"],
        "body": row["body"],
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
    }


def _event_list_response(source: str, degraded: bool, items: list[dict], snapshot_age: float | None) -> dict:
    return {
        "source": source,
        "degraded": degraded,
        "snapshot_age_seconds": None if snapshot_age is None else round(snapshot_age, 3),
        "items": items,
    }


@router.get("/streams/{stream_id}/events", response_model=EventListResponse)
def list_events(
    stream_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    before_id: int | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    user_id = int(current_user["id"])
    cached_items, snapshot_age = list_cached_events(stream_id, limit, before_id)
    if (
        before_id is None
        and cached_items
        and snapshot_age is not None
        and snapshot_age <= settings.snapshot_cache_fresh_seconds
        and is_cached_stream_member(stream_id, user_id)
    ):
        return _event_list_response("cache", False, cached_items, snapshot_age)

    try:
        with get_conn() as conn:
            with get_cursor(conn) as cur:
                _ensure_room_member(cur, stream_id, user_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        if not is_cached_stream_member(stream_id, user_id):
            raise HTTPException(status_code=503, detail="Stream read unavailable") from exc
        if cached_items:
            return _event_list_response("cache", True, cached_items, snapshot_age)
        raise HTTPException(status_code=503, detail="Stream read unavailable") from exc

    sql = """
        SELECT id, request_id, room_id, room_seq, user_id, event_type, category, payment_id, body, created_at
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
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        if cached_items:
            return _event_list_response("cache", True, cached_items, snapshot_age)
        raise HTTPException(status_code=503, detail="Stream read unavailable") from exc

    result = [_event_row_to_response(row) for row in rows]
    return _event_list_response("db", False, result, None)


@router.post("/events/{event_id}/read", response_model=ReadReceiptResponse)
def mark_as_read(
    event_id: int,
    payload: ReadReceiptCreate,
    current_user: dict = Depends(get_current_user),
):
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            room_id = _message_room_id(cur, event_id)
            _ensure_room_member(cur, room_id, int(current_user["id"]))

            cur.execute(
                """
                INSERT INTO read_receipts (message_id, user_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (event_id, int(current_user["id"])),
            )
            conn.commit()

    return {"status": "ok", "event_id": event_id, "user_id": int(current_user["id"])}


@router.get("/streams/{stream_id}/unread-count/{user_id}", response_model=UnreadCountResponse)
def unread_count(stream_id: int, user_id: int, current_user: dict = Depends(get_current_user)):
    if int(current_user["id"]) != user_id:
        raise HTTPException(status_code=403, detail="Unread count access denied")
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            _ensure_room_member(cur, stream_id, user_id)
            cur.execute(
                """
                SELECT COUNT(*) AS unread
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
    return {"stream_id": stream_id, "user_id": user_id, "unread": int(row["unread"])}
