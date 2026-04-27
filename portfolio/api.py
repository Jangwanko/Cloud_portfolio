import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from portfolio.auth import authenticate_user, create_access_token, get_current_user, hash_password
from portfolio.config import settings
from portfolio.db import get_conn, get_cursor
from portfolio.kafka_client import list_recent_topic_messages, publish_ingress_job
from portfolio.metrics import observe_api_stage
from portfolio.schemas import (
    EventCreate,
    LoginRequest,
    ReadReceiptCreate,
    StreamCreate,
    UserCreate,
)
from portfolio.state_store import (
    clear_fallback_idem,
    delete_request_status,
    fallback_idem_key,
    get_fallback_idem,
    load_request_status,
    request_status_key,
    set_fallback_idem,
    store_request_status,
)

router = APIRouter(prefix="/v1", tags=["events"])

_membership_cache: dict[tuple[int, int], float] = {}


def _request_status_key(request_id: str) -> str:
    return request_status_key(request_id)


def _fallback_idem_key(route: str, idem_key: str) -> str:
    return fallback_idem_key(route, idem_key)


def _queue_unavailable_detail() -> str:
    return "Kafka unavailable"


def _state_unavailable_detail() -> str:
    return "PostgreSQL state store unavailable"


def _store_request_status(request_id: str, payload: dict) -> None:
    store_request_status(request_id, payload)


def _load_request_status(request_id: str) -> dict | None:
    return load_request_status(request_id)


def _externalize_request_status(payload: dict) -> dict:
    status = dict(payload)
    if "message_id" in status:
        status["event_id"] = status.pop("message_id")
    if "room_id" in status:
        status["stream_id"] = status.pop("room_id")
    if "room_seq" in status:
        status["stream_seq"] = status.pop("room_seq")
    return status


def _set_fallback_idem(route: str, idem_key: str, request_id: str) -> bool:
    return set_fallback_idem(route, idem_key, request_id)


def _get_fallback_idem(route: str, idem_key: str) -> str | None:
    return get_fallback_idem(route, idem_key)


def _clear_fallback_idem(route: str, idem_key: str, request_id: str | None = None) -> None:
    clear_fallback_idem(route, idem_key, request_id)


def _delete_request_status(request_id: str) -> None:
    delete_request_status(request_id)


def _claim_or_load_request(route: str, idem_key: str, request_id: str) -> dict | None:
    with observe_api_stage("postgres_idempotency"):
        # The common path is a new idempotency key, so claim first and only
        # do extra lookups when the key already exists.
        if _set_fallback_idem(route, idem_key, request_id):
            return None

        existing_request_id = _get_fallback_idem(route, idem_key)
        if existing_request_id:
            existing_status = _load_request_status(existing_request_id)
            if existing_status is not None:
                return _externalize_request_status(existing_status)
            _clear_fallback_idem(route, idem_key, existing_request_id)

        if _set_fallback_idem(route, idem_key, request_id):
            return None

        existing_request_id = _get_fallback_idem(route, idem_key)
        if existing_request_id:
            existing_status = _load_request_status(existing_request_id)
            if existing_status is not None:
                return _externalize_request_status(existing_status)

    raise RuntimeError("Failed to claim idempotency key")


def _store_request_and_queue_job(request_id: str, request_payload: dict, job_payload: dict) -> None:
    with observe_api_stage("kafka_publish"):
        publish_ingress_job(job_payload["room_id"], job_payload)


def _room_members_key(room_id: int) -> str:
    return f"room_members:{room_id}"


def _membership_cache_key(room_id: int, user_id: int) -> tuple[int, int]:
    return (int(room_id), int(user_id))


def _cache_membership(room_id: int, user_id: int) -> None:
    _membership_cache[_membership_cache_key(room_id, user_id)] = (
        time.monotonic() + settings.membership_cache_ttl_seconds
    )


def _is_membership_cached(room_id: int, user_id: int) -> bool:
    expires_at = _membership_cache.get(_membership_cache_key(room_id, user_id))
    if expires_at is None:
        return False
    if expires_at < time.monotonic():
        _membership_cache.pop(_membership_cache_key(room_id, user_id), None)
        return False
    return True


