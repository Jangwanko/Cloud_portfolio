import json

from portfolio.db import get_conn, get_cursor


def is_kafka_backend() -> bool:
    return True


def request_status_key(request_id: str) -> str:
    return f"message_request_status:{request_id}"


def fallback_idem_key(route: str, idem_key: str) -> str:
    return f"message_request_idem:{route}:{idem_key}"


def store_request_status(request_id: str, payload: dict) -> None:
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO request_statuses (request_id, user_id, status_json)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (request_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    status_json = EXCLUDED.status_json,
                    updated_at = NOW()
                """,
                (request_id, payload.get("user_id"), json.dumps(payload)),
            )
        conn.commit()


def load_request_status(request_id: str) -> dict | None:
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                "SELECT status_json FROM request_statuses WHERE request_id=%s",
                (request_id,),
            )
            row = cur.fetchone()
    if row is None:
        return None
    status = row["status_json"]
    if isinstance(status, str):
        return json.loads(status)
    return status


def delete_request_status(request_id: str) -> None:
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute("DELETE FROM request_statuses WHERE request_id=%s", (request_id,))
        conn.commit()


def set_fallback_idem(route: str, idem_key: str, request_id: str) -> bool:
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO intake_idempotency_keys (route, idem_key, request_id)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING request_id
                """,
                (route, idem_key, request_id),
            )
            inserted = cur.fetchone() is not None
        conn.commit()
    return inserted


def get_fallback_idem(route: str, idem_key: str) -> str | None:
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                """
                SELECT request_id
                FROM intake_idempotency_keys
                WHERE route=%s AND idem_key=%s
                """,
                (route, idem_key),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return str(row["request_id"])


def clear_fallback_idem(route: str, idem_key: str, request_id: str | None = None) -> None:
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            if request_id is None:
                cur.execute(
                    "DELETE FROM intake_idempotency_keys WHERE route=%s AND idem_key=%s",
                    (route, idem_key),
                )
            else:
                cur.execute(
                    """
                    DELETE FROM intake_idempotency_keys
                    WHERE route=%s AND idem_key=%s AND request_id=%s
                    """,
                    (route, idem_key, request_id),
                )
        conn.commit()


def next_room_seq(room_id: int) -> int:
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO room_sequence_allocations (room_id, last_seq)
                VALUES (%s, 0)
                ON CONFLICT (room_id) DO NOTHING
                """,
                (room_id,),
            )
            cur.execute(
                """
                UPDATE room_sequence_allocations
                SET last_seq = last_seq + 1,
                    updated_at = NOW()
                WHERE room_id=%s
                RETURNING last_seq
                """,
                (room_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return int(row["last_seq"])
