import base64
import hashlib
import json
import time
from functools import lru_cache

from portfolio.config import settings
from portfolio.event_envelope import MAX_JSON_WIRE_NESTING_DEPTH, validate_json_structure
from portfolio.metrics import health_status


INVALID_KAFKA_PAYLOAD_MARKER = "__invalid_kafka_payload__"


class InvalidKafkaPayload(dict):
    """In-process marker carrying provenance captured from undecodable Kafka bytes."""


def _reject_nonfinite_json(value: str):
    raise ValueError(f"Non-finite JSON value: {value}")


def _bootstrap_servers() -> list[str]:
    return [server.strip() for server in settings.kafka_bootstrap_servers.split(",") if server.strip()]


def _serialize_json(value):
    if value is None:
        return None
    validate_json_structure(value, max_depth=MAX_JSON_WIRE_NESTING_DEPTH)
    return json.dumps(value, allow_nan=False).encode("utf-8")


def _deserialize_json(value: bytes | None):
    if value is None:
        return None
    try:
        decoded = json.loads(value.decode("utf-8"), parse_constant=_reject_nonfinite_json)
        validate_json_structure(decoded, max_depth=MAX_JSON_WIRE_NESTING_DEPTH)
        return decoded
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        return InvalidKafkaPayload(
            {
                INVALID_KAFKA_PAYLOAD_MARKER: True,
                "decode_error": type(exc).__name__,
                "raw_base64": base64.b64encode(value[:2048]).decode("ascii"),
                "raw_size": len(value),
                "raw_sha256": hashlib.sha256(value).hexdigest(),
            }
        )


def _deserialize_utf8_key(value: bytes | None) -> str | None:
    if value is None:
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def is_invalid_kafka_payload(value) -> bool:
    return isinstance(value, dict) and value.get(INVALID_KAFKA_PAYLOAD_MARKER) is True


@lru_cache(maxsize=1)
def get_kafka_producer():
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=_bootstrap_servers(),
        key_serializer=lambda value: str(value).encode("utf-8"),
        value_serializer=_serialize_json,
        enable_idempotence=True,
        acks="all",
        retries=3,
        linger_ms=5,
        max_block_ms=3000,
        request_timeout_ms=3000,
    )


def publish_ingress_job(key: int | str, payload: dict) -> None:
    producer = get_kafka_producer()
    future = producer.send(settings.kafka_ingress_topic, key=key, value=payload)
    future.get(timeout=10)


def publish_dlq_job(key: int | str, payload: dict) -> None:
    producer = get_kafka_producer()
    future = producer.send(settings.kafka_dlq_topic, key=key, value=payload)
    future.get(timeout=10)


def publish_request_status(request_id: str, payload: dict) -> None:
    producer = get_kafka_producer()
    future = producer.send(settings.kafka_request_status_topic, key=request_id, value=payload)
    future.get(timeout=10)


def publish_message_snapshot(message_id: int | str, payload: dict) -> None:
    producer = get_kafka_producer()
    future = producer.send(settings.kafka_message_snapshot_topic, key=message_id, value=payload)
    future.get(timeout=10)


def publish_stream_snapshot(stream_id: int | str, payload: dict) -> None:
    producer = get_kafka_producer()
    future = producer.send(settings.kafka_stream_snapshot_topic, key=stream_id, value=payload)
    future.get(timeout=10)


def publish_notification_job(key: int | str, payload: dict) -> None:
    producer = get_kafka_producer()
    future = producer.send(settings.kafka_notification_topic, key=key, value=payload)
    future.get(timeout=10)


def build_ingress_consumer():
    from kafka import KafkaConsumer

    return KafkaConsumer(
        settings.kafka_ingress_topic,
        bootstrap_servers=_bootstrap_servers(),
        group_id=settings.kafka_consumer_group,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        value_deserializer=_deserialize_json,
        consumer_timeout_ms=1000,
    )


def build_dlq_consumer():
    from kafka import KafkaConsumer

    return KafkaConsumer(
        settings.kafka_dlq_topic,
        bootstrap_servers=_bootstrap_servers(),
        group_id=f"{settings.kafka_consumer_group}-dlq-replayer",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        key_deserializer=_deserialize_utf8_key,
        value_deserializer=_deserialize_json,
        consumer_timeout_ms=1000,
    )


def build_notification_consumer():
    from kafka import KafkaConsumer

    return KafkaConsumer(
        settings.kafka_notification_topic,
        bootstrap_servers=_bootstrap_servers(),
        group_id=settings.kafka_notification_consumer_group,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        key_deserializer=_deserialize_utf8_key,
        value_deserializer=_deserialize_json,
        consumer_timeout_ms=1000,
    )


