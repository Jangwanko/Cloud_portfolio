import logging
import threading
import time

from portfolio.config import settings
from portfolio.kafka_client import build_materialized_cache_consumer

_request_status_cache: dict[str, dict] = {}
_message_snapshot_cache: dict[int, dict] = {}
_stream_message_updated_at: dict[int, float] = {}
_stream_membership_cache: dict[int, set[int]] = {}
_cache_lock = threading.RLock()
_stop_event = threading.Event()
_cache_thread: threading.Thread | None = None


def cache_request_status(request_id: str, payload: dict) -> None:
    with _cache_lock:
        _request_status_cache[request_id] = dict(payload)


def get_cached_request_status(request_id: str) -> dict | None:
    with _cache_lock:
        payload = _request_status_cache.get(request_id)
        return dict(payload) if payload is not None else None


def cache_message_snapshot(payload: dict) -> None:
    with _cache_lock:
        _message_snapshot_cache[int(payload["id"])] = dict(payload)
        _stream_message_updated_at[int(payload["stream_id"])] = time.monotonic()


def list_cached_events(stream_id: int, limit: int, before_id: int | None = None) -> tuple[list[dict], float | None]:
    with _cache_lock:
        rows = [
            dict(message)
            for message in _message_snapshot_cache.values()
            if int(message.get("stream_id", -1)) == int(stream_id)
            and (before_id is None or int(message.get("id", 0)) < int(before_id))
        ]
        updated_at = _stream_message_updated_at.get(int(stream_id))
    rows.sort(key=lambda item: int(item["id"]), reverse=True)
    age = None if updated_at is None else max(0, time.monotonic() - updated_at)
    return rows[:limit], age


def cache_stream_snapshot(payload: dict) -> None:
    with _cache_lock:
        _stream_membership_cache[int(payload["stream_id"])] = {
            int(member_id) for member_id in payload.get("member_ids", [])
        }


def is_cached_stream_member(stream_id: int, user_id: int) -> bool:
    with _cache_lock:
        member_ids = _stream_membership_cache.get(int(stream_id))
        return member_ids is not None and int(user_id) in member_ids


def _apply_materialized_record(topic: str, key: str | None, value: dict | None) -> None:
    if not key or value is None:
        return
    if topic == settings.kafka_request_status_topic:
        cache_request_status(str(key), value)
    elif topic == settings.kafka_message_snapshot_topic:
        cache_message_snapshot(value)
    elif topic == settings.kafka_stream_snapshot_topic:
        cache_stream_snapshot(value)


def _consume_materialized_topics() -> None:
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
            for topic in topics:
                partitions = consumer.partitions_for_topic(topic)
                if partitions:
                    topic_partitions.extend(
                        TopicPartition(topic, partition) for partition in sorted(partitions)
                    )

            if not topic_partitions:
                time.sleep(2)
                continue

            consumer.assign(topic_partitions)
            consumer.seek_to_beginning(*topic_partitions)

            while not _stop_event.is_set():
                records = consumer.poll(timeout_ms=1000, max_records=200)
                for messages in records.values():
                    for message in messages:
                        _apply_materialized_record(message.topic, message.key, message.value)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Materialized cache consumer failed: %s", exc)
            time.sleep(2)
        finally:
            if consumer is not None:
                try:
                    consumer.close()
                except Exception:  # noqa: BLE001
                    pass


def start_materialized_cache() -> None:
    global _cache_thread

    if _cache_thread is not None and _cache_thread.is_alive():
        return
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
