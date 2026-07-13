import copy
import heapq
import json
import logging
import math
import threading
import time
from collections import OrderedDict
from datetime import datetime

from portfolio.config import settings
from portfolio.event_envelope import (
    MAX_JSON_WIRE_NESTING_DEPTH,
    is_generic_event_type,
    validate_json_structure,
)
from portfolio.kafka_client import build_materialized_cache_consumer, is_invalid_kafka_payload

_request_status_cache: OrderedDict[str, dict] = OrderedDict()
_message_snapshot_cache: OrderedDict[int, dict] = OrderedDict()
_stream_message_updated_at: dict[int, float] = {}
_message_snapshot_epochs: dict[int, float] = {}
_message_snapshot_id_heap: list[int] = []
_stream_message_counts: dict[int, int] = {}
_stream_membership_cache: OrderedDict[int, set[int]] = OrderedDict()
_cache_lock = threading.RLock()
_stop_event = threading.Event()
_cache_thread: threading.Thread | None = None
_cache_ready = False
_cache_hydrated = False
_cache_last_error: str | None = None
_MAX_POSTGRES_BIGINT = 9_223_372_036_854_775_807
_MAX_TIMESTAMP_FUTURE_SKEW_SECONDS = 300


def _positive_bigint(value, *, field_name: str) -> int:
    if type(value) is not int or value <= 0 or value > _MAX_POSTGRES_BIGINT:
        raise ValueError(f"Invalid {field_name}")
    return value


def _request_status_key(key: str) -> str:
    if not isinstance(key, str) or not key or len(key) > 80 or "\x00" in key:
        raise ValueError("Invalid request status key")
    return key


def _bounded_text(
    value,
    *,
    field_name: str,
    max_length: int,
    allow_empty: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or (not allow_empty and not value)
        or len(value) > max_length
        or "\x00" in value
    ):
        raise ValueError(f"Invalid {field_name}")
    return value


def _optional_text(
    value,
    *,
    field_name: str,
    max_length: int,
    allow_empty: bool = False,
) -> str | None:
    if value is None:
        return None
    return _bounded_text(
        value,
        field_name=field_name,
        max_length=max_length,
        allow_empty=allow_empty,
    )


def _optional_alias_bigint(
    payload: dict,
    canonical: str,
    legacy: str,
    *,
    field_name: str,
) -> int | None:
    present = [field for field in (canonical, legacy) if field in payload]
    if not present:
        return None
    raw_values = [payload[field] for field in present]
    if all(value is None for value in raw_values):
        return None
    if any(value is None for value in raw_values):
        raise ValueError(f"Conflicting {canonical}/{legacy}")
    values = [
        _positive_bigint(payload[field], field_name=f"{field_name} {field}")
        for field in present
    ]
    if len(set(values)) != 1:
        raise ValueError(f"Conflicting {canonical}/{legacy}")
    return values[0]