def build_materialized_cache_consumer():
    from kafka import KafkaConsumer

    return KafkaConsumer(
        bootstrap_servers=_bootstrap_servers(),
        enable_auto_commit=False,
        consumer_timeout_ms=1000,
        key_deserializer=_deserialize_utf8_key,
        value_deserializer=_deserialize_json,
    )


def publish_state_tombstone(topic: str, key: int | str) -> None:
    producer = get_kafka_producer()
    producer.send(topic, key=key, value=None).get(timeout=10)


def publish_request_status_tombstone(request_id: str) -> None:
    publish_state_tombstone(settings.kafka_request_status_topic, request_id)


def publish_message_snapshot_tombstone(message_id: int | str) -> None:
    publish_state_tombstone(settings.kafka_message_snapshot_topic, message_id)


def publish_stream_snapshot_tombstone(stream_id: int | str) -> None:
    publish_state_tombstone(settings.kafka_stream_snapshot_topic, stream_id)


def list_recent_topic_messages(topic: str, limit: int) -> list[dict]:
    from kafka import KafkaConsumer, TopicPartition

    consumer = KafkaConsumer(
        bootstrap_servers=_bootstrap_servers(),
        enable_auto_commit=False,
        consumer_timeout_ms=1000,
        key_deserializer=_deserialize_utf8_key,
        value_deserializer=_deserialize_json,
    )
    try:
        partitions = consumer.partitions_for_topic(topic)
        if not partitions:
            return []

        topic_partitions = [TopicPartition(topic, partition) for partition in sorted(partitions)]
        consumer.assign(topic_partitions)
        beginning_offsets = consumer.beginning_offsets(topic_partitions)
        end_offsets = consumer.end_offsets(topic_partitions)

        target_end_offsets: dict[object, int] = {}
        for topic_partition in topic_partitions:
            beginning = int(beginning_offsets.get(topic_partition, 0))
            end = int(end_offsets.get(topic_partition, 0))
            consumer.seek(topic_partition, max(beginning, end - limit))
            target_end_offsets[topic_partition] = end

        items: list[dict] = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if all(
                consumer.position(topic_partition) >= target_end_offsets[topic_partition]
                for topic_partition in topic_partitions
            ):
                break
            records = consumer.poll(
                timeout_ms=200,
                max_records=max(limit, limit * len(topic_partitions)),
            )
            if not records:
                break
            for topic_partition, messages in records.items():
                for message in messages:
                    items.append(
                        {
                            "topic": message.topic,
                            "partition": topic_partition.partition,
                            "offset": message.offset,
                            "timestamp": message.timestamp,
                            "key": message.key,
                            "value": message.value,
                        }
                    )

        items.sort(key=lambda item: (item["timestamp"] or 0, item["partition"], item["offset"]), reverse=True)
        return items[:limit]
    finally:
        consumer.close()


def reset_topic(topic: str, partitions: int = 8, replication_factor: int = 3, configs: dict[str, str] | None = None) -> None:
    from kafka import KafkaAdminClient
    from kafka.admin import NewTopic
    from kafka.errors import TopicAlreadyExistsError, UnknownTopicOrPartitionError

    client = KafkaAdminClient(
        bootstrap_servers=_bootstrap_servers(),
        request_timeout_ms=3000,
        api_version_auto_timeout_ms=3000,
    )
    try:
        try:
            client.delete_topics([topic], timeout_ms=5000)
        except UnknownTopicOrPartitionError:
            pass

        deadline = time.monotonic() + 10
        while topic in client.list_topics() and time.monotonic() < deadline:
            time.sleep(0.2)

        try:
            client.create_topics(
                [
                    NewTopic(
                        name=topic,
                        num_partitions=partitions,
                        replication_factor=replication_factor,
                        topic_configs=configs or {},
                    )
                ],
                timeout_ms=5000,
            )
        except TopicAlreadyExistsError:
            pass
    finally:
        client.close()


def ping_kafka() -> bool:
    try:
        from kafka import KafkaAdminClient

        client = KafkaAdminClient(
            bootstrap_servers=_bootstrap_servers(),
            request_timeout_ms=3000,
            api_version_auto_timeout_ms=3000,
        )
        try:
            client.list_topics()
        finally:
            client.close()
        health_status.labels(component="kafka").set(1)
        return True
    except Exception:
        health_status.labels(component="kafka").set(0)
        time.sleep(0.2)
        return False
