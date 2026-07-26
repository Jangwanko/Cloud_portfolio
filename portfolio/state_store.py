import json
from uuid import uuid4

from portfolio.db import get_conn, get_cursor
from portfolio.event_envelope import MAX_JSON_WIRE_NESTING_DEPTH, validate_json_structure


DLQ_REPLAY_CLAIM_ROUTE = "POST:/v1/dlq/ingress/replay"
DLQ_REPLAY_CLAIM_LEASE_SECONDS = 30
_MAX_POSTGRES_BIGINT = 9_223_372_036_854_775_807


class RequestStatusOwnerConflict(ValueError):
    pass


def _validate_replay_claim_identity(request_id: str, replay_generation: int) -> None:
    if (
        not isinstance(request_id, str)
        or not request_id
        or len(request_id) > 80
        or "\x00" in request_id
    ):
        raise ValueError("Invalid DLQ replay request_id")
    validate_json_structure(request_id)
    if (
        type(replay_generation) is not int
        or replay_generation < 0
        or replay_generation > _MAX_POSTGRES_BIGINT
    ):
        raise ValueError("Invalid DLQ replay generation")


def _dlq_replay_claim_key(request_id: str, replay_generation: int) -> str:
    _validate_replay_claim_identity(request_id, replay_generation)
    return f"{request_id}:{replay_generation}"


def _dlq_replay_claim_value(request_id: str, owner_token: str) -> str:
    _validate_replay_claim_identity(request_id, 0)
    if (
        not isinstance(owner_token, str)
        or not owner_token
        or len(owner_token) > 80
        or "\x00" in owner_token
    ):
        raise ValueError("Invalid DLQ replay owner token")
    validate_json_structure(owner_token)
    return f"claimed:{owner_token}:{request_id}"


def claim_dlq_replay(request_id: str, replay_generation: int) -> tuple[str, str | None]:
    claim_key = _dlq_replay_claim_key(request_id, replay_generation)
    owner_token = str(uuid4())
    claim_value = _dlq_replay_claim_value(request_id, owner_token)

    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                "/*NO LOAD BALANCE*/ SELECT status_json FROM request_statuses WHERE request_id=%s",
                (request_id,),
            )
            row = cur.fetchone()
            status_payload = row.get("status_json") if row else None
            if isinstance(status_payload, str):
                try:
                    status_payload = json.loads(status_payload)
                except json.JSONDecodeError:
                    status_payload = None
            if isinstance(status_payload, dict) and status_payload.get("status") == "persisted":
                return "persisted", None

            cur.execute(
                """
                INSERT INTO intake_idempotency_keys (route, idem_key, request_id)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING request_id
                """,
                (DLQ_REPLAY_CLAIM_ROUTE, claim_key, claim_value),
            )
            if cur.fetchone() is not None:
                conn.commit()
                return "claimed", owner_token

            cur.execute(
                """
                /*NO LOAD BALANCE*/ SELECT request_id
                FROM intake_idempotency_keys
                WHERE route=%s AND idem_key=%s
                FOR UPDATE
                """,
                (DLQ_REPLAY_CLAIM_ROUTE, claim_key),
            )
            existing = cur.fetchone()
            existing_value = str(existing["request_id"]) if existing else ""
            if existing_value.startswith("published:") or not existing_value.startswith("claimed:"):
                return "published", None

            cur.execute(
                """
                UPDATE intake_idempotency_keys
                SET request_id=%s, created_at=NOW()
                WHERE route=%s AND idem_key=%s
                  AND created_at < NOW() - (%s * INTERVAL '1 second')
                RETURNING request_id
                """,
                (
                    claim_value,
                    DLQ_REPLAY_CLAIM_ROUTE,
                    claim_key,
                    DLQ_REPLAY_CLAIM_LEASE_SECONDS,
                ),
            )
            if cur.fetchone() is not None:
                conn.commit()
                return "claimed", owner_token

    return "in_progress", None