def _optional_nonnegative_int(value, *, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0 or value > _MAX_POSTGRES_BIGINT:
        raise ValueError(f"Invalid {field_name}")
    return value


def _iso_timestamp(value, *, field_name: str) -> str:
    timestamp = _bounded_text(value, field_name=field_name, max_length=64)
    try:
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        timestamp_epoch = parsed.timestamp()
        if (
            not math.isfinite(timestamp_epoch)
            or timestamp_epoch > time.time() + _MAX_TIMESTAMP_FUTURE_SKEW_SECONDS
        ):
            raise ValueError
    except (ValueError, OverflowError, OSError):
        raise ValueError(f"Invalid {field_name}") from None
    return timestamp


def _optional_iso_timestamp(value, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _iso_timestamp(value, field_name=field_name)


def _json_object(value, *, field_name: str, max_bytes: int) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    validate_json_structure(value, max_depth=MAX_JSON_WIRE_NESTING_DEPTH)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"Invalid {field_name}") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{field_name} exceeds {max_bytes} UTF-8 JSON bytes")
    return json.loads(encoded)


def _numeric_topic_key(key: str, *, field_name: str) -> int:
    if not isinstance(key, str) or not key or not key.isascii() or not key.isdecimal():
        raise ValueError(f"Invalid {field_name} key")
    value = _positive_bigint(int(key), field_name=field_name)
    if key != str(value):
        raise ValueError(f"Non-canonical {field_name} key")
    return value


def _normalize_request_status_record(key: str, payload: dict) -> dict:
    request_id = _request_status_key(key)
    if not isinstance(payload, dict):
        raise ValueError("Request status must be an object")
    validate_json_structure(payload, max_depth=MAX_JSON_WIRE_NESTING_DEPTH)
    if payload.get("request_id") != request_id:
        raise ValueError("Request status key/payload mismatch")
    owner_id = _optional_alias_bigint(
        payload,
        "actor_id",
        "user_id",
        field_name="request status owner",
    )
    if owner_id is None:
        raise ValueError("Request status owner is missing")
    status_value = _bounded_text(
        payload.get("status"),
        field_name="request status value",
        max_length=40,
    )
    normalized = {
        "request_id": request_id,
        "status": status_value,
        "actor_id": owner_id,
        "user_id": owner_id,
    }

    for canonical, legacy, field_name in (
        ("stream_id", "room_id", "request status stream"),
        ("stream_seq", "room_seq", "request status stream sequence"),
        ("event_id", "message_id", "request status event"),
    ):
        value = _optional_alias_bigint(
            payload,
            canonical,
            legacy,
            field_name=field_name,
        )
        if value is not None:
            normalized[canonical] = value

    for field_name, max_length, allow_empty in (
        ("body", 1000, True),
        ("category", 50, False),
        ("payment_id", 80, True),
        ("persistence", 40, False),
    ):
        value = _optional_text(
            payload.get(field_name),
            field_name=f"request status {field_name}",
            max_length=max_length,
            allow_empty=allow_empty,
        )
        if value is not None:
            normalized[field_name] = value

    failed_reason = payload.get("failed_reason")
    if failed_reason is None:
        failed_reason = payload.get("reason")
    failed_reason = _optional_text(
        failed_reason,
        field_name="request status failed_reason",
        max_length=500,
        allow_empty=False,
    )
    if failed_reason is not None:
        normalized["failed_reason"] = failed_reason

    schema_version = payload.get("schema_version")
    if schema_version is not None:
        if type(schema_version) is not int or not 1 <= schema_version <= 32_767:
            raise ValueError("Invalid request status schema_version")
        normalized["schema_version"] = schema_version

    event_type = payload.get("event_type")
    if event_type is not None:
        event_type = _bounded_text(
            event_type,
            field_name="request status event_type",
            max_length=50,
        )
        if schema_version is not None and schema_version >= 2 and not is_generic_event_type(event_type):
            raise ValueError("Invalid generic request status event_type")
        normalized["event_type"] = event_type

    if "payload" in payload:
        normalized["payload"] = _json_object(
            payload["payload"],
            field_name="request status payload",
            max_bytes=65_536,
        )
    if "metadata" in payload:
        normalized["metadata"] = _json_object(
            payload["metadata"],
            field_name="request status metadata",
            max_bytes=16_384,
        )
    if schema_version is not None and schema_version >= 2:
        if "event_type" not in normalized or "payload" not in normalized:
            raise ValueError("Generic request status envelope is incomplete")
        normalized.setdefault("metadata", {})

    for field_name in ("retry_count",):
        value = _optional_nonnegative_int(payload.get(field_name), field_name=field_name)
        if value is not None:
            normalized[field_name] = value

    for field_name in (
        "queued_at",
        "created_at",
        "persisted_at",
        "next_retry_at",
        "failed_at",
    ):
        value = _optional_iso_timestamp(
            payload.get(field_name),
            field_name=f"request status {field_name}",
        )
        if value is not None:
            normalized[field_name] = value
    return normalized


def _normalize_message_snapshot_record(key: str, payload: dict) -> dict:
    message_key = _numeric_topic_key(key, field_name="message")
    if not isinstance(payload, dict):
        raise ValueError("Message snapshot must be an object")
    validate_json_structure(payload, max_depth=MAX_JSON_WIRE_NESTING_DEPTH)
    message_id = _positive_bigint(payload.get("id"), field_name="message id")
    if message_id != message_key:
        raise ValueError("Message snapshot key/payload mismatch")
    stream_id = _optional_alias_bigint(
        payload,
        "stream_id",
        "room_id",
        field_name="message stream",
    )
    if stream_id is None:
        raise ValueError("Message snapshot stream is missing")
    stream_seq = _optional_alias_bigint(
        payload,
        "stream_seq",
        "room_seq",
        field_name="message stream sequence",
    )
    if stream_seq is None:
        raise ValueError("Message snapshot stream sequence is missing")
    user_id = _optional_alias_bigint(
        payload,
        "actor_id",
        "user_id",
        field_name="message owner",
    )
    if user_id is None:
        raise ValueError("Message snapshot owner is missing")
    request_id = payload.get("request_id")
    _request_status_key(request_id)
    body = _bounded_text(
        payload.get("body"),
        field_name="message body",
        max_length=1000,
        allow_empty=True,
    )
    schema_version = payload.get("schema_version", 1)
    if type(schema_version) is not int or not 1 <= schema_version <= 32_767:
        raise ValueError("Invalid message schema_version")
    event_type = payload.get("event_type", "legacy.message")
    event_type = _bounded_text(
        event_type,
        field_name="message event_type",
        max_length=50,
    )
    if schema_version >= 2 and not is_generic_event_type(event_type):
        raise ValueError("Invalid generic message event_type")
    payload_value = payload.get("payload")
    if payload_value is None and schema_version < 2:
        payload_value = {"text": body}
    payload_value = _json_object(
        payload_value,
        field_name="message payload",
        max_bytes=65_536,
    )
    metadata = _json_object(
        payload.get("metadata", {}),
        field_name="message metadata",
        max_bytes=16_384,
    )
    created_at = _iso_timestamp(payload.get("created_at"), field_name="message created_at")
    normalized = {
        "id": message_id,
        "request_id": request_id,
        "stream_id": stream_id,
        "stream_seq": stream_seq,
        "user_id": user_id,
        "actor_id": user_id,
        "body": body,
        "event_type": event_type,
        "category": _optional_text(
            payload.get("category"),
            field_name="message category",
            max_length=50,
        ),
        "payment_id": _optional_text(
            payload.get("payment_id"),
            field_name="message payment_id",
            max_length=80,
            allow_empty=True,
        ),
        "schema_version": schema_version,
        "payload": payload_value,
        "metadata": metadata,
        "created_at": created_at,
    }
    persisted_at = _optional_iso_timestamp(
        payload.get("persisted_at"),
        field_name="message persisted_at",
    )
    if persisted_at is not None:
        if datetime.fromisoformat(persisted_at).timestamp() < datetime.fromisoformat(created_at).timestamp():
            raise ValueError("Message persisted_at precedes created_at")
        normalized["persisted_at"] = persisted_at
    return normalized


def _normalize_stream_snapshot_record(key: str, payload: dict) -> dict:
    stream_key = _numeric_topic_key(key, field_name="stream")
    if not isinstance(payload, dict):
        raise ValueError("Stream snapshot must be an object")
    validate_json_structure(payload, max_depth=MAX_JSON_WIRE_NESTING_DEPTH)
    stream_id = _positive_bigint(payload.get("stream_id"), field_name="stream id")
    if stream_id != stream_key:
        raise ValueError("Stream snapshot key/payload mismatch")
    name = payload.get("name")
    if (
        not isinstance(name, str)
        or not 2 <= len(name) <= 50
        or "\x00" in name
    ):
        raise ValueError("Invalid stream name")
    member_ids = payload.get("member_ids")
    if not isinstance(member_ids, list) or len(member_ids) > 100:
        raise ValueError("Invalid stream member_ids")
    normalized_members = [
        _positive_bigint(member_id, field_name="stream member id")
        for member_id in member_ids
    ]
    if len(set(normalized_members)) != len(normalized_members):
        raise ValueError("Duplicate stream member id")
    return {
        "stream_id": stream_id,
        "name": name,
        "member_ids": normalized_members,
    }


def _trim_cache(cache: OrderedDict, max_items: int) -> None:
    while len(cache) > max(1, max_items):
        cache.popitem(last=False)


def _trim_message_cache(max_items: int) -> None:
    """Keep the globally newest persisted IDs regardless of replay partition order."""

    while len(_message_snapshot_cache) > max(1, max_items):
        while (
            _message_snapshot_id_heap
            and _message_snapshot_id_heap[0] not in _message_snapshot_cache
        ):
            heapq.heappop(_message_snapshot_id_heap)
        if not _message_snapshot_id_heap:
            raise RuntimeError("Message snapshot heap/cache invariant failed")
        _remove_message_snapshot(heapq.heappop(_message_snapshot_id_heap))


def _compact_message_heap_if_needed() -> None:
    live_count = len(_message_snapshot_cache)
    if len(_message_snapshot_id_heap) <= (2 * live_count) + 1024:
        return
    _message_snapshot_id_heap[:] = _message_snapshot_cache.keys()
    heapq.heapify(_message_snapshot_id_heap)


def _message_epoch(snapshot: dict) -> float:
    timestamp = snapshot.get("persisted_at") or snapshot["created_at"]
    return datetime.fromisoformat(timestamp).timestamp()


def _recompute_stream_timestamp(stream_id: int) -> None:
    timestamps = [
        _message_snapshot_epochs[message_id]
        for message_id, message in _message_snapshot_cache.items()
        if message["stream_id"] == stream_id
    ]
    if timestamps:
        _stream_message_updated_at[stream_id] = max(timestamps)
    else:
        _stream_message_updated_at.pop(stream_id, None)


def _remove_message_snapshot(message_id: int) -> dict | None:
    removed = _message_snapshot_cache.pop(message_id, None)
    removed_epoch = _message_snapshot_epochs.pop(message_id, None)
    if removed is None:
        return None
    stream_id = removed["stream_id"]
    remaining = _stream_message_counts.get(stream_id, 1) - 1
    if remaining <= 0:
        _stream_message_counts.pop(stream_id, None)
        _stream_message_updated_at.pop(stream_id, None)
    else:
        _stream_message_counts[stream_id] = remaining
        if (
            removed_epoch is not None
            and removed_epoch >= _stream_message_updated_at.get(stream_id, removed_epoch)
        ):
            _recompute_stream_timestamp(stream_id)
    _compact_message_heap_if_needed()
    return removed


def cache_request_status(request_id: str, payload: dict) -> None:
    normalized = _normalize_request_status_record(request_id, payload)
    with _cache_lock:
        _request_status_cache[request_id] = normalized
        _request_status_cache.move_to_end(request_id)
        _trim_cache(_request_status_cache, settings.materialized_cache_max_request_statuses)


def get_cached_request_status(request_id: str) -> dict | None:
    with _cache_lock:
        payload = _request_status_cache.get(request_id)
        if payload is not None:
            _request_status_cache.move_to_end(request_id)
        return copy.deepcopy(payload) if payload is not None else None


def cache_message_snapshot(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Message snapshot must be an object")
    message_id = _positive_bigint(payload.get("id"), field_name="message id")
    normalized = _normalize_message_snapshot_record(str(message_id), payload)
    with _cache_lock:
        existing = _message_snapshot_cache.get(message_id)
        if existing is not None:
            immutable_existing = {key: value for key, value in existing.items() if key != "persisted_at"}
            immutable_normalized = {
                key: value for key, value in normalized.items() if key != "persisted_at"
            }
            if immutable_existing != immutable_normalized:
                raise ValueError("Message snapshot identity/content mutation")
        else:
            heapq.heappush(_message_snapshot_id_heap, message_id)
            stream_id = normalized["stream_id"]
            _stream_message_counts[stream_id] = _stream_message_counts.get(stream_id, 0) + 1
        snapshot_epoch = _message_epoch(normalized)
        previous_epoch = _message_snapshot_epochs.get(message_id)
        if previous_epoch is not None and snapshot_epoch < previous_epoch:
            raise ValueError("Message snapshot persisted_at regressed")
        _message_snapshot_cache[message_id] = normalized
        _message_snapshot_cache.move_to_end(message_id)
        _message_snapshot_epochs[message_id] = snapshot_epoch
        stream_id = normalized["stream_id"]
        _stream_message_updated_at[stream_id] = max(
            snapshot_epoch,
            _stream_message_updated_at.get(stream_id, snapshot_epoch),
        )
        _trim_message_cache(settings.materialized_cache_max_messages)
        _compact_message_heap_if_needed()


def list_cached_events(stream_id: int, limit: int, before_id: int | None = None) -> tuple[list[dict], float | None]:
    with _cache_lock:
        rows = [
            copy.deepcopy(message)
            for message in _message_snapshot_cache.values()
            if int(message.get("stream_id", -1)) == int(stream_id)
            and (before_id is None or int(message.get("id", 0)) < int(before_id))
        ]
        updated_at = _stream_message_updated_at.get(int(stream_id))
    rows.sort(key=lambda item: int(item["id"]), reverse=True)
    age = None if updated_at is None else max(0, time.time() - updated_at)
    return rows[:limit], age


def cache_stream_snapshot(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Stream snapshot must be an object")
    stream_id = _positive_bigint(payload.get("stream_id"), field_name="stream id")
    normalized = _normalize_stream_snapshot_record(str(stream_id), payload)
    with _cache_lock:
        _stream_membership_cache[stream_id] = set(normalized["member_ids"])
        _stream_membership_cache.move_to_end(stream_id)
        _trim_cache(_stream_membership_cache, settings.materialized_cache_max_streams)


def is_cached_stream_member(stream_id: int, user_id: int) -> bool:
    with _cache_lock:
        member_ids = _stream_membership_cache.get(int(stream_id))
        if member_ids is not None:
            _stream_membership_cache.move_to_end(int(stream_id))
        return member_ids is not None and int(user_id) in member_ids


def evict_request_status(request_id: str) -> None:
    with _cache_lock:
        _request_status_cache.pop(str(request_id), None)


def evict_message_snapshot(message_id: int | str) -> None:
    with _cache_lock:
        _remove_message_snapshot(int(message_id))


def evict_stream_snapshot(stream_id: int | str) -> None:
    with _cache_lock:
        stream_id_value = int(stream_id)
        _stream_membership_cache.pop(stream_id_value, None)
        _stream_message_updated_at.pop(stream_id_value, None)


def clear_materialized_cache() -> None:
    with _cache_lock:
        _request_status_cache.clear()
        _message_snapshot_cache.clear()
        _stream_message_updated_at.clear()
        _message_snapshot_epochs.clear()
        _message_snapshot_id_heap.clear()
        _stream_message_counts.clear()
        _stream_membership_cache.clear()


def get_materialized_cache_status() -> dict:
    with _cache_lock:
        return {
            "ready": _cache_ready,
            "hydrated": _cache_hydrated,
            "last_error": _cache_last_error,
            "request_statuses": len(_request_status_cache),
            "messages": len(_message_snapshot_cache),
            "streams": len(_stream_membership_cache),
        }


def _evict_materialized_key(topic: str, key: str) -> None:
    if topic == settings.kafka_request_status_topic:
        evict_request_status(_request_status_key(key))
    elif topic == settings.kafka_message_snapshot_topic:
        evict_message_snapshot(_numeric_topic_key(key, field_name="message"))
    elif topic == settings.kafka_stream_snapshot_topic:
        evict_stream_snapshot(_numeric_topic_key(key, field_name="stream"))


def _apply_materialized_record(topic: str, key: str | None, value: dict | None) -> None:
    if not key:
        return
    try:
        if topic == settings.kafka_request_status_topic:
            request_key = _request_status_key(key)
            if value is None:
                evict_request_status(request_key)
            else:
                if is_invalid_kafka_payload(value) or not isinstance(value, dict):
                    raise ValueError("Malformed request-status value")
                cache_request_status(request_key, value)
        elif topic == settings.kafka_message_snapshot_topic:
            message_key = _numeric_topic_key(key, field_name="message")
            if value is None:
                evict_message_snapshot(message_key)
            else:
                if is_invalid_kafka_payload(value) or not isinstance(value, dict):
                    raise ValueError("Malformed message-snapshot value")
                cache_message_snapshot(
                    _normalize_message_snapshot_record(str(message_key), value)
                )
        elif topic == settings.kafka_stream_snapshot_topic:
            stream_key = _numeric_topic_key(key, field_name="stream")
            if value is None:
                evict_stream_snapshot(stream_key)
            else:
                if is_invalid_kafka_payload(value) or not isinstance(value, dict):
                    raise ValueError("Malformed stream-snapshot value")
                cache_stream_snapshot(
                    _normalize_stream_snapshot_record(str(stream_key), value)
                )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        try:
            _evict_materialized_key(topic, key)
        except (TypeError, ValueError, OverflowError):
            pass
        logging.warning(
            "Evicted invalid materialized-cache record topic=%s key=%s error=%s",
            topic,
            key,
            exc,
        )


def _consume_materialized_topics() -> None:
    global _cache_ready, _cache_hydrated, _cache_last_error

    from kafka import TopicPartition

    while not _stop_event.is_set():
        consumer = None
        try:
            consumer = build_materialized_cache_consumer()
            topics = [
                settings.kafka_request_status_topic,
                settings.kafka_message_snapshot_topic,
                settings.kafka_stream_snapshot_topic,
            ]
            topic_partitions = []
            unavailable_topics = []
            for topic in topics:
                partitions = consumer.partitions_for_topic(topic)
                if not partitions:
                    unavailable_topics.append(topic)
                    continue
                topic_partitions.extend(
                    TopicPartition(topic, partition) for partition in sorted(partitions)
                )

            if unavailable_topics:
                clear_materialized_cache()
                with _cache_lock:
                    _cache_ready = False
                    _cache_hydrated = False
                    _cache_last_error = "materialized_topics_unavailable"
                logging.warning(
                    "Materialized cache topics unavailable: %s",
                    ",".join(unavailable_topics),
                )
                time.sleep(2)
                continue

            # Rebuild into an unavailable cache rather than exposing partial
            # beginning-of-log replay after a consumer reconnect.
            clear_materialized_cache()
            with _cache_lock:
                _cache_ready = False
                _cache_hydrated = False
            consumer.assign(topic_partitions)
            consumer.seek_to_beginning(*topic_partitions)
            initial_end_offsets = consumer.end_offsets(topic_partitions)

            while not _stop_event.is_set():
                records = consumer.poll(timeout_ms=1000, max_records=200)
                for messages in records.values():
                    for message in messages:
                        _apply_materialized_record(message.topic, message.key, message.value)
                if not _cache_ready and all(
                    consumer.position(topic_partition) >= initial_end_offsets[topic_partition]
                    for topic_partition in topic_partitions
                ):
                    with _cache_lock:
                        _cache_ready = True
                        _cache_hydrated = True
                        _cache_last_error = None
        except Exception as exc:  # noqa: BLE001
            clear_materialized_cache()
            with _cache_lock:
                _cache_ready = False
                _cache_hydrated = False
                _cache_last_error = type(exc).__name__
            logging.warning("Materialized cache consumer failed: %s", exc)
            time.sleep(2)
        finally:
            if consumer is not None:
                try:
                    consumer.close()
                except Exception:  # noqa: BLE001
                    pass


def start_materialized_cache() -> None:
    global _cache_thread, _cache_ready, _cache_hydrated, _cache_last_error

    if _cache_thread is not None and _cache_thread.is_alive():
        return
    clear_materialized_cache()
    with _cache_lock:
        _cache_ready = False
        _cache_hydrated = False
        _cache_last_error = None
    _stop_event.clear()
    _cache_thread = threading.Thread(
        target=_consume_materialized_topics,
        name="db-snapshot-materialized-cache",
        daemon=True,
    )
    _cache_thread.start()


def stop_materialized_cache() -> None:
    _stop_event.set()
    if _cache_thread is not None:
        _cache_thread.join(timeout=3)
