import base64
import hashlib
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from uuid import uuid4

from kafka.structs import OffsetAndMetadata, TopicPartition
from prometheus_client import start_http_server
from psycopg2 import DataError, InterfaceError, OperationalError

from portfolio.config import settings
from portfolio.db import get_conn, get_cursor, init_pool_with_retry, reconnect_pool
from portfolio.event_envelope import is_generic_event_type, validate_json_structure
from portfolio.kafka_client import (
    InvalidKafkaPayload,
    build_ingress_consumer,
    build_notification_consumer,
    is_invalid_kafka_payload,
    publish_dlq_job,
    publish_notification_job,
)
from portfolio.metrics import (
    dlq_events_total,
    event_persist_lag_seconds,
    health_status,
    notification_publish_failures_total,
    observe_worker_stage,
    queue_wait_seconds,
    registry,
    worker_failures_total,
    worker_last_success_timestamp,
    worker_processed_total,
    worker_processing_seconds,
)
from portfolio.order_events import classify_order_event
from portfolio.state_store import (
    RequestStatusOwnerConflict,
    store_request_status,
    upsert_request_status,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_INGRESS_ROUTE_RE = re.compile(
    r"^POST:/v(?P<version>[12])/(?P<resource>streams|orders)/(?P<stream_id>[1-9][0-9]*)/events$"
)
_MAX_INLINE_RETRY_DELAY_SECONDS = 60.0
_MAX_POSTGRES_BIGINT = 9_223_372_036_854_775_807
_PUBLIC_INGRESS_AUTHORIZATION_REJECTION = "Event authorization rejected"


class RoomSequenceGapError(RuntimeError):
    pass


class NotificationTargetMissing(ValueError):
    pass


class IngressAuthorizationError(ValueError):
    pass


def _reject_ingress_authorization(
    reason: str,
    *,
    request_id: str,
    room_id: int,
    user_id: int,
) -> None:
    logging.warning(
        "Ingress authorization rejected request_id=%s room_id=%s user_id=%s reason=%s",
        request_id,
        room_id,
        user_id,
        reason,
    )
    raise IngressAuthorizationError(reason)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _topic_partition(message) -> TopicPartition:
    return TopicPartition(message.topic, message.partition)


def _commit_processed_record(consumer, message) -> None:
    partition = _topic_partition(message)
    consumer.commit(
        offsets={partition: OffsetAndMetadata(message.offset + 1, "", -1)}
    )


def _seek_failed_record(consumer, message) -> TopicPartition:
    partition = _topic_partition(message)
    consumer.seek(partition, message.offset)
    return partition


def _event_envelope(job_payload: dict) -> tuple[str, dict, dict]:
    schema_version = job_payload.get("schema_version", 1)
    is_generic_envelope = (
        (
            isinstance(schema_version, int)
            and not isinstance(schema_version, bool)
            and schema_version >= 2
        )
        or str(job_payload.get("route", "")).startswith("POST:/v2/")
    )
    if "event_type" in job_payload:
        event_type = job_payload["event_type"]
    elif is_generic_envelope:
        raise ValueError("Generic envelope missing event_type")
    else:
        event_type = "legacy.message"
    if (
        not isinstance(event_type, str)
        or not event_type
        or len(event_type) > 50
        or "\x00" in event_type
    ):
        raise ValueError("Invalid event_type")
    if is_generic_envelope and not is_generic_event_type(event_type):
        raise ValueError("Invalid generic event_type")

    if is_generic_envelope:
        if "payload" not in job_payload:
            raise ValueError("Generic envelope missing payload")
        payload = job_payload["payload"]
        metadata = job_payload["metadata"] if "metadata" in job_payload else {}
    else:
        payload = job_payload.get("payload")
        if payload is None and "body" in job_payload:
            payload = {"text": job_payload["body"]}
        metadata = job_payload.get("metadata")
    if not is_generic_envelope and metadata is None:
        metadata = {}
        if job_payload.get("category") is not None:
            metadata["classification"] = job_payload["category"]
        if job_payload.get("payment_id") is not None:
            metadata["external_references"] = {"payment": job_payload["payment_id"]}
    if not isinstance(payload, dict):
        raise ValueError("Invalid payload")
    if not isinstance(metadata, dict):
        raise ValueError("Invalid metadata")
    validate_json_structure(payload)
    validate_json_structure(metadata)
    try:
        payload_size = len(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        metadata_size = len(
            json.dumps(
                metadata,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Payload and metadata must contain finite JSON values") from exc
    if payload_size > 65_536:
        raise ValueError("Payload exceeds 65536 UTF-8 JSON bytes")
    if metadata_size > 16_384:
        raise ValueError("Metadata exceeds 16384 UTF-8 JSON bytes")
    return event_type, payload, metadata


def _legacy_body_preview(payload: dict) -> str:
    for key in ("message", "text", "description"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value[:1000]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:1000] or "{}"


def _validate_alias_pair(payload: dict, canonical: str, legacy: str) -> None:
    if canonical in payload and legacy in payload:
        canonical_value = payload[canonical]
        legacy_value = payload[legacy]
        if type(canonical_value) is not type(legacy_value) or canonical_value != legacy_value:
            raise ValueError(f"Conflicting {canonical}/{legacy}")


def _compatibility_text(value, *, max_length: int) -> str | None:
    if not isinstance(value, str) or not value or len(value) > max_length:
        return None
    return value


def _validated_iso_timestamp(value, *, field_name: str) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or "\x00" in value
    ):
        raise ValueError(f"Invalid {field_name}")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        if not math.isfinite(parsed.timestamp()):
            raise ValueError
    except (ValueError, OverflowError, OSError):
        raise ValueError(f"Invalid {field_name}") from None
    return value


def _validate_ingress_payload(job_payload: object) -> dict:
    if not isinstance(job_payload, dict):
        raise ValueError("Ingress payload must be a JSON object")
    _validate_alias_pair(job_payload, "stream_id", "room_id")
    _validate_alias_pair(job_payload, "actor_id", "user_id")
    _validate_alias_pair(job_payload, "stream_seq", "room_seq")
    if "room_id" not in job_payload and "stream_id" in job_payload:
        job_payload["room_id"] = job_payload["stream_id"]
    if "user_id" not in job_payload and "actor_id" in job_payload:
        job_payload["user_id"] = job_payload["actor_id"]
    if "room_seq" not in job_payload and "stream_seq" in job_payload:
        job_payload["room_seq"] = job_payload["stream_seq"]
    for field in ("request_id", "route", "room_id", "user_id"):
        if field not in job_payload:
            raise ValueError(f"Ingress payload missing {field}")
    if (
        not isinstance(job_payload["request_id"], str)
        or not job_payload["request_id"]
        or len(job_payload["request_id"]) > 80
        or "\x00" in job_payload["request_id"]
    ):
        raise ValueError("Invalid request_id")
    if (
        not isinstance(job_payload["route"], str)
        or not job_payload["route"]
        or len(job_payload["route"]) > 500
        or "\x00" in job_payload["route"]
    ):
        raise ValueError("Invalid route")
    if (
        isinstance(job_payload["room_id"], bool)
        or not isinstance(job_payload["room_id"], int)
        or job_payload["room_id"] <= 0
        or job_payload["room_id"] > _MAX_POSTGRES_BIGINT
    ):
        raise ValueError("Invalid room_id")
    if (
        isinstance(job_payload["user_id"], bool)
        or not isinstance(job_payload["user_id"], int)
        or job_payload["user_id"] <= 0
        or job_payload["user_id"] > _MAX_POSTGRES_BIGINT
    ):
        raise ValueError("Invalid user_id")
    route_match = _INGRESS_ROUTE_RE.fullmatch(job_payload["route"])
    if route_match is None:
        raise ValueError("Invalid ingress route")
    if int(route_match.group("stream_id")) != job_payload["room_id"]:
        raise ValueError("Ingress route stream does not match room_id")
    is_v2_route = route_match.group("version") == "2"
    if is_v2_route and route_match.group("resource") != "streams":
        raise ValueError("V2 ingress route must use streams")
    if is_v2_route and "schema_version" not in job_payload:
        raise ValueError("V2 ingress missing schema_version")
    schema_version = job_payload.get("schema_version", 1)
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or not 1 <= schema_version <= 32_767
    ):
        raise ValueError("Invalid schema_version")
    if is_v2_route and schema_version != 2:
        raise ValueError("V2 ingress requires schema_version 2")
    job_payload["schema_version"] = schema_version
    if schema_version >= 2:
        if "payload" not in job_payload:
            raise ValueError("Generic ingress missing payload")
    elif "payload" not in job_payload and "body" not in job_payload:
        raise ValueError("Ingress payload missing payload")
    event_type, payload, metadata = _event_envelope(job_payload)
    job_payload["event_type"] = event_type
    job_payload["payload"] = payload
    job_payload["metadata"] = metadata
    job_payload["body"] = _legacy_body_preview(payload)
    idem_key = job_payload.get("x_idempotency_key")
    if idem_key is not None and (
        not isinstance(idem_key, str)
        or not idem_key
        or len(idem_key) > 128
        or "\x00" in idem_key
    ):
        raise ValueError("Invalid x_idempotency_key")
    room_seq = job_payload.get("room_seq")
    if room_seq is not None and (
        isinstance(room_seq, bool)
        or not isinstance(room_seq, int)
        or room_seq <= 0
        or room_seq > _MAX_POSTGRES_BIGINT
    ):
        raise ValueError("Invalid room_seq")
    for counter_field in ("retry_count", "replay_count"):
        counter = job_payload.get(counter_field, 0)
        if (
            isinstance(counter, bool)
            or not isinstance(counter, int)
            or counter < 0
            or counter > _MAX_POSTGRES_BIGINT
        ):
            raise ValueError(f"Invalid {counter_field}")
    next_retry_at = job_payload.get("next_retry_at")
    if next_retry_at is not None:
        raise ValueError("Ingress next_retry_at must be null")
    queued_at = _validated_iso_timestamp(
        job_payload.get("queued_at"),
        field_name="queued_at",
    )
    replayed_at = _validated_iso_timestamp(
        job_payload.get("replayed_at"),
        field_name="replayed_at",
    )
    payment_id = job_payload.get("payment_id")
    if payment_id is not None and (
        not isinstance(payment_id, str)
        or len(payment_id) > 80
        or "\x00" in payment_id
    ):
        raise ValueError("Invalid payment_id")

    normalized = {
        "request_id": job_payload["request_id"],
        "route": job_payload["route"],
        "room_id": job_payload["room_id"],
        "user_id": job_payload["user_id"],
        "room_seq": room_seq,
        "schema_version": schema_version,
        "event_type": event_type,
        "payload": payload,
        "metadata": metadata,
        "body": job_payload["body"],
        "x_idempotency_key": idem_key,
        "retry_count": job_payload.get("retry_count", 0),
        "replay_count": job_payload.get("replay_count", 0),
        "next_retry_at": None,
    }
    if queued_at is not None:
        normalized["queued_at"] = queued_at
    if replayed_at is not None:
        normalized["replayed_at"] = replayed_at
    if payment_id is not None:
        normalized["payment_id"] = payment_id
    return normalized


def _safe_nonnegative_int(value) -> int:
    if type(value) is not int or value < 0 or value > _MAX_POSTGRES_BIGINT:
        return 0
    return value


def move_invalid_ingress_to_dlq(raw, reason: str) -> None:
    source = raw if isinstance(raw, dict) else {}
    request_id = source.get("request_id")
    if (
        not isinstance(request_id, str)
        or not request_id
        or len(request_id) > 80
        or "\x00" in request_id
    ):
        request_id = f"invalid-{uuid4()}"
    room_id = source.get("room_id")
    if (
        isinstance(room_id, bool)
        or not isinstance(room_id, int)
        or room_id <= 0
        or room_id > _MAX_POSTGRES_BIGINT
    ):
        room_id = 0
    user_id = source.get("user_id")
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= 0
        or user_id > _MAX_POSTGRES_BIGINT
    ):
        user_id = None

    try:
        if isinstance(raw, bytes):
            raw_bytes = raw
        elif isinstance(raw, str):
            raw_bytes = raw.encode("utf-8", errors="replace")
        else:
            raw_bytes = json.dumps(
                raw,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raw_bytes = f"<{type(raw).__name__}:unserializable>".encode("ascii")

    diagnostic_source = (
        "raw_input_bytes" if isinstance(raw, (bytes, str)) else "normalized_json"
    )
    diagnostic_size_bytes = len(raw_bytes)
    diagnostic_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    diagnostic_preview = raw_bytes[:1024]
    if isinstance(source, InvalidKafkaPayload):
        marker_size = source.get("raw_size")
        marker_sha256 = source.get("raw_sha256")
        marker_preview = source.get("raw_base64")
        if type(marker_size) is int and marker_size >= 0:
            diagnostic_size_bytes = marker_size
        if (
            isinstance(marker_sha256, str)
            and len(marker_sha256) == 64
            and all(character in "0123456789abcdef" for character in marker_sha256)
        ):
            diagnostic_sha256 = marker_sha256
        if isinstance(marker_preview, str):
            try:
                diagnostic_preview = base64.b64decode(marker_preview, validate=True)[:1024]
            except (ValueError, base64.binascii.Error):
                pass
        diagnostic_source = "kafka_raw_bytes"

    safe_reason = str(reason).replace("\x00", "\\0")[:300]
    payload = {
        "request_id": request_id,
        "room_id": room_id,
        "user_id": user_id,
        "__invalid_kafka_payload__": True,
        "failed_reason": f"invalid_ingress:{safe_reason}",
        "failed_at": now_iso(),
        "retry_count": _safe_nonnegative_int(source.get("retry_count", 0)),
        "replay_count": _safe_nonnegative_int(source.get("replay_count", 0)),
        "diagnostic_source": diagnostic_source,
        "diagnostic_size_bytes": diagnostic_size_bytes,
        "diagnostic_sha256": diagnostic_sha256,
        "diagnostic_preview_base64": base64.b64encode(diagnostic_preview).decode("ascii"),
    }
    publish_dlq_job(room_id, payload)
    dlq_events_total.labels(reason="invalid_ingress").inc()
    if payload.get("user_id") is not None:
        failure_status = {
            "request_id": payload["request_id"],
            "status": "failed_dlq",
            "user_id": payload["user_id"],
            "failed_reason": payload["failed_reason"],
            "failed_at": payload["failed_at"],
        }
        if payload["room_id"] > 0:
            failure_status["room_id"] = payload["room_id"]
        update_request_status(
            payload["request_id"],
            failure_status,
        )


def _message_row_response(row: dict, persisted_at: str | None = None) -> dict:
    created_at = row["created_at"]
    created_at_iso = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    response = {
        "id": row["id"],
        "request_id": row["request_id"],
        "status": "persisted",
        "room_id": row["room_id"],
        "room_seq": row["room_seq"],
        "user_id": row["user_id"],
        "actor_id": row["user_id"],
        "body": row.get("body", ""),
        "event_type": row.get("event_type") or "legacy.message",
        "category": row.get("category"),
        "payment_id": row.get("payment_id"),
        "schema_version": int(row.get("schema_version") or 1),
        "payload": (
            row.get("payload")
            if row.get("payload") is not None
            else {"text": row.get("body", "")}
        ),
        "metadata": row.get("metadata") or {},
        "created_at": created_at_iso,
    }
    if persisted_at is not None:
        response["persisted_at"] = persisted_at
    return response


def _validated_idempotency_response(
    cached_row,
    *,
    expected_user_id: int,
    expected_room_id: int,
) -> dict | None:
    if not isinstance(cached_row, dict) or not isinstance(
        cached_row.get("response_json"), dict
    ):
        return None
    response = dict(cached_row["response_json"])

    for field in ("id", "room_id", "room_seq", "user_id"):
        value = response.get(field)
        if type(value) is not int or value <= 0 or value > _MAX_POSTGRES_BIGINT:
            logging.warning("Ignoring malformed idempotency response: invalid %s", field)
            return None
    if response["user_id"] != expected_user_id or response["room_id"] != expected_room_id:
        return None

    actor_id = response.get("actor_id")
    if actor_id is not None and (
        type(actor_id) is not int
        or actor_id <= 0
        or actor_id > _MAX_POSTGRES_BIGINT
        or actor_id != response["user_id"]
    ):
        logging.warning("Ignoring malformed idempotency response: conflicting actor owner")
        return None

    request_id = response.get("request_id")
    if (
        not isinstance(request_id, str)
        or not request_id
        or len(request_id) > 80
        or "\x00" in request_id
    ):
        logging.warning("Ignoring malformed idempotency response: invalid request_id")
        return None
    if not isinstance(response.get("body"), str) or len(response["body"]) > 1000:
        logging.warning("Ignoring malformed idempotency response: invalid body")
        return None
    if not isinstance(response.get("created_at"), str) or not response["created_at"]:
        logging.warning("Ignoring malformed idempotency response: invalid created_at")
        return None

    schema_version = response.get("schema_version", 1)
    if type(schema_version) is not int or not 1 <= schema_version <= 32_767:
        logging.warning("Ignoring malformed idempotency response: invalid schema_version")
        return None
    try:
        _event_envelope(response)
    except (TypeError, ValueError, RecursionError) as exc:
        logging.warning("Ignoring malformed idempotency response envelope: %s", exc)
        return None
    return response


def _persist_message_with_cursor(job_payload: dict, cur) -> dict:
    route = job_payload["route"]
    request_id = job_payload["request_id"]
    room_id = job_payload["room_id"]
    user_id = job_payload["user_id"]
    event_type, event_payload, event_metadata = _event_envelope(job_payload)
    body = _legacy_body_preview(event_payload)
    category = None
    payment_id = None
    if route.startswith("POST:/v1/orders/"):
        # The deprecated reference adapter owns this classification contract.
        # Recompute it at the persistence boundary so a forged legacy Kafka
        # payload cannot write an arbitrary order classification.
        category = classify_order_event(event_type)
        event_metadata = dict(event_metadata)
        event_metadata["classification"] = category
        external_references = event_metadata.get("external_references")
        payment_id = _compatibility_text(job_payload.get("payment_id"), max_length=80)
        if payment_id is None and isinstance(external_references, dict):
            payment_id = _compatibility_text(external_references.get("payment"), max_length=80)
    schema_version = int(job_payload.get("schema_version", 1))
    room_seq_raw = job_payload.get("room_seq")
    room_seq = int(room_seq_raw) if room_seq_raw is not None else None
    x_idempotency_key = job_payload.get("x_idempotency_key")
    idempotency_route = f"{route}|actor:{user_id}"

    if x_idempotency_key:
        # Kafka ordering is scoped to a stream partition, while a caller can
        # accidentally reuse an idempotency key across concurrent requests.
        # Serialize that key in PostgreSQL before reading or writing its final
        # response so two partitions cannot persist two messages and race the
        # idempotency row's ON CONFLICT update.
        advisory_lock_key = json.dumps(
            [idempotency_route, x_idempotency_key],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        cur.execute(
            "/*NO LOAD BALANCE*/ SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (advisory_lock_key,),
        )
        cur.execute(
            "/*NO LOAD BALANCE*/ SELECT response_json FROM idempotency_keys WHERE route=%s AND idem_key=%s",
            (idempotency_route, x_idempotency_key),
        )
        cached = cur.fetchone()
        response = _validated_idempotency_response(
            cached,
            expected_user_id=user_id,
            expected_room_id=room_id,
        )
        if response is not None:
            response["_idempotency_hit"] = True
            return response

        # Rows written before actor-scoped keys were introduced used the plain
        # route. Adopt only a result owned by this actor, then seed the scoped
        # key so retries remain safe after the compatibility window.
        cur.execute(
            "/*NO LOAD BALANCE*/ SELECT response_json FROM idempotency_keys WHERE route=%s AND idem_key=%s",
            (route, x_idempotency_key),
        )
        legacy_cached = cur.fetchone()
        response = _validated_idempotency_response(
            legacy_cached,
            expected_user_id=user_id,
            expected_room_id=room_id,
        )
        if response is not None:
            cur.execute(
                """
                INSERT INTO idempotency_keys (route, idem_key, response_json)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (route, idem_key) DO NOTHING
                """,
                (idempotency_route, x_idempotency_key, json.dumps(response)),
            )
            response["_idempotency_hit"] = True
            return response

    cur.execute(
        """
        /*NO LOAD BALANCE*/
        SELECT id, request_id, room_id, user_id, event_type, category, payment_id,
               schema_version, payload, metadata, body, room_seq, created_at
        FROM messages
        WHERE request_id=%s
        """,
        (request_id,),
    )
    existing = cur.fetchone()
    if existing is not None:
        if existing["room_id"] != room_id or existing["user_id"] != user_id:
            _reject_ingress_authorization(
                "request_identity_conflict",
                request_id=request_id,
                room_id=room_id,
                user_id=user_id,
            )
        return _message_row_response(existing)

    cur.execute("/*NO LOAD BALANCE*/ SELECT id FROM rooms WHERE id=%s", (room_id,))
    if cur.fetchone() is None:
        _reject_ingress_authorization(
            "stream_not_found",
            request_id=request_id,
            room_id=room_id,
            user_id=user_id,
        )

    cur.execute("/*NO LOAD BALANCE*/ SELECT id FROM users WHERE id=%s", (user_id,))
    if cur.fetchone() is None:
        _reject_ingress_authorization(
            "actor_not_found",
            request_id=request_id,
            room_id=room_id,
            user_id=user_id,
        )

    cur.execute(
        "/*NO LOAD BALANCE*/ SELECT 1 FROM room_members WHERE room_id=%s AND user_id=%s",
        (room_id, user_id),
    )
    if cur.fetchone() is None:
        _reject_ingress_authorization(
            "membership_missing",
            request_id=request_id,
            room_id=room_id,
            user_id=user_id,
        )

    cur.execute(
        """
        INSERT INTO room_sequences (room_id, last_seq)
        VALUES (%s, 0)
        ON CONFLICT (room_id) DO NOTHING
        """,
        (room_id,),
    )
    cur.execute(
        "/*NO LOAD BALANCE*/ SELECT last_seq FROM room_sequences WHERE room_id=%s FOR UPDATE",
        (room_id,),
    )
    seq_row = cur.fetchone()
    last_seq = int(seq_row["last_seq"])

    expected_seq = last_seq + 1
    if room_seq is None:
        room_seq = expected_seq

    if room_seq <= last_seq:
        cur.execute(
            """
            /*NO LOAD BALANCE*/
            SELECT id, request_id, room_id, user_id, event_type, category, payment_id,
                   schema_version, payload, metadata, body, room_seq, created_at
            FROM messages
            WHERE room_id=%s AND room_seq=%s
            """,
            (room_id, room_seq),
        )
        duplicate = cur.fetchone()
        if duplicate is not None:
            if duplicate["request_id"] == request_id:
                return _message_row_response(duplicate)
            raise ValueError(
                f"Room sequence conflict room_id={room_id} room_seq={room_seq}"
            )
        raise ValueError(f"Room sequence is stale room_id={room_id} room_seq={room_seq}")

    if room_seq > expected_seq:
        raise RoomSequenceGapError(
            f"Room sequence gap detected expected={expected_seq} got={room_seq}"
        )

    cur.execute(
        """
        INSERT INTO messages (
            request_id, room_id, user_id, event_type, category, payment_id,
            schema_version, payload, metadata, body, room_seq
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        RETURNING id, request_id, room_id, user_id, event_type, category, payment_id,
                  schema_version, payload, metadata, body, room_seq, created_at
        """,
        (
            request_id,
            room_id,
            user_id,
            event_type,
            category,
            payment_id,
            schema_version,
            json.dumps(event_payload, ensure_ascii=False),
            json.dumps(event_metadata, ensure_ascii=False),
            body,
            room_seq,
        ),
    )
    message = cur.fetchone()
    cur.execute(
        """
        UPDATE room_sequences
        SET last_seq=%s, updated_at=NOW()
        WHERE room_id=%s
        """,
        (room_seq, room_id),
    )

    response = _message_row_response(message)

    if x_idempotency_key:
        cur.execute(
            """
            INSERT INTO idempotency_keys (route, idem_key, response_json)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (route, idem_key)
            DO UPDATE SET response_json = EXCLUDED.response_json
            """,
            (idempotency_route, x_idempotency_key, json.dumps(response)),
        )

    return response


def persisted_status_payload(request_id: str, response: dict) -> dict:
    event_type, event_payload, event_metadata = _event_envelope(response)
    body = response.get("body") or _legacy_body_preview(event_payload)
    payload = {
        "request_id": request_id,
        "status": "persisted",
        "message_id": response["id"],
        "room_id": response["room_id"],
        "room_seq": response["room_seq"],
        "user_id": response["user_id"],
        "actor_id": response["user_id"],
        "body": body,
        "created_at": response["created_at"],
    }
    if response.get("persisted_at") is not None:
        payload["persisted_at"] = response["persisted_at"]
    payload.update(
        {
            "event_type": event_type,
            "category": response.get("category"),
            "payment_id": response.get("payment_id"),
            "schema_version": int(response.get("schema_version") or 1),
            "payload": event_payload,
            "metadata": event_metadata,
        }
    )
    return payload


def notification_attempt_payload(message_response: dict) -> dict:
    event_type, event_payload, event_metadata = _event_envelope(message_response)
    payload = {
        "event_id": message_response["id"],
        "stream_id": message_response["room_id"],
        "message_id": message_response["id"],
        "room_id": message_response["room_id"],
        "event_type": event_type,
        "payload_preview": _legacy_body_preview(event_payload)[:120],
        "metadata": event_metadata,
        "body_preview": _legacy_body_preview(event_payload)[:30],
    }
    return payload


def insert_notification_attempt(cur, payload: dict) -> None:
    event_id = payload.get("event_id", payload.get("message_id"))
    stream_id = payload.get("stream_id", payload.get("room_id"))
    cur.execute(
        """
        WITH target AS (
            SELECT id
            FROM messages
            WHERE id=%s AND room_id=%s
        ), inserted AS (
            INSERT INTO notification_attempts (message_id, room_id, payload)
            SELECT %s, %s, %s::jsonb
            FROM target
            ON CONFLICT (message_id) DO NOTHING
            RETURNING id
        )
        SELECT
            EXISTS (SELECT 1 FROM target) AS target_exists,
            EXISTS (SELECT 1 FROM inserted) AS inserted
        """,
        (
            event_id,
            stream_id,
            event_id,
            stream_id,
            json.dumps(payload, allow_nan=False),
        ),
    )
    result = cur.fetchone()
    if not result or result.get("target_exists") is not True:
        raise NotificationTargetMissing("Notification target event is missing")


def update_request_status(request_id: str, payload: dict) -> None:
    with observe_worker_stage("request_status_update"):
        try:
            store_request_status(request_id, payload)
        except RequestStatusOwnerConflict:
            logging.warning(
                "Skipped request status owner conflict request_id=%s",
                request_id,
            )
            return
        except Exception as exc:  # noqa: BLE001
            logging.warning("Failed to store request status in PostgreSQL request_id=%s error=%s", request_id, exc)


def move_to_dlq(job_payload: dict, reason: str) -> None:
    request_id = job_payload["request_id"]
    job_payload["failed_reason"] = reason
    job_payload["failed_at"] = now_iso()
    publish_dlq_job(job_payload["room_id"], job_payload)
    dlq_events_total.labels(reason=reason).inc()
    update_request_status(
        request_id,
        {
            "request_id": request_id,
            "status": "failed_dlq",
            "room_id": job_payload.get("room_id"),
            "user_id": job_payload.get("user_id"),
            "failed_reason": reason,
            "retry_count": int(job_payload.get("retry_count", 0)),
            "failed_at": job_payload["failed_at"],
        },
    )


def mark_inline_retry(job_payload: dict) -> float:
    retry_count = int(job_payload.get("retry_count", 0)) + 1
    base_delay = float(settings.ingress_retry_base_delay_seconds)
    if not math.isfinite(base_delay) or base_delay <= 0:
        logging.error(
            "Invalid INGRESS_RETRY_BASE_DELAY_SECONDS=%r; using 1 second",
            settings.ingress_retry_base_delay_seconds,
        )
        base_delay = 1.0
    exponent = min(max(retry_count - 1, 0), 20)
    delay = min(base_delay * (2**exponent), _MAX_INLINE_RETRY_DELAY_SECONDS)
    job_payload["retry_count"] = retry_count
    job_payload["next_retry_at"] = time.time() + delay
    try:
        update_request_status(
            job_payload["request_id"],
            {
                "request_id": job_payload["request_id"],
                "status": "queued",
                "room_id": job_payload.get("room_id"),
                "user_id": job_payload.get("user_id"),
                "room_seq": job_payload.get("room_seq"),
                "retry_count": retry_count,
                "next_retry_at": datetime.fromtimestamp(
                    float(job_payload["next_retry_at"]), tz=timezone.utc
                ).isoformat(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logging.warning("Failed to update inline retry status request_id=%s error=%s", job_payload["request_id"], exc)
    return delay


def store_attempt(payload: dict) -> None:
    last_error = None
    for attempt in range(2):
        try:
            with observe_worker_stage("notification_db_insert"):
                with get_conn() as conn:
                    with get_cursor(conn) as cur:
                        insert_notification_attempt(cur, payload)
                    conn.commit()
            return
        except (DataError, NotificationTargetMissing):
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == 0:
                reconnect_pool()
                time.sleep(1)
                continue
            raise last_error


def persist_ingress_job(job_payload: dict) -> dict:
    request_id = job_payload["request_id"]

    with get_conn() as conn:
        with get_cursor(conn) as cur:
            with observe_worker_stage("db_persist"):
                response = _persist_message_with_cursor(job_payload, cur)

            response["persisted_at"] = now_iso()
            status_payload = persisted_status_payload(request_id, response)
            with observe_worker_stage("request_status_update"):
                upsert_request_status(cur, request_id, status_payload)

        conn.commit()

    # The durable status row is written inside the transaction. The returned
    # timestamp is refreshed after commit() so the Worker histogram measures
    # the actual commit-observed boundary without a second state publish.
    response["persisted_at"] = now_iso()
    if not response.get("_idempotency_hit"):
        try:
            with observe_worker_stage("notification_publish"):
                publish_notification_job(response["room_id"], notification_attempt_payload(response))
        except Exception as exc:  # noqa: BLE001
            notification_publish_failures_total.inc()
            logging.exception(
                "Notification publish failed after PostgreSQL commit; core persistence remains committed "
                "request_id=%s message_id=%s error=%s",
                request_id,
                response["id"],
                exc,
            )
    response.pop("_idempotency_hit", None)
    return response


_KAFKA_KEY_UNSET = object()


def handle_ingress_job(raw: str, *, kafka_key=_KAFKA_KEY_UNSET) -> str:
    try:
        decoded = raw if isinstance(raw, dict) else json.loads(raw)
        if is_invalid_kafka_payload(decoded):
            raise ValueError("Kafka payload is marked as invalid")
        job_payload = _validate_ingress_payload(decoded)
    except (ValueError, TypeError, json.JSONDecodeError, RecursionError) as exc:
        move_invalid_ingress_to_dlq(raw, str(exc))
        return "dlq"
    if kafka_key is not _KAFKA_KEY_UNSET:
        if isinstance(kafka_key, bytes):
            try:
                normalized_kafka_key = kafka_key.decode("utf-8")
            except UnicodeDecodeError:
                normalized_kafka_key = None
        elif isinstance(kafka_key, (str, int)) and not isinstance(kafka_key, bool):
            normalized_kafka_key = str(kafka_key)
        else:
            normalized_kafka_key = None
        if normalized_kafka_key != str(job_payload["room_id"]):
            move_invalid_ingress_to_dlq(job_payload, "kafka_key_stream_id_mismatch")
            return "dlq"
    request_id = job_payload["request_id"]
    queued_at = job_payload.get("queued_at")

    if queued_at:
        try:
            enqueued_at = datetime.fromisoformat(str(queued_at)).timestamp()
            queue_wait_seconds.observe(max(0, time.time() - enqueued_at))
        except Exception:  # noqa: BLE001
            pass

    while True:
        next_retry_at = job_payload.get("next_retry_at")
        if next_retry_at is not None and float(next_retry_at) > time.time():
            time.sleep(
                min(
                    max(0, float(next_retry_at) - time.time()),
                    _MAX_INLINE_RETRY_DELAY_SECONDS,
                )
            )

        try:
            response = persist_ingress_job(job_payload)
            if queued_at:
                try:
                    accepted_at = datetime.fromisoformat(str(queued_at)).timestamp()
                    persisted_at = datetime.fromisoformat(
                        str(response["persisted_at"])
                    ).timestamp()
                    event_persist_lag_seconds.observe(max(0, persisted_at - accepted_at))
                except Exception:  # noqa: BLE001
                    pass
            return "success"
        except IngressAuthorizationError:
            update_request_status(
                request_id,
                {
                    "request_id": request_id,
                    "status": "failed",
                    "room_id": job_payload.get("room_id"),
                    "user_id": job_payload.get("user_id"),
                    "failed_reason": _PUBLIC_INGRESS_AUTHORIZATION_REJECTION,
                },
            )
            return "rejected"
        except ValueError as exc:
            update_request_status(
                request_id,
                {
                    "request_id": request_id,
                    "status": "failed",
                    "room_id": job_payload.get("room_id"),
                    "user_id": job_payload.get("user_id"),
                    "failed_reason": str(exc),
                },
            )
            return "rejected"
        except DataError as exc:
            move_to_dlq(job_payload, f"invalid_persistence_data:{type(exc).__name__}")
            return "dlq"
        except RoomSequenceGapError:
            retry_count = int(job_payload.get("retry_count", 0))
            if retry_count >= settings.ingress_max_retries:
                move_to_dlq(job_payload, "room_sequence_gap")
                return "dlq"
            delay = mark_inline_retry(job_payload)
            time.sleep(delay)
        except (OperationalError, InterfaceError, RuntimeError) as exc:
            retry_count = int(job_payload.get("retry_count", 0))
            if retry_count >= settings.ingress_max_retries:
                move_to_dlq(job_payload, f"transient_error_max_retries:{type(exc).__name__}")
                return "dlq"

            delay = mark_inline_retry(job_payload)
            try:
                reconnect_pool()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(delay)


def _normalize_notification_payload(payload: object) -> dict:
    if not isinstance(payload, dict) or is_invalid_kafka_payload(payload):
        raise ValueError("Notification payload must be a valid JSON object")
    _validate_alias_pair(payload, "event_id", "message_id")
    _validate_alias_pair(payload, "stream_id", "room_id")
    event_id = payload.get("event_id", payload.get("message_id"))
    stream_id = payload.get("stream_id", payload.get("room_id"))
    for field_name, value in (("event_id", event_id), ("stream_id", stream_id)):
        if (
            type(value) is not int
            or value <= 0
            or value > _MAX_POSTGRES_BIGINT
        ):
            raise ValueError(f"Invalid notification {field_name}")

    event_type = payload.get("event_type", "legacy.message")
    if (
        not isinstance(event_type, str)
        or not event_type
        or len(event_type) > 50
        or "\x00" in event_type
    ):
        raise ValueError("Invalid notification event_type")
    body_preview = payload.get("body_preview", "")
    payload_preview = payload.get("payload_preview", body_preview)
    if (
        not isinstance(body_preview, str)
        or len(body_preview) > 30
        or "\x00" in body_preview
    ):
        raise ValueError("Invalid notification body_preview")
    if (
        not isinstance(payload_preview, str)
        or len(payload_preview) > 120
        or "\x00" in payload_preview
    ):
        raise ValueError("Invalid notification payload_preview")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Invalid notification metadata")
    validate_json_structure(metadata)
    try:
        metadata_size = len(
            json.dumps(
                metadata,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Invalid notification metadata") from exc
    if metadata_size > 16_384:
        raise ValueError("Notification metadata exceeds 16384 UTF-8 JSON bytes")
    return {
        "event_id": event_id,
        "stream_id": stream_id,
        "message_id": event_id,
        "room_id": stream_id,
        "event_type": event_type,
        "payload_preview": payload_preview,
        "metadata": metadata,
        "body_preview": body_preview,
    }


def handle_notification_job(raw: str) -> str:
    try:
        payload = raw if isinstance(raw, dict) else json.loads(raw)
        payload = _normalize_notification_payload(payload)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        logging.error("Notification payload rejected because it is not valid JSON")
        return "rejected"
    logging.info(
        "Notification processed event_id=%s stream_id=%s preview=%s",
        payload.get("event_id"),
        payload.get("stream_id"),
        payload.get("payload_preview") or payload.get("body_preview"),
    )
    try:
        store_attempt(payload)
    except NotificationTargetMissing:
        logging.warning(
            "Notification skipped because persisted target is missing event_id=%s stream_id=%s",
            payload["event_id"],
            payload["stream_id"],
        )
        return "rejected"
    except DataError as exc:
        logging.error("Notification payload rejected by PostgreSQL: %s", type(exc).__name__)
        return "rejected"
    return "success"


def _handle_ingress_record(message) -> str:
    return handle_ingress_job(message.value, kafka_key=message.key)


def _process_worker_batch(
    consumer,
    records: dict,
    handler,
    worker_name: str,
    *,
    pass_message: bool = False,
) -> None:
    failed_partitions: set[TopicPartition] = set()

    for messages in records.values():
        for message in messages:
            partition = _topic_partition(message)
            if partition in failed_partitions:
                continue

            started_at = time.perf_counter()
            try:
                outcome = handler(message if pass_message else message.value)
                _commit_processed_record(consumer, message)
            except Exception as exc:  # noqa: BLE001
                failed_partitions.add(partition)
                try:
                    _seek_failed_record(consumer, message)
                except Exception:  # noqa: BLE001
                    logging.exception(
                        "Kafka %s worker failed to seek topic=%s partition=%s offset=%s",
                        worker_name,
                        message.topic,
                        message.partition,
                        message.offset,
                    )
                    raise
                worker_processed_total.labels(result="failure").inc()
                worker_failures_total.inc()
                health_status.labels(component="worker").set(0)
                logging.exception(
                    "Kafka %s worker failed; partition rewound topic=%s partition=%s "
                    "offset=%s error=%s",
                    worker_name,
                    message.topic,
                    message.partition,
                    message.offset,
                    exc,
                )
                time.sleep(1)
            else:
                result = str(outcome or "success")
                worker_processed_total.labels(result=result).inc()
                if result == "success":
                    worker_last_success_timestamp.set(time.time())
                if not failed_partitions:
                    health_status.labels(component="worker").set(1)
            finally:
                worker_processing_seconds.observe(time.perf_counter() - started_at)


def run_kafka_notification_loop() -> None:
    health_status.labels(component="worker").set(1)
    logging.info(
        "Worker started with Kafka notifications. topic=%s group=%s metrics_port=%s",
        settings.kafka_notification_topic,
        settings.kafka_notification_consumer_group,
        settings.worker_metrics_port,
    )

    while True:
        try:
            consumer = build_notification_consumer()
        except Exception as exc:  # noqa: BLE001
            worker_failures_total.inc()
            health_status.labels(component="worker").set(0)
            logging.exception("Kafka notification consumer init failed: %s", exc)
            time.sleep(2)
            continue

        try:
            while True:
                records = consumer.poll(timeout_ms=1000, max_records=20)
                if not records:
                    continue
                _process_worker_batch(
                    consumer,
                    records,
                    handle_notification_job,
                    "notification",
                )
        except Exception as exc:  # noqa: BLE001
            worker_failures_total.inc()
            health_status.labels(component="worker").set(0)
            logging.exception("Kafka notification loop failed: %s", exc)
            try:
                consumer.close()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)


def run_kafka_worker_loop() -> None:
    health_status.labels(component="worker").set(1)
    logging.info(
        "Worker started with Kafka ingress. topic=%s group=%s dlq_topic=%s metrics_port=%s",
        settings.kafka_ingress_topic,
        settings.kafka_consumer_group,
        settings.kafka_dlq_topic,
        settings.worker_metrics_port,
    )

    while True:
        try:
            consumer = build_ingress_consumer()
        except Exception as exc:  # noqa: BLE001
            worker_failures_total.inc()
            health_status.labels(component="worker").set(0)
            logging.exception("Kafka consumer init failed: %s", exc)
            time.sleep(2)
            continue

        try:
            while True:
                records = consumer.poll(timeout_ms=1000, max_records=20)
                if not records:
                    continue
                _process_worker_batch(
                    consumer,
                    records,
                    _handle_ingress_record,
                    "ingress",
                    pass_message=True,
                )
        except Exception as exc:  # noqa: BLE001
            worker_failures_total.inc()
            health_status.labels(component="worker").set(0)
            logging.exception("Kafka consumer loop failed: %s", exc)
            try:
                consumer.close()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)


def main() -> None:
    init_pool_with_retry(settings.startup_retries, settings.startup_retry_delay)
    start_http_server(settings.worker_metrics_port, registry=registry)
    if settings.worker_mode == "notification":
        run_kafka_notification_loop()
        return
    run_kafka_worker_loop()


if __name__ == "__main__":
    main()