def _cache_room_members(room_id: int, member_ids: list[int]) -> None:
    if not member_ids:
        return
    for member_id in member_ids:
        _cache_membership(room_id, int(member_id))


def _ensure_room_exists(cur, room_id: int) -> None:
    cur.execute("SELECT id FROM rooms WHERE id=%s", (room_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Stream not found")


def _ensure_room_member(cur, room_id: int, user_id: int) -> None:
    _ensure_room_exists(cur, room_id)
    cur.execute(
        "SELECT 1 FROM room_members WHERE room_id=%s AND user_id=%s",
        (room_id, user_id),
    )
    if cur.fetchone() is None:
        raise HTTPException(status_code=403, detail="Stream access denied")


def _ensure_room_member_for_ingress(room_id: int, user_id: int) -> None:
    with observe_api_stage("membership_check"):
        if _is_membership_cached(room_id, user_id):
            return

        try:
            with get_conn() as conn:
                with get_cursor(conn) as cur:
                    _ensure_room_member(cur, room_id, user_id)
                    _cache_room_members(room_id, [user_id])
            return
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=503, detail="Authorization check unavailable")


def _message_room_id(cur, message_id: int) -> int:
    cur.execute("SELECT room_id FROM messages WHERE id=%s", (message_id,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return int(row["room_id"])


@router.post("/users")
def create_user(payload: UserCreate):
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


@router.post("/auth/login")
def login(payload: LoginRequest):
    user = authenticate_user(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    access_token = create_access_token(user["id"], user["username"])
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user,
    }


@router.post("/streams")
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
            _cache_room_members(int(room["id"]), valid_member_ids)
            return {
                "id": room["id"],
                "name": room["name"],
                "member_ids": valid_member_ids,
            }


@router.post("/streams/{stream_id}/events")
def create_event(
    stream_id: int,
    payload: EventCreate,
    x_idempotency_key: str | None = Header(default=None),
    current_user: dict = Depends(get_current_user),
):
    actor_user_id = int(current_user["id"])
    _ensure_room_member_for_ingress(stream_id, actor_user_id)

    route = f"POST:/v1/streams/{stream_id}/events"
    request_id = str(uuid4())

    if x_idempotency_key:
        try:
            existing_status = _claim_or_load_request(route, x_idempotency_key, request_id)
            if existing_status is not None:
                return existing_status
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=_state_unavailable_detail()) from exc

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
        if x_idempotency_key:
            try:
                _clear_fallback_idem(route, x_idempotency_key, request_id)
            except Exception:
                pass
        try:
            _delete_request_status(request_id)
        except Exception:
            pass
        raise HTTPException(status_code=503, detail=_queue_unavailable_detail()) from exc
    return accepted_response


@router.get("/event-requests/{request_id}")
def get_event_request_status(request_id: str, current_user: dict = Depends(get_current_user)):
    status = _load_request_status(request_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Request not found")
    status_user_id = status.get("user_id")
    if status_user_id is not None and int(status_user_id) != int(current_user["id"]):
        raise HTTPException(status_code=403, detail="Request access denied")
    return _externalize_request_status(status)


@router.get("/dlq/ingress")
def get_ingress_dlq(
    limit: int = Query(default=20, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    items = list_recent_topic_messages(settings.kafka_dlq_topic, limit)
    return {
        "queue_backend": "kafka",
        "topic": settings.kafka_dlq_topic,
        "count": len(items),
        "items": items,
    }


@router.get("/streams/{stream_id}/events")
def list_events(
    stream_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    before_id: int | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            _ensure_room_member(cur, stream_id, int(current_user["id"]))

    sql = """
        SELECT id, request_id, room_id, room_seq, user_id, body, created_at
        FROM messages
        WHERE room_id=%s
    """
    params: list[int] = [stream_id]

    if before_id is not None:
        sql += " AND id < %s"
        params.append(before_id)

    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)

    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()

    result = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "request_id": row["request_id"],
                "stream_id": row["room_id"],
                "stream_seq": row["room_seq"],
                "user_id": row["user_id"],
                "body": row["body"],
                "created_at": row["created_at"].isoformat(),
            }
        )
    return result


@router.post("/events/{event_id}/read")
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


@router.get("/streams/{stream_id}/unread-count/{user_id}")
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
