import json
import time
from functools import lru_cache

from portfolio.config import settings
from portfolio.metrics import health_status


def _bootstrap_servers() -> list[str]:
    return [server.strip() for server in settings.kafka_bootstrap_servers.split(",") if server.strip()]


@lru_cache(maxsize=1)
def get_kafka_producer():
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=_bootstrap_servers(),
        key_serializer=lambda value: str(value).encode("utf-8"),
        value_serializer=lambda value: json.dumps(value).encode("utf-8"),
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


def build_ingress_consumer():
    from kafka import KafkaConsumer

    return KafkaConsumer(
        settings.kafka_ingress_topic,
        bootstrap_servers=_bootstrap_servers(),
        group_id=settings.kafka_consumer_group,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        key_deserializer=lambda value: value.decode("utf-8") if value else None,
        value_deserializer=lambda value: json.loads(value.decode("utf-8")),
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
        key_deserializer=lambda value: value.decode("utf-8") if value else None,
        value_deserializer=lambda value: json.loads(value.decode("utf-8")),
        consumer_timeout_ms=1000,
    )


def list_recent_topic_messages(topic: str, limit: int) -> list[dict]:
    from kafka import KafkaConsumer, TopicPartition

    consumer = KafkaConsumer(
        bootstrap_servers=_bootstrap_servers(),
        enable_auto_commit=False,
        consumer_timeout_ms=1000,
        key_deserializer=lambda value: value.decode("utf-8") if value else None,
        value_deserializer=lambda value: json.loads(value.decode("utf-8")),
    )
    try:
        partitions = consumer.partitions_for_topic(topic)
        if not partitions:
            return []

        topic_partitions = [TopicPartition(topic, partition) for partition in sorted(partitions)]
        consumer.assign(topic_partitions)
        beginning_offsets = consumer.beginning_offsets(topic_partitions)
        end_offsets = consumer.end_offsets(topic_partitions)

        for topic_partition in topic_partitions:
            beginning = int(beginning_offsets.get(topic_partition, 0))
            end = int(end_offsets.get(topic_partition, 0))
            consumer.seek(topic_partition, max(beginning, end - limit))

        items: list[dict] = []
        deadline = time.monotonic() + 2
        while len(items) < limit and time.monotonic() < deadline:
            records = consumer.poll(timeout_ms=200, max_records=limit)
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
                    if len(items) >= limit:
                        break
                if len(items) >= limit:
                    break

        items.sort(key=lambda item: (item["timestamp"] or 0, item["partition"], item["offset"]), reverse=True)
        return items[:limit]
    finally:
        consumer.close()


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