def mark_dlq_replay_published(
    request_id: str,
    replay_generation: int,
    owner_token: str,
) -> bool:
    claim_key = _dlq_replay_claim_key(request_id, replay_generation)
    claim_value = _dlq_replay_claim_value(request_id, owner_token)
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                """
                UPDATE intake_idempotency_keys
                SET request_id=%s, created_at=NOW()
                WHERE route=%s AND idem_key=%s AND request_id=%s
                RETURNING request_id
                """,
                (
                    f"published:{request_id}",
                    DLQ_REPLAY_CLAIM_ROUTE,
                    claim_key,
                    claim_value,
                ),
            )
            updated = cur.fetchone() is not None
        conn.commit()
    return updated


def release_dlq_replay_claim(
    request_id: str,
    replay_generation: int,
    owner_token: str,
) -> None:
    claim_key = _dlq_replay_claim_key(request_id, replay_generation)
    claim_value = _dlq_replay_claim_value(request_id, owner_token)
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                """
                DELETE FROM intake_idempotency_keys
                WHERE route=%s AND idem_key=%s AND request_id=%s
                """,
                (DLQ_REPLAY_CLAIM_ROUTE, claim_key, claim_value),
            )
        conn.commit()


def _normalized_request_status(request_id: str, payload: dict) -> tuple[int, dict]:
    if not isinstance(request_id, str) or not request_id or len(request_id) > 80 or "\x00" in request_id:
        raise ValueError("Invalid request status request_id")
    if not isinstance(payload, dict):
        raise ValueError("Request status must be an object")
    status = dict(payload)
    payload_request_id = status.get("request_id", request_id)
    if payload_request_id != request_id:
        raise ValueError("Request status request_id mismatch")
    actor_id = status.get("actor_id")
    user_id = status.get("user_id")
    for field_name, value in (("actor_id", actor_id), ("user_id", user_id)):
        if value is not None and (
            type(value) is not int
            or value <= 0
            or value > _MAX_POSTGRES_BIGINT
        ):
            raise ValueError(f"Invalid request status {field_name}")
    if actor_id is not None and user_id is not None and actor_id != user_id:
        raise ValueError("Conflicting request status actor_id/user_id")
    owner_id = actor_id if actor_id is not None else user_id
    if owner_id is None:
        raise ValueError("Request status owner is missing")
    status["request_id"] = request_id
    status["user_id"] = owner_id
    status["actor_id"] = owner_id
    validate_json_structure(status, max_depth=MAX_JSON_WIRE_NESTING_DEPTH)
    return owner_id, status


def upsert_request_status(cur, request_id: str, payload: dict) -> dict:
    owner_id, status = _normalized_request_status(request_id, payload)
    cur.execute(
        """
        INSERT INTO request_statuses (request_id, user_id, status_json)
        VALUES (%s, %s, %s::jsonb)
        ON CONFLICT (request_id) DO UPDATE SET
            user_id = EXCLUDED.user_id,
            status_json = EXCLUDED.status_json,
            updated_at = NOW()
        WHERE request_statuses.user_id IS NULL
           OR request_statuses.user_id = EXCLUDED.user_id
        RETURNING user_id
        """,
        (request_id, owner_id, json.dumps(status, allow_nan=False)),
    )
    if cur.fetchone() is None:
        raise RequestStatusOwnerConflict("Request status owner conflict")
    return status


def store_request_status(request_id: str, payload: dict) -> dict:
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            status = upsert_request_status(cur, request_id, payload)
        conn.commit()
    return status


def load_request_status(request_id: str) -> dict | None:
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute(
                "/*NO LOAD BALANCE*/ SELECT status_json FROM request_statuses WHERE request_id=%s",
                (request_id,),
            )
            row = cur.fetchone()
    if row is None:
        return None
    status = row["status_json"]
    if isinstance(status, str):
        return json.loads(status)
    return status
