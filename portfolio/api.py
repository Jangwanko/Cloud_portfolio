import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query

from portfolio.config import settings
from portfolio.db import get_conn, get_cursor
from portfolio.redis_client import get_redis, reconnect_redis, update_queue_depth
from portfolio.schemas import (
    MessageCreate,
    ReadReceiptCreate,
    RoomCreate,
    UserCreate,
)

router = APIRouter(prefix="/v1", tags=["messaging"])


def _request_status_key(request_id: str) -> str:
    return f"message_request_status:{request_id}"


def _fallback_idem_key(route: str, idem_key: str) -> str:
    return f"message_request_idem:{route}:{idem_key}"


def _redis_client():
    try:
        return get_redis()
    except Exception:
        return reconnect_redis()


def _store_request_status(request_id: str, payload: dict) -> None:
    _redis_client().set(_request_status_key(request_id), json.dumps(payload))


def _load_request_status(request_id: str) -> dict | None:
    raw = _redis_client().get(_request_status_key(request_id))
    if not raw:
        return None
    return json.loads(raw)


def _set_fallback_idem(route: str, idem_key: str, request_id: str) -> bool:
    return bool(_redis_client().set(_fallback_idem_key(route, idem_key), request_id, nx=True))


def _get_fallback_idem(route: str, idem_key: str) -> str | None:
    return _redis_client().get(_fallback_idem_key(route, idem_key))


def _queue_ingress_message(job_payload: dict) -> None:
    redis_client = _redis_client()
    redis_client.lpush(settings.ingress_queue, json.dumps(job_payload))
    update_queue_depth(settings.ingress_queue)


@router.post("/users")
def create_user(payload: UserCreate):
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            try:
                cur.execute(
                    "INSERT INTO users (username) VALUES (%s) RETURNING id, username",
                    (payload.username,),
                )
                row = cur.fetchone()
                conn.commit()
                return row
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                raise HTTPException(status_code=409, detail="Username already exists") from exc


@router.post("/rooms")
def create_room(payload: RoomCreate):
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                "INSERT INTO rooms (name) VALUES (%s) RETURNING id, name",
                (payload.name,),
            )
            room = cur.fetchone()

            valid_member_ids: list[int] = []
            for member_id in payload.member_ids:
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
            return {
                "id": room["id"],
                "name": room["name"],
                "member_ids": valid_member_ids,
            }


@router.post("/rooms/{room_id}/messages")
def create_message(
    room_id: int,
    payload: MessageCreate,
    x_idempotency_key: str | None = Header(default=None),
):
    route = f"POST:/v1/rooms/{room_id}/messages"

    if x_idempotency_key:
        existing_request_id = _get_fallback_idem(route, x_idempotency_key)
        if existing_request_id:
            existing_status = _load_request_status(existing_request_id)
            if existing_status is not None:
                return existing_status

    request_id = str(uuid4())
    queued_at = None

    if x_idempotency_key:
        inserted = _set_fallback_idem(route, x_idempotency_key, request_id)
        if not inserted:
            existing_request_id = _get_fallback_idem(route, x_idempotency_key)
            if existing_request_id:
                existing_status = _load_request_status(existing_request_id)
                if existing_status is not None:
                    return existing_status

    queued_at = datetime.now(timezone.utc).isoformat()
    accepted_response = {
        "request_id": request_id,
        "status": "accepted",
        "persistence": "queued",
        "room_id": room_id,
        "user_id": payload.user_id,
        "body": payload.body,
        "queued_at": queued_at,
    }
    _store_request_status(request_id, accepted_response)
    _queue_ingress_message(
        {
            "request_id": request_id,
            "route": route,
            "room_id": room_id,
            "user_id": payload.user_id,
            "body": payload.body,
            "x_idempotency_key": x_idempotency_key,
            "queued_at": queued_at,
        }
    )
    return accepted_response


@router.get("/message-requests/{request_id}")
def get_message_request_status(request_id: str):
    status = _load_request_status(request_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return status


@router.get("/rooms/{room_id}/messages")
def list_messages(
    room_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    before_id: int | None = Query(default=None),
):
    sql = """
        SELECT id, request_id, room_id, user_id, body, created_at
        FROM messages
        WHERE room_id=%s
    """
    params: list[int] = [room_id]

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
                "room_id": row["room_id"],
                "user_id": row["user_id"],
                "body": row["body"],
                "created_at": row["created_at"].isoformat(),
            }
        )
    return result


@router.post("/messages/{message_id}/read")
def mark_as_read(message_id: int, payload: ReadReceiptCreate):
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute("SELECT id FROM messages WHERE id=%s", (message_id,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Message not found")

            cur.execute("SELECT id FROM users WHERE id=%s", (payload.user_id,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="User not found")

            cur.execute(
                """
                INSERT INTO read_receipts (message_id, user_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (message_id, payload.user_id),
            )
            conn.commit()

    return {"status": "ok", "message_id": message_id, "user_id": payload.user_id}


@router.get("/rooms/{room_id}/unread-count/{user_id}")
def unread_count(room_id: int, user_id: int):
    with get_conn() as conn:
        with get_cursor(conn) as cur:
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
                (room_id, user_id),
            )
            row = cur.fetchone()
    return {"room_id": room_id, "user_id": user_id, "unread": int(row["unread"])}
