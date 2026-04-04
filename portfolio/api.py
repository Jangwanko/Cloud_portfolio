import json

from fastapi import APIRouter, Header, HTTPException, Query

from portfolio.config import settings
from portfolio.db import get_conn, get_cursor
from portfolio.redis_client import get_redis
from portfolio.schemas import (
    MessageCreate,
    ReadReceiptCreate,
    RoomCreate,
    UserCreate,
)

router = APIRouter(prefix="/v1", tags=["messaging"])


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

    with get_conn() as conn:
        with get_cursor(conn) as cur:
            if x_idempotency_key:
                cur.execute(
                    "SELECT response_json FROM idempotency_keys WHERE route=%s AND idem_key=%s",
                    (route, x_idempotency_key),
                )
                cached = cur.fetchone()
                if cached:
                    return cached["response_json"]

            cur.execute("SELECT id FROM rooms WHERE id=%s", (room_id,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Room not found")

            cur.execute("SELECT id FROM users WHERE id=%s", (payload.user_id,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="User not found")

            cur.execute(
                """
                INSERT INTO messages (room_id, user_id, body)
                VALUES (%s, %s, %s)
                RETURNING id, room_id, user_id, body, created_at
                """,
                (room_id, payload.user_id, payload.body),
            )
            message = cur.fetchone()

            response = {
                "id": message["id"],
                "room_id": message["room_id"],
                "user_id": message["user_id"],
                "body": message["body"],
                "created_at": message["created_at"].isoformat(),
            }

            if x_idempotency_key:
                cur.execute(
                    """
                    INSERT INTO idempotency_keys (route, idem_key, response_json)
                    VALUES (%s, %s, %s::jsonb)
                    """,
                    (route, x_idempotency_key, json.dumps(response)),
                )

            conn.commit()

    job_payload = {
        "message_id": response["id"],
        "room_id": room_id,
        "body_preview": payload.body[:30],
    }
    get_redis().lpush(settings.notification_queue, json.dumps(job_payload))

    return response


@router.get("/rooms/{room_id}/messages")
def list_messages(
    room_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    before_id: int | None = Query(default=None),
):
    sql = """
        SELECT id, room_id, user_id, body, created_at
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
