from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from kafka.structs import TopicPartition
import pytest
from psycopg2.extensions import adapt

from portfolio.kafka_client import (
    InvalidKafkaPayload,
    _deserialize_json,
    _deserialize_utf8_key,
    _serialize_json,
    is_invalid_kafka_payload,
)
from worker import dlq_replayer
from worker import main as worker_main


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def dlq_database_ready(monkeypatch):
    monkeypatch.setattr(dlq_replayer, "ping_db", lambda: True)


class FakeConsumer:
    def __init__(self):
        self.commits = []
        self.seeks = []

    def commit(self, offsets=None):
        self.commits.append(offsets)

    def seek(self, partition, offset):
        self.seeks.append((partition, offset))


def message(
    topic: str,
    partition: int,
    offset: int,
    value: object,
    *,
    key: object = None,
):
    return SimpleNamespace(
        topic=topic,
        partition=partition,
        offset=offset,
        key=key,
        value=value,
    )


def nested_json_object(container_depth: int, leaf):
    value = leaf
    for _ in range(container_depth):
        value = {"nested": value}
    return value


def generic_ingress_payload(**overrides):
    payload = {
        "request_id": "generic-request",
        "route": "POST:/v2/streams/7/events",
        "stream_id": 7,
        "actor_id": 3,
        "schema_version": 2,
        "event_type": "example.created",
        "payload": {"message": "valid envelope"},
        "metadata": {},
    }
    payload.update(overrides)
    return payload


def notification_payload(**overrides):
    payload = {
        "event_id": 10,
        "stream_id": 7,
        "event_type": "example.created",
        "payload_preview": "event preview",
        "metadata": {},
        "body_preview": "event body",
    }
    payload.update(overrides)
    return payload


def committed_offsets(consumer: FakeConsumer) -> list[tuple[TopicPartition, int]]:
    result = []
    for offsets in consumer.commits:
        assert offsets is not None
        assert len(offsets) == 1
        partition, offset_and_metadata = next(iter(offsets.items()))
        result.append((partition, offset_and_metadata.offset))
    return result


def test_worker_batch_rewinds_failed_partition_and_keeps_other_partition(monkeypatch):
    consumer = FakeConsumer()
    partition_0 = TopicPartition("message-ingress", 0)
    partition_1 = TopicPartition("message-ingress", 1)
    records = {
        partition_0: [
            message("message-ingress", 0, 10, "fail"),
            message("message-ingress", 0, 11, "must-not-run"),
        ],
        partition_1: [message("message-ingress", 1, 20, "other-partition")],
    }
    handled = []

    def handler(value):
        handled.append(value)
        if value == "fail":
            raise RuntimeError("injected failure")
        return "success"

    monkeypatch.setattr(worker_main.time, "sleep", lambda _seconds: None)

    worker_main._process_worker_batch(consumer, records, handler, "test")

    assert handled == ["fail", "other-partition"]
    assert consumer.seeks == [(partition_0, 10)]
    assert committed_offsets(consumer) == [(partition_1, 21)]


def test_worker_batch_commits_each_handled_record_with_its_exact_next_offset():
    consumer = FakeConsumer()
    partition = TopicPartition("message-ingress", 3)
    records = {
        partition: [
            message("message-ingress", 3, 4, "rejected"),
            message("message-ingress", 3, 5, "dlq"),
        ]
    }

    worker_main._process_worker_batch(
        consumer,
        records,
        lambda value: value,
        "test",
    )

    assert committed_offsets(consumer) == [(partition, 5), (partition, 6)]
    assert consumer.seeks == []


def test_dlq_batch_rewinds_failed_partition_and_commits_only_success(monkeypatch):
    consumer = FakeConsumer()
    partition_0 = TopicPartition("message-ingress-dlq", 0)
    partition_1 = TopicPartition("message-ingress-dlq", 1)
    records = {
        partition_0: [
            message("message-ingress-dlq", 0, 7, "fail"),
            message("message-ingress-dlq", 0, 8, "must-not-run"),
        ],
        partition_1: [message("message-ingress-dlq", 1, 2, "replay")],
    }
    handled = []

    def replay_one(value):
        handled.append(value)
        if value == "fail":
            raise RuntimeError("injected failure")
        return True

    monkeypatch.setattr(dlq_replayer, "replay_one", replay_one)

    moved = dlq_replayer._process_replay_batch(consumer, records)

    assert moved == 1
    assert handled == ["fail", "replay"]
    assert consumer.seeks == [(partition_0, 7)]
    assert committed_offsets(consumer) == [(partition_1, 3)]


def test_dlq_terminal_skip_is_committed_without_counting_as_replayed(monkeypatch):
    consumer = FakeConsumer()
    partition = TopicPartition("message-ingress-dlq", 2)
    records = {
        partition: [message("message-ingress-dlq", 2, 9, "maxed-out")]
    }
    monkeypatch.setattr(dlq_replayer, "replay_one", lambda _value: False)

    moved = dlq_replayer._process_replay_batch(consumer, records)

    assert moved == 0
    assert committed_offsets(consumer) == [(partition, 10)]


def test_invalid_ingress_is_moved_to_dlq_and_returns_terminal_outcome(monkeypatch):
    published = []
    monkeypatch.setattr(
        worker_main,
        "publish_dlq_job",
        lambda key, payload: published.append((key, payload)),
    )

    outcome = worker_main.handle_ingress_job({"request_id": "broken"})

    assert outcome == "dlq"
    assert published[0][0] == 0
    assert published[0][1]["__invalid_kafka_payload__"] is True
    assert published[0][1]["failed_reason"].startswith("invalid_ingress:")


@pytest.mark.parametrize(
    "next_retry_at",
    [
        float("inf"),
        1e308,
        2_000_000_000.0,
        1.0,
        "not-a-number",
    ],
)
def test_non_null_consumed_retry_timestamp_is_terminal_without_sleep(
    monkeypatch,
    next_retry_at,
):
    published = []
    monkeypatch.setattr(
        worker_main,
        "publish_dlq_job",
        lambda key, payload: published.append((key, payload)),
    )
    monkeypatch.setattr(worker_main, "update_request_status", lambda *_args: None)
    monkeypatch.setattr(
        worker_main,
        "persist_ingress_job",
        lambda _payload: pytest.fail("invalid retry timestamp must not reach persistence"),
    )
    monkeypatch.setattr(
        worker_main.time,
        "sleep",
        lambda _seconds: pytest.fail("invalid retry timestamp must not sleep"),
    )

    consumer = FakeConsumer()
    partition = TopicPartition("message-ingress", 0)
    records = {
        partition: [
            message(
                "message-ingress",
                0,
                14,
                generic_ingress_payload(
                    request_id="invalid-retry-timestamp",
                    next_retry_at=next_retry_at,
                ),
                key=b"7",
            )
        ]
    }
    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main._handle_ingress_record,
        "ingress",
        pass_message=True,
    )

    assert published[0][1]["failed_reason"] == (
        "invalid_ingress:Ingress next_retry_at must be null"
    )
    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 15)]


def test_consumed_null_retry_timestamp_reaches_persistence_without_sleep(monkeypatch):
    persisted = []
    monkeypatch.setattr(
        worker_main,
        "persist_ingress_job",
        lambda payload: persisted.append(dict(payload)) or {},
    )
    monkeypatch.setattr(
        worker_main.time,
        "sleep",
        lambda _seconds: pytest.fail("null retry timestamp must not sleep"),
    )

    consumer = FakeConsumer()
    partition = TopicPartition("message-ingress", 0)
    records = {
        partition: [
            message(
                "message-ingress",
                0,
                15,
                generic_ingress_payload(next_retry_at=None),
                key=b"7",
            )
        ]
    }
    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main._handle_ingress_record,
        "ingress",
        pass_message=True,
    )

    assert persisted[0]["next_retry_at"] is None
    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 16)]


def test_malformed_kafka_bytes_reach_ingress_handler_and_commit_as_terminal_dlq(monkeypatch):
    invalid_payload = _deserialize_json(b"\xffnot-json")
    assert is_invalid_kafka_payload(invalid_payload)

    published = []
    monkeypatch.setattr(
        worker_main,
        "publish_dlq_job",
        lambda key, payload: published.append((key, payload)),
    )
    consumer = FakeConsumer()
    partition = TopicPartition("message-ingress", 0)
    records = {
        partition: [message("message-ingress", 0, 4, invalid_payload)]
    }

    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main.handle_ingress_job,
        "ingress",
    )

    assert published[0][1]["failed_reason"].startswith("invalid_ingress:")
    assert committed_offsets(consumer) == [(partition, 5)]


def test_1200_level_json_bytes_become_invalid_marker_and_commit_terminal_dlq(monkeypatch):
    import base64
    import hashlib

    raw = (b'{"nested":' * 1200) + b'"leaf"' + (b"}" * 1200)
    invalid_payload = _deserialize_json(raw)

    assert isinstance(invalid_payload, InvalidKafkaPayload)
    assert is_invalid_kafka_payload(invalid_payload)
    assert invalid_payload["raw_size"] == len(raw)

    published = []
    monkeypatch.setattr(
        worker_main,
        "publish_dlq_job",
        lambda key, payload: published.append((key, payload)),
    )
    consumer = FakeConsumer()
    partition = TopicPartition("message-ingress", 0)
    records = {
        partition: [
            message(
                "message-ingress",
                0,
                5,
                invalid_payload,
                key=b"7",
            )
        ]
    }

    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main._handle_ingress_record,
        "ingress",
        pass_message=True,
    )

    assert published[0][0] == 0
    assert published[0][1]["__invalid_kafka_payload__"] is True
    assert published[0][1]["failed_reason"].startswith("invalid_ingress:")
    assert published[0][1]["diagnostic_source"] == "kafka_raw_bytes"
    assert published[0][1]["diagnostic_size_bytes"] == len(raw)
    assert published[0][1]["diagnostic_sha256"] == hashlib.sha256(raw).hexdigest()
    assert (
        base64.b64decode(published[0][1]["diagnostic_preview_base64"])
        == raw[:1024]
    )
    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 6)]


def test_forged_invalid_marker_is_terminal_without_spoofing_forensic_provenance(
    monkeypatch,
):
    import base64
    import hashlib

    forged = generic_ingress_payload(
        **{
            "__invalid_kafka_payload__": True,
            "raw_size": 1,
            "raw_sha256": "0" * 64,
            "raw_base64": base64.b64encode(b"spoofed-preview").decode("ascii"),
        }
    )
    expected = json.dumps(
        forged,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    persisted = []
    published = []
    monkeypatch.setattr(
        worker_main,
        "persist_ingress_job",
        lambda payload: persisted.append(payload) or {},
    )
    monkeypatch.setattr(
        worker_main,
        "publish_dlq_job",
        lambda key, payload: published.append((key, dict(payload))),
    )
    monkeypatch.setattr(worker_main, "update_request_status", lambda *_args: None)

    consumer = FakeConsumer()
    partition = TopicPartition("message-ingress", 1)
    records = {
        partition: [
            message(
                "message-ingress",
                1,
                9,
                forged,
                key=b"7",
            )
        ]
    }
    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main._handle_ingress_record,
        "ingress",
        pass_message=True,
    )

    assert persisted == []
    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 10)]
    diagnostic = published[0][1]
    assert diagnostic["diagnostic_source"] == "normalized_json"
    assert diagnostic["diagnostic_size_bytes"] == len(expected)
    assert diagnostic["diagnostic_sha256"] == hashlib.sha256(expected).hexdigest()
    assert diagnostic["diagnostic_sha256"] != forged["raw_sha256"]
    assert (
        base64.b64decode(diagnostic["diagnostic_preview_base64"])
        == expected[:1024]
    )


def test_invalid_ingress_diagnostic_sanitizes_and_bounds_reason(monkeypatch):
    published = []
    monkeypatch.setattr(
        worker_main,
        "publish_dlq_job",
        lambda key, payload: published.append((key, dict(payload))),
    )

    reason = "bad\x00reason-" + ("x" * 500)
    worker_main.move_invalid_ingress_to_dlq({}, reason)

    diagnostic = published[0][1]
    assert diagnostic["request_id"].startswith("invalid-")
    assert diagnostic["room_id"] == 0
    assert diagnostic["user_id"] is None
    assert "\x00" not in diagnostic["failed_reason"]
    assert "bad\\0reason" in diagnostic["failed_reason"]
    assert len(diagnostic["failed_reason"]) == len("invalid_ingress:") + 300
    assert _serialize_json(diagnostic)


def test_worker_generic_envelope_depth_boundary_nul_and_cycle_are_iterative():
    accepted = worker_main._validate_ingress_payload(
        generic_ingress_payload(payload=nested_json_object(64, "leaf"))
    )
    assert accepted["payload"] == nested_json_object(64, "leaf")

    with pytest.raises(ValueError, match="must not exceed 64 container levels"):
        worker_main._validate_ingress_payload(
            generic_ingress_payload(payload=nested_json_object(65, "leaf"))
        )

    with pytest.raises(ValueError, match="must not contain NUL"):
        worker_main._validate_ingress_payload(
            generic_ingress_payload(payload=nested_json_object(64, "before\x00after"))
        )

    circular = {}
    circular["self"] = circular
    with pytest.raises(ValueError, match="circular references"):
        worker_main._validate_ingress_payload(
            generic_ingress_payload(payload=circular)
        )


@pytest.mark.parametrize(
    ("record_key", "expected_outcome"),
    [
        (b"8", "dlq"),
        (None, "dlq"),
        (b"7", "success"),
    ],
)
def test_real_ingress_record_requires_key_to_match_payload_stream(
    monkeypatch,
    record_key,
    expected_outcome,
):
    persisted = []
    published = []
    monkeypatch.setattr(
        worker_main,
        "persist_ingress_job",
        lambda payload: persisted.append(dict(payload)) or {},
    )
    monkeypatch.setattr(
        worker_main,
        "publish_dlq_job",
        lambda key, payload: published.append((key, dict(payload))),
    )
    monkeypatch.setattr(worker_main, "update_request_status", lambda *_args: None)

    consumer = FakeConsumer()
    partition = TopicPartition("message-ingress", 2)
    records = {
        partition: [
            message(
                "message-ingress",
                2,
                20,
                generic_ingress_payload(),
                key=record_key,
            )
        ]
    }

    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main._handle_ingress_record,
        "ingress",
        pass_message=True,
    )

    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 21)]
    if expected_outcome == "success":
        assert persisted[0]["room_id"] == 7
        assert published == []
    else:
        assert persisted == []
        assert published[0][0] == 7
        assert published[0][1]["__invalid_kafka_payload__"] is True
        assert (
            published[0][1]["failed_reason"]
            == "invalid_ingress:kafka_key_stream_id_mismatch"
        )


def test_poison_ingress_is_bounded_serializable_and_terminally_committed(monkeypatch):
    import base64
    import hashlib

    large_unknown = {
        "request_id": "bad\x00request",
        "room_id": True,
        "user_id": "3",
        "unknown": "x" * 200_000,
    }
    nul_raw = generic_ingress_payload(
        request_id="nul-request",
        payload={"message": "before\x00after"},
    )
    deep_raw = generic_ingress_payload(
        request_id="deep-request",
        payload=nested_json_object(65, "leaf"),
    )
    circular_raw = generic_ingress_payload(request_id="circular-request")
    circular_payload = {}
    circular_payload["self"] = circular_payload
    circular_raw["payload"] = circular_payload
    unserializable_raw = {
        "request_id": "unserializable-request",
        "room_id": 7,
        "user_id": 3,
        "unknown": object(),
    }
    direct_raw_bytes = b"\xff" * 2_000
    direct_raw_text = "not-json" * 300
    raw_values = [
        large_unknown,
        nul_raw,
        deep_raw,
        circular_raw,
        unserializable_raw,
        direct_raw_bytes,
        direct_raw_text,
    ]

    published = []
    monkeypatch.setattr(
        worker_main,
        "publish_dlq_job",
        lambda key, payload: published.append((key, dict(payload))),
    )
    monkeypatch.setattr(worker_main, "update_request_status", lambda *_args: None)

    consumer = FakeConsumer()
    partition = TopicPartition("message-ingress", 4)
    records = {
        partition: [
            message(
                "message-ingress",
                4,
                offset,
                raw,
                key=b"7",
            )
            for offset, raw in enumerate(raw_values, start=30)
        ]
    }
    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main._handle_ingress_record,
        "ingress",
        pass_message=True,
    )

    assert consumer.seeks == []
    assert committed_offsets(consumer) == [
        (partition, offset) for offset in range(31, 38)
    ]
    assert len(published) == len(raw_values)

    diagnostic_keys = {
        "request_id",
        "room_id",
        "user_id",
        "__invalid_kafka_payload__",
        "failed_reason",
        "failed_at",
        "retry_count",
        "replay_count",
        "diagnostic_source",
        "diagnostic_size_bytes",
        "diagnostic_sha256",
        "diagnostic_preview_base64",
    }
    by_request_id = {payload["request_id"]: payload for _key, payload in published}
    for dlq_key, payload in published:
        assert set(payload) == diagnostic_keys
        assert payload["__invalid_kafka_payload__"] is True
        assert payload["failed_reason"].startswith("invalid_ingress:")
        assert "\x00" not in payload["failed_reason"]
        assert len(payload["failed_reason"]) <= len("invalid_ingress:") + 300
        assert payload["diagnostic_source"] in {
            "normalized_json",
            "raw_input_bytes",
        }
        assert len(payload["diagnostic_sha256"]) == 64
        assert len(base64.b64decode(payload["diagnostic_preview_base64"])) <= 1024
        assert len(_serialize_json(payload)) < 4096
        assert dlq_key == payload["room_id"]
        assert not set(payload).intersection({"unknown", "payload", "metadata", "body"})

    invalid_id_payload = next(
        payload
        for _key, payload in published
        if payload["request_id"].startswith("invalid-")
    )
    assert invalid_id_payload["room_id"] == 0
    assert invalid_id_payload["user_id"] is None

    large_expected = json.dumps(
        large_unknown,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    large_diagnostic = published[0][1]
    assert large_diagnostic["diagnostic_source"] == "normalized_json"
    assert large_diagnostic["diagnostic_size_bytes"] == len(large_expected)
    assert large_diagnostic["diagnostic_sha256"] == hashlib.sha256(
        large_expected
    ).hexdigest()
    assert (
        base64.b64decode(large_diagnostic["diagnostic_preview_base64"])
        == large_expected[:1024]
    )

    for raw, request_id in (
        (nul_raw, "nul-request"),
        (deep_raw, "deep-request"),
    ):
        expected = json.dumps(
            raw,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        diagnostic = by_request_id[request_id]
        assert diagnostic["diagnostic_source"] == "normalized_json"
        assert diagnostic["diagnostic_size_bytes"] == len(expected)
        assert diagnostic["diagnostic_sha256"] == hashlib.sha256(expected).hexdigest()
        assert (
            base64.b64decode(diagnostic["diagnostic_preview_base64"])
            == expected[:1024]
        )

    fallback = b"<dict:unserializable>"
    for request_id in ("circular-request", "unserializable-request"):
        diagnostic = by_request_id[request_id]
        assert diagnostic["diagnostic_source"] == "normalized_json"
        assert diagnostic["diagnostic_size_bytes"] == len(fallback)
        assert diagnostic["diagnostic_sha256"] == hashlib.sha256(fallback).hexdigest()
        assert base64.b64decode(diagnostic["diagnostic_preview_base64"]) == fallback

    for diagnostic, expected in (
        (published[-2][1], direct_raw_bytes),
        (published[-1][1], direct_raw_text.encode("utf-8")),
    ):
        assert diagnostic["diagnostic_source"] == "raw_input_bytes"
        assert diagnostic["diagnostic_size_bytes"] == len(expected)
        assert diagnostic["diagnostic_sha256"] == hashlib.sha256(expected).hexdigest()
        assert (
            base64.b64decode(diagnostic["diagnostic_preview_base64"])
            == expected[:1024]
        )


@pytest.mark.parametrize(
    "invalid_route",
    [
        "GET:/v2/streams/7/events",
        "POST:/v3/streams/7/events",
        "POST:/v2/channels/7/events",
        "POST:/v2/streams/07/events",
        "POST:/v2/streams/7/events/extra",
    ],
)
def test_worker_rejects_noncanonical_ingress_routes(invalid_route):
    with pytest.raises(ValueError, match="Invalid ingress route"):
        worker_main._validate_ingress_payload(
            generic_ingress_payload(route=invalid_route)
        )


def test_worker_rejects_route_stream_mismatch_and_v2_order_route():
    with pytest.raises(ValueError, match="route stream does not match room_id"):
        worker_main._validate_ingress_payload(
            generic_ingress_payload(route="POST:/v2/streams/8/events")
        )

    with pytest.raises(ValueError, match="V2 ingress route must use streams"):
        worker_main._validate_ingress_payload(
            generic_ingress_payload(route="POST:/v2/orders/7/events")
        )


def test_ingress_validation_returns_only_normalized_allowlisted_fields():
    queued_at = "2026-07-14T06:00:00+09:00"
    replayed_at = "2026-07-14T06:01:00+09:00"
    raw = generic_ingress_payload(
        room_seq=2,
        x_idempotency_key="idem-normalized",
        retry_count=1,
        replay_count=2,
        next_retry_at=None,
        queued_at=queued_at,
        replayed_at=replayed_at,
        payment_id="p" * 80,
        body="caller-controlled body",
        status="failed",
        persistence="persisted",
        failed_reason="forged",
        failed_at="forged",
        replayed=True,
        unknown={"large": "x" * 10_000},
    )

    normalized = worker_main._validate_ingress_payload(raw)

    assert normalized is not raw
    assert set(normalized) == {
        "request_id",
        "route",
        "room_id",
        "user_id",
        "room_seq",
        "schema_version",
        "event_type",
        "payload",
        "metadata",
        "body",
        "x_idempotency_key",
        "retry_count",
        "replay_count",
        "next_retry_at",
        "queued_at",
        "replayed_at",
        "payment_id",
    }
    assert normalized["body"] == "valid envelope"
    assert normalized["queued_at"] == queued_at
    assert normalized["replayed_at"] == replayed_at
    assert normalized["payment_id"] == "p" * 80
    for removed in (
        "stream_id",
        "actor_id",
        "status",
        "persistence",
        "failed_reason",
        "failed_at",
        "replayed",
        "unknown",
    ):
        assert removed not in normalized


@pytest.mark.parametrize("field_name", ("queued_at", "replayed_at"))
@pytest.mark.parametrize(
    "invalid_value",
    [
        "",
        "not-an-iso-timestamp",
        "2026-07-14T06:00:00",
        "2026-07-14T06:00:00+09:00\x00",
        "x" * 65,
        "10000-01-01T00:00:00+00:00",
        1_700_000_000,
    ],
)
def test_ingress_provenance_timestamps_require_bounded_aware_iso(
    field_name,
    invalid_value,
):
    with pytest.raises(ValueError, match=f"Invalid {field_name}"):
        worker_main._validate_ingress_payload(
            generic_ingress_payload(**{field_name: invalid_value})
        )


@pytest.mark.parametrize("invalid_payment_id", ["x" * 81, "pay\x00-1", 123])
def test_ingress_payment_id_is_bounded_nul_free_text(invalid_payment_id):
    with pytest.raises(ValueError, match="Invalid payment_id"):
        worker_main._validate_ingress_payload(
            generic_ingress_payload(payment_id=invalid_payment_id)
        )


def test_large_unknown_ingress_is_stripped_before_bounded_persistence_dlq(monkeypatch):
    replayed_at = "2026-07-14T06:01:00+09:00"
    raw = generic_ingress_payload(
        request_id="large-unknown-persistence",
        queued_at="2026-07-14T06:00:00+09:00",
        replayed_at=replayed_at,
        replay_count=1,
        payment_id="pay-1",
        status="persisted",
        persistence="done",
        failed_reason="forged",
        replayed=True,
        junk="x" * 900_000,
    )
    persistence_inputs = []
    published = []

    def fail_persistence(payload):
        persistence_inputs.append(dict(payload))
        raise worker_main.DataError("invalid persistence data")

    monkeypatch.setattr(worker_main, "persist_ingress_job", fail_persistence)
    monkeypatch.setattr(
        worker_main,
        "publish_dlq_job",
        lambda key, payload: published.append((key, dict(payload))),
    )
    monkeypatch.setattr(worker_main, "update_request_status", lambda *_args: None)
    monkeypatch.setattr(
        worker_main,
        "mark_inline_retry",
        lambda _payload: pytest.fail("DataError must not enter inline retry"),
    )
    monkeypatch.setattr(
        worker_main.time,
        "sleep",
        lambda _seconds: pytest.fail("DataError must not sleep"),
    )

    consumer = FakeConsumer()
    partition = TopicPartition("message-ingress", 6)
    records = {
        partition: [
            message(
                "message-ingress",
                6,
                40,
                raw,
                key=b"7",
            )
        ]
    }
    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main._handle_ingress_record,
        "ingress",
        pass_message=True,
    )

    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 41)]
    assert len(persistence_inputs) == 1
    assert len(published) == 1
    persisted = persistence_inputs[0]
    dlq_payload = published[0][1]
    for removed in (
        "junk",
        "status",
        "persistence",
        "failed_reason",
        "replayed",
    ):
        assert removed not in persisted
    assert dlq_payload["failed_reason"] == "invalid_persistence_data:DataError"
    assert dlq_payload["replayed_at"] == replayed_at
    assert "junk" not in dlq_payload
    assert len(_serialize_json(dlq_payload)) < 128 * 1024


def test_persistence_data_error_moves_to_dlq_and_commits_terminal_offset(monkeypatch):
    payload = {
        "request_id": "data-error-1",
        "route": "POST:/v2/streams/7/events",
        "stream_id": 7,
        "actor_id": 3,
        "schema_version": 2,
        "event_type": "example.created",
        "payload": {"message": "valid envelope"},
        "metadata": {},
    }
    moved = []

    def fail_persistence(_payload):
        raise worker_main.DataError("invalid database representation")

    monkeypatch.setattr(worker_main, "persist_ingress_job", fail_persistence)
    monkeypatch.setattr(
        worker_main,
        "move_to_dlq",
        lambda value, reason: moved.append((dict(value), reason)),
    )
    monkeypatch.setattr(
        worker_main,
        "mark_inline_retry",
        lambda _payload: pytest.fail("DataError must not enter inline retry"),
    )

    consumer = FakeConsumer()
    partition = TopicPartition("message-ingress", 0)
    records = {
        partition: [message("message-ingress", 0, 12, payload)],
    }
    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main.handle_ingress_job,
        "ingress",
    )

    assert moved[0][0]["request_id"] == "data-error-1"
    assert moved[0][1] == "invalid_persistence_data:DataError"
    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 13)]


def test_json_serializer_emits_real_kafka_tombstone():
    assert _serialize_json(None) is None
    assert _serialize_json({"status": "persisted"}) == b'{"status": "persisted"}'


def test_json_serializer_enforces_depth_nul_and_cycle_before_kafka_send():
    assert _serialize_json(nested_json_object(65, "leaf"))

    with pytest.raises(ValueError, match="must not exceed 65 container levels"):
        _serialize_json(nested_json_object(66, "leaf"))

    with pytest.raises(ValueError, match="must not contain NUL"):
        _serialize_json(nested_json_object(65, "before\x00after"))

    circular = {}
    circular["self"] = circular
    with pytest.raises(ValueError, match="circular references"):
        _serialize_json(circular)


def test_extreme_json_exponent_is_rejected_by_structure_decoder_and_serializer():
    from portfolio.event_envelope import validate_json_structure

    decoded_number = json.loads("1e1000000")
    assert decoded_number == float("inf")

    with pytest.raises(ValueError, match="JSON numbers must be finite"):
        validate_json_structure({"value": decoded_number})

    invalid = _deserialize_json(b'{"value":1e1000000}')
    assert isinstance(invalid, InvalidKafkaPayload)
    assert invalid["decode_error"] == "ValueError"

    with pytest.raises(ValueError, match="JSON numbers must be finite"):
        _serialize_json({"value": decoded_number})


def test_json_unicode_scalar_validation_rejects_lone_surrogates_and_allows_emoji():
    from portfolio.event_envelope import validate_json_structure

    for value in (
        {"value": "\ud800"},
        {"\udfff": "value"},
    ):
        with pytest.raises(ValueError, match="valid Unicode scalars"):
            validate_json_structure(value)
        with pytest.raises(ValueError, match="valid Unicode scalars"):
            _serialize_json(value)

    for raw in (
        b'{"value":"\\ud800"}',
        b'{"\\udfff":"value"}',
    ):
        invalid = _deserialize_json(raw)
        assert isinstance(invalid, InvalidKafkaPayload)
        assert invalid["decode_error"] == "ValueError"

    decoded = _deserialize_json(b'{"value":"\\ud83d\\ude00"}')
    assert decoded == {"value": "😀"}
    assert _serialize_json(decoded)

    with pytest.raises(ValueError, match="valid Unicode scalars"):
        worker_main._validate_ingress_payload(
            generic_ingress_payload(payload={"value": "\ud800"})
        )
    accepted = worker_main._validate_ingress_payload(
        generic_ingress_payload(payload={"value": "😀"})
    )
    assert accepted["payload"] == {"value": "😀"}


def test_shared_kafka_producer_preserves_idempotent_ordering_without_linger(monkeypatch):
    import kafka
    from portfolio import kafka_client

    captured = {}

    def fake_producer(**kwargs):
        captured.update(kwargs)
        return object()

    kafka_client.get_kafka_producer.cache_clear()
    monkeypatch.setattr(kafka, "KafkaProducer", fake_producer)
    kafka_client.get_kafka_producer()
    kafka_client.get_kafka_producer.cache_clear()

    assert captured["enable_idempotence"] is True
    assert captured["acks"] == "all"
    assert captured["retries"] == 3
    assert captured["max_in_flight_requests_per_connection"] == 1
    assert captured["linger_ms"] == 0


def test_ingress_producer_keeps_short_batch_window(monkeypatch):
    import kafka
    from portfolio import kafka_client

    captured = {}

    def fake_producer(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(kafka, "KafkaProducer", fake_producer)
    kafka_client.get_kafka_ingress_producer.cache_clear()
    kafka_client.get_kafka_ingress_producer()
    kafka_client.get_kafka_ingress_producer.cache_clear()

    assert captured["enable_idempotence"] is True
    assert captured["acks"] == "all"
    assert captured["retries"] == 3
    assert captured["max_in_flight_requests_per_connection"] == 1
    assert captured["linger_ms"] == 5


def test_ingress_consumer_preserves_raw_key_for_record_validation(monkeypatch):
    import kafka
    from portfolio import kafka_client

    captured = {}

    def fake_consumer(*topics, **kwargs):
        captured["topics"] = topics
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(kafka, "KafkaConsumer", fake_consumer)
    kafka_client.build_ingress_consumer()

    assert captured["topics"] == (kafka_client.settings.kafka_ingress_topic,)
    assert "key_deserializer" not in captured["kwargs"]
    assert captured["kwargs"]["value_deserializer"] is _deserialize_json


def test_non_ingress_consumers_use_fail_closed_utf8_key_deserializer(monkeypatch):
    import kafka
    from portfolio import kafka_client

    captured = []

    def fake_consumer(*topics, **kwargs):
        captured.append((topics, kwargs))
        return object()

    monkeypatch.setattr(kafka, "KafkaConsumer", fake_consumer)
    kafka_client.build_dlq_consumer()
    kafka_client.build_notification_consumer()

    assert _deserialize_utf8_key(None) is None
    assert _deserialize_utf8_key(b"stream-7") == "stream-7"
    assert _deserialize_utf8_key(b"\xff") is None
    assert len(captured) == 2
    assert all(
        kwargs["key_deserializer"] is _deserialize_utf8_key
        for _topics, kwargs in captured
    )
    assert all(
        kwargs["fetch_max_bytes"] == 50 * 1024 * 1024
        and kwargs["receive_message_max_bytes"] == 64 * 1024 * 1024
        for _topics, kwargs in captured
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"message_id": "wrong", "room_id": 1},
        {"message_id": True, "room_id": 1},
        {"message_id": 1, "room_id": 0},
    ],
)
def test_invalid_notification_is_terminal_rejection(payload):
    assert worker_main.handle_notification_job(payload) == "rejected"


@pytest.mark.parametrize(
    ("case_name", "payload"),
    [
        (
            "event_alias_conflict",
            notification_payload(message_id=11),
        ),
        (
            "stream_alias_conflict",
            notification_payload(room_id=8),
        ),
        ("event_zero", notification_payload(event_id=0)),
        ("event_bool", notification_payload(event_id=True)),
        (
            "event_bigint_overflow",
            notification_payload(event_id=9_223_372_036_854_775_808),
        ),
        ("stream_zero", notification_payload(stream_id=0)),
        ("stream_bool", notification_payload(stream_id=True)),
        (
            "stream_bigint_overflow",
            notification_payload(stream_id=9_223_372_036_854_775_808),
        ),
        ("event_type_nul", notification_payload(event_type="bad\x00type")),
        ("event_type_oversized", notification_payload(event_type="x" * 51)),
        ("body_preview_nul", notification_payload(body_preview="bad\x00body")),
        ("body_preview_oversized", notification_payload(body_preview="x" * 31)),
        (
            "payload_preview_nul",
            notification_payload(payload_preview="bad\x00preview"),
        ),
        (
            "payload_preview_oversized",
            notification_payload(payload_preview="x" * 121),
        ),
        ("metadata_nonobject", notification_payload(metadata=[])),
        (
            "metadata_depth",
            notification_payload(metadata=nested_json_object(65, "leaf")),
        ),
        (
            "metadata_nonfinite",
            notification_payload(metadata={"value": json.loads("1e1000000")}),
        ),
        (
            "metadata_oversized",
            notification_payload(metadata={"value": "가" * 5_500}),
        ),
        (
            "metadata_lone_surrogate_value",
            notification_payload(metadata={"value": "\ud800"}),
        ),
        (
            "metadata_lone_surrogate_key",
            notification_payload(metadata={"\udfff": "value"}),
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_invalid_notification_is_rejected_and_exact_offset_committed(
    monkeypatch,
    case_name,
    payload,
):
    monkeypatch.setattr(
        worker_main,
        "store_attempt",
        lambda _payload: pytest.fail(f"{case_name} must not reach PostgreSQL"),
    )
    consumer = FakeConsumer()
    partition = TopicPartition("message-notifications", 1)
    records = {
        partition: [message("message-notifications", 1, 12, payload)]
    }

    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main.handle_notification_job,
        "notification",
    )

    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 13)]


def test_notification_huge_unknown_is_stripped_before_normalized_success(monkeypatch):
    stored = []
    raw = notification_payload(
        metadata={"emoji": "😀"},
        unknown="x" * 900_000,
        status="forged",
    )
    monkeypatch.setattr(
        worker_main,
        "store_attempt",
        lambda payload: stored.append(dict(payload)),
    )
    consumer = FakeConsumer()
    partition = TopicPartition("message-notifications", 2)
    records = {
        partition: [message("message-notifications", 2, 20, raw)]
    }

    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main.handle_notification_job,
        "notification",
    )

    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 21)]
    assert len(stored) == 1
    assert stored[0] == {
        "event_id": 10,
        "stream_id": 7,
        "message_id": 10,
        "room_id": 7,
        "event_type": "example.created",
        "payload_preview": "event preview",
        "metadata": {"emoji": "😀"},
        "body_preview": "event body",
    }
    assert len(_serialize_json(stored[0])) < 128 * 1024


def test_legacy_notification_aliases_are_normalized_and_committed(monkeypatch):
    stored = []
    raw = {
        "message_id": 12,
        "room_id": 7,
        "body_preview": "legacy body",
    }
    monkeypatch.setattr(
        worker_main,
        "store_attempt",
        lambda payload: stored.append(dict(payload)),
    )
    consumer = FakeConsumer()
    partition = TopicPartition("message-notifications", 3)
    records = {
        partition: [message("message-notifications", 3, 30, raw)]
    }

    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main.handle_notification_job,
        "notification",
    )

    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 31)]
    assert stored == [
        {
            "event_id": 12,
            "stream_id": 7,
            "message_id": 12,
            "room_id": 7,
            "event_type": "legacy.message",
            "payload_preview": "legacy body",
            "metadata": {},
            "body_preview": "legacy body",
        }
    ]


def test_notification_data_error_is_terminal_without_reconnect_or_sleep(monkeypatch):
    attempts = []

    def fail_connection():
        attempts.append("db")
        raise worker_main.DataError("invalid notification persistence data")

    monkeypatch.setattr(worker_main, "get_conn", fail_connection)
    monkeypatch.setattr(
        worker_main,
        "reconnect_pool",
        lambda: pytest.fail("DataError must not reconnect"),
    )
    monkeypatch.setattr(
        worker_main.time,
        "sleep",
        lambda _seconds: pytest.fail("DataError must not sleep"),
    )
    consumer = FakeConsumer()
    partition = TopicPartition("message-notifications", 4)
    records = {
        partition: [
            message("message-notifications", 4, 40, notification_payload())
        ]
    }

    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main.handle_notification_job,
        "notification",
    )

    assert attempts == ["db"]
    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 41)]


def test_notification_operational_error_retries_then_rewinds_partition(monkeypatch):
    attempts = []
    reconnects = []
    sleeps = []

    def unavailable_connection():
        attempts.append("db")
        raise worker_main.OperationalError("database unavailable")

    monkeypatch.setattr(worker_main, "get_conn", unavailable_connection)
    monkeypatch.setattr(
        worker_main,
        "reconnect_pool",
        lambda: reconnects.append("reconnect"),
    )
    monkeypatch.setattr(worker_main.time, "sleep", lambda seconds: sleeps.append(seconds))
    consumer = FakeConsumer()
    partition = TopicPartition("message-notifications", 5)
    records = {
        partition: [
            message("message-notifications", 5, 50, notification_payload())
        ]
    }

    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main.handle_notification_job,
        "notification",
    )

    assert attempts == ["db", "db"]
    assert reconnects == ["reconnect"]
    assert sleeps == [1, 1]
    assert consumer.commits == []
    assert consumer.seeks == [(partition, 50)]


@pytest.mark.parametrize(
    "payload",
    [
        notification_payload(event_id=999),
        notification_payload(stream_id=8),
    ],
    ids=("target_missing", "room_mismatch"),
)
def test_notification_missing_target_is_terminal_without_reconnect(
    monkeypatch,
    payload,
):
    class FakeConn:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, _exc, _tb):
            if exc_type is not None:
                self.rollbacks += 1
            return False

        def commit(self):
            self.commits += 1

    class MissingCursor:
        def __init__(self):
            self.params = None

        def execute(self, _sql, params=None):
            self.params = params

        def fetchone(self):
            return {"target_exists": False, "inserted": False}

    conn = FakeConn()
    cursor = MissingCursor()

    @contextmanager
    def fake_cursor(_conn):
        yield cursor

    monkeypatch.setattr(worker_main, "get_conn", lambda: conn)
    monkeypatch.setattr(worker_main, "get_cursor", fake_cursor)
    monkeypatch.setattr(
        worker_main,
        "reconnect_pool",
        lambda: pytest.fail("missing notification target must not reconnect"),
    )
    monkeypatch.setattr(
        worker_main.time,
        "sleep",
        lambda _seconds: pytest.fail("missing notification target must not sleep"),
    )
    consumer = FakeConsumer()
    partition = TopicPartition("message-notifications", 6)
    records = {
        partition: [message("message-notifications", 6, 60, payload)]
    }

    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main.handle_notification_job,
        "notification",
    )

    assert cursor.params[:2] == (payload["event_id"], payload["stream_id"])
    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 61)]


@pytest.mark.parametrize("inserted", [True, False], ids=("new", "duplicate"))
def test_notification_existing_target_new_or_duplicate_is_success(
    monkeypatch,
    inserted,
):
    class FakeConn:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, _exc, _tb):
            if exc_type is not None:
                self.rollbacks += 1
            return False

        def commit(self):
            self.commits += 1

    class ExistingCursor:
        def execute(self, _sql, _params=None):
            pass

        def fetchone(self):
            return {"target_exists": True, "inserted": inserted}

    conn = FakeConn()

    @contextmanager
    def fake_cursor(_conn):
        yield ExistingCursor()

    monkeypatch.setattr(worker_main, "get_conn", lambda: conn)
    monkeypatch.setattr(worker_main, "get_cursor", fake_cursor)
    monkeypatch.setattr(
        worker_main,
        "reconnect_pool",
        lambda: pytest.fail("valid notification target must not reconnect"),
    )
    monkeypatch.setattr(
        worker_main.time,
        "sleep",
        lambda _seconds: pytest.fail("valid notification target must not sleep"),
    )
    consumer = FakeConsumer()
    partition = TopicPartition("message-notifications", 7)
    records = {
        partition: [
            message("message-notifications", 7, 70, notification_payload())
        ]
    }

    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main.handle_notification_job,
        "notification",
    )

    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 71)]


def test_notification_batch_uses_one_store_call_and_commits_each_record(monkeypatch):
    stored_batches = []

    def store_batch(payloads):
        stored_batches.append(payloads)
        return {(payload["event_id"], payload["stream_id"]) for payload in payloads}

    monkeypatch.setattr(worker_main, "store_attempt_batch", store_batch)
    consumer = FakeConsumer()
    partition = TopicPartition("message-notifications", 0)
    records = {
        partition: [
            message(
                "message-notifications",
                0,
                10,
                notification_payload(event_id=10),
            ),
            message(
                "message-notifications",
                0,
                11,
                notification_payload(event_id=11),
            ),
        ]
    }

    worker_main._process_notification_batch(consumer, records)

    assert len(stored_batches) == 1
    assert [payload["event_id"] for payload in stored_batches[0]] == [10, 11]
    assert committed_offsets(consumer) == [(partition, 11), (partition, 12)]
    assert consumer.seeks == []


def test_notification_batch_commits_invalid_and_missing_targets(monkeypatch):
    monkeypatch.setattr(worker_main, "store_attempt_batch", lambda _payloads: set())
    consumer = FakeConsumer()
    partition = TopicPartition("message-notifications", 1)
    records = {
        partition: [
            message("message-notifications", 1, 20, "invalid-json"),
            message(
                "message-notifications",
                1,
                21,
                notification_payload(event_id=999),
            ),
        ]
    }

    worker_main._process_notification_batch(consumer, records)

    assert committed_offsets(consumer) == [(partition, 21), (partition, 22)]
    assert consumer.seeks == []


def test_notification_batch_database_failure_rewinds_each_partition(monkeypatch):
    def fail_batch(_payloads):
        raise worker_main.OperationalError("database unavailable")

    monkeypatch.setattr(worker_main, "store_attempt_batch", fail_batch)
    monkeypatch.setattr(worker_main.time, "sleep", lambda _seconds: None)
    consumer = FakeConsumer()
    partition_0 = TopicPartition("message-notifications", 0)
    partition_1 = TopicPartition("message-notifications", 1)
    records = {
        partition_0: [
            message("message-notifications", 0, 30, notification_payload(event_id=30))
        ],
        partition_1: [
            message("message-notifications", 1, 40, notification_payload(event_id=40))
        ],
    }

    worker_main._process_notification_batch(consumer, records)

    assert consumer.commits == []
    assert consumer.seeks == [(partition_0, 30), (partition_1, 40)]


def test_notification_batch_insert_uses_one_values_statement(monkeypatch):
    calls = []

    def fake_execute_values(cur, sql, rows, **kwargs):
        calls.append((cur, sql, rows, kwargs))
        return [{"event_id": 10, "stream_id": 7}, {"event_id": 11, "stream_id": 7}]

    monkeypatch.setattr(worker_main, "execute_values", fake_execute_values)
    cursor = object()
    payloads = [notification_payload(event_id=10), notification_payload(event_id=11)]

    targets = worker_main.insert_notification_attempt_batch(cursor, payloads)

    assert targets == {(10, 7), (11, 7)}
    assert len(calls) == 1
    _cursor, sql, rows, kwargs = calls[0]
    assert "WITH incoming" in sql
    assert "ON CONFLICT (message_id) DO NOTHING" in sql
    assert len(rows) == 2
    assert kwargs == {
        "template": "(%s, %s, %s::jsonb)",
        "page_size": 2,
        "fetch": True,
    }


def test_invalid_dlq_payload_is_skipped_instead_of_blocking_partition():
    assert dlq_replayer.replay_one({"request_id": "missing-room"}) is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("room_id", 7.9),
        ("room_id", "7"),
        ("replay_count", 0.9),
        ("replay_count", "0"),
        ("replay_count", True),
    ],
)
def test_automatic_dlq_replay_rejects_coercible_numeric_fields(field, value):
    payload = {"request_id": "req-strict", "room_id": 7, "replay_count": 0}
    payload[field] = value

    assert dlq_replayer.replay_one(payload) is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", ""),
        ("request_id", "x" * 81),
        ("request_id", "bad\x00id"),
        ("request_id", "bad\ud800id"),
        ("room_id", 0),
        ("room_id", -1),
        ("room_id", 9_223_372_036_854_775_808),
        ("replay_count", -1),
        ("replay_count", 9_223_372_036_854_775_808),
    ],
)
def test_automatic_dlq_replay_rejects_out_of_range_identity_fields(field, value):
    payload = {"request_id": "req-strict", "room_id": 7, "replay_count": 0}
    payload[field] = value

    assert dlq_replayer.replay_one(payload) is False


def test_automatic_dlq_replay_rejects_huge_json_integer_text():
    raw = '{"request_id":"req-huge","room_id":7,"replay_count":' + ("9" * 5000) + "}"

    assert dlq_replayer.replay_one(raw) is False


def test_automatic_dlq_replay_refuses_counter_overflow(monkeypatch):
    payload = {
        "request_id": "req-overflow",
        "room_id": 7,
        "replay_count": 9_223_372_036_854_775_807,
    }
    monkeypatch.setattr(
        dlq_replayer,
        "settings",
        SimpleNamespace(dlq_replay_max_count=9_223_372_036_854_775_808),
    )
    monkeypatch.setattr(
        dlq_replayer,
        "claim_dlq_replay",
        lambda *_args: pytest.fail("overflow payload must not create a replay claim"),
    )

    assert dlq_replayer.replay_one(payload) is False


def test_dlq_batch_pauses_before_processing_when_database_is_unavailable(monkeypatch):
    consumer = FakeConsumer()
    partition = TopicPartition("message-ingress-dlq", 0)
    records = {
        partition: [message("message-ingress-dlq", 0, 3, {"request_id": "req-1"})]
    }
    monkeypatch.setattr(dlq_replayer, "ping_db", lambda: False)

    with pytest.raises(dlq_replayer.ReplayDatabaseUnavailable):
        dlq_replayer._process_replay_batch(consumer, records)

    assert consumer.commits == []
    assert consumer.seeks == []


def test_automatic_dlq_replay_respects_shared_claim_ownership(monkeypatch):
    payload = {"request_id": "req-1", "room_id": 7, "replay_count": 0}
    published = []
    finalized = []
    monkeypatch.setattr(
        dlq_replayer,
        "claim_dlq_replay",
        lambda _request_id, _generation: ("claimed", "owner-1"),
    )
    monkeypatch.setattr(
        dlq_replayer,
        "publish_ingress_job",
        lambda key, value: published.append((key, value)),
    )
    monkeypatch.setattr(
        dlq_replayer,
        "mark_dlq_replay_published",
        lambda request_id, generation, owner: finalized.append(
            (request_id, generation, owner)
        )
        or True,
    )

    assert dlq_replayer.replay_one(dict(payload)) is True
    assert published[0][1]["replay_count"] == 1
    assert finalized == [("req-1", 0, "owner-1")]

    monkeypatch.setattr(
        dlq_replayer,
        "claim_dlq_replay",
        lambda _request_id, _generation: ("in_progress", None),
    )
    with pytest.raises(dlq_replayer.ReplayClaimInProgress):
        dlq_replayer.replay_one(dict(payload))


def test_ingress_handler_returns_outcomes_and_hides_authorization_detail(monkeypatch):
    statuses = []
    payload = {
        "request_id": "req-1",
        "room_id": 7,
        "user_id": 3,
        "route": "POST:/v1/streams/7/events",
        "body": "hello",
    }

    monkeypatch.setattr(
        worker_main,
        "persist_ingress_job",
        lambda _payload: {"id": 1},
    )
    assert worker_main.handle_ingress_job(dict(payload)) == "success"

    def reject(_payload):
        raise worker_main.IngressAuthorizationError("stream_not_found")

    monkeypatch.setattr(worker_main, "persist_ingress_job", reject)
    monkeypatch.setattr(
        worker_main,
        "update_request_status",
        lambda request_id, value: statuses.append((request_id, value)),
    )
    assert worker_main.handle_ingress_job(dict(payload)) == "rejected"
    assert statuses == [
        (
            "req-1",
            {
                "request_id": "req-1",
                "status": "failed",
                "room_id": 7,
                "user_id": 3,
                "failed_reason": "Event authorization rejected",
            },
        )
    ]

    moved = []

    def sequence_gap(_payload):
        raise worker_main.RoomSequenceGapError("gap")

    monkeypatch.setattr(worker_main, "persist_ingress_job", sequence_gap)
    monkeypatch.setattr(
        worker_main,
        "move_to_dlq",
        lambda value, reason: moved.append((value, reason)),
    )
    dlq_payload = dict(payload, retry_count=worker_main.settings.ingress_max_retries)
    assert worker_main.handle_ingress_job(dlq_payload) == "dlq"
    assert moved[0][1] == "room_sequence_gap"


def test_ingress_authorization_detail_is_logged_but_not_public(caplog):
    with caplog.at_level("WARNING"):
        with pytest.raises(
            worker_main.IngressAuthorizationError,
            match="membership_missing",
        ):
            worker_main._reject_ingress_authorization(
                "membership_missing",
                request_id="req-1",
                room_id=7,
                user_id=3,
            )

    assert "reason=membership_missing" in caplog.text
    assert "request_id=req-1" in caplog.text


@pytest.mark.parametrize(
    ("base_delay", "retry_count"),
    [
        (float("nan"), 0),
        (float("inf"), 0),
        (-1.0, 0),
        (0.0, 0),
        (1e308, 0),
        (1.0, 10**100),
    ],
)
def test_inline_retry_delay_is_finite_bounded_and_timestamp_safe(
    monkeypatch,
    base_delay,
    retry_count,
):
    import math

    now = 1_700_000_000.0
    statuses = []
    sleeps = []
    monkeypatch.setattr(
        worker_main,
        "settings",
        SimpleNamespace(ingress_retry_base_delay_seconds=base_delay),
    )
    monkeypatch.setattr(
        worker_main,
        "time",
        SimpleNamespace(
            time=lambda: now,
            sleep=lambda seconds: sleeps.append(seconds),
        ),
    )
    monkeypatch.setattr(
        worker_main,
        "update_request_status",
        lambda request_id, status: statuses.append((request_id, dict(status))),
    )
    payload = {
        "request_id": "retry-boundary",
        "room_id": 7,
        "user_id": 3,
        "retry_count": retry_count,
    }

    delay = worker_main.mark_inline_retry(payload)
    worker_main.time.sleep(delay)

    assert math.isfinite(delay)
    assert 0 < delay <= 60
    assert payload["retry_count"] == retry_count + 1
    assert math.isfinite(payload["next_retry_at"])
    assert payload["next_retry_at"] == pytest.approx(now + delay)
    assert len(statuses) == 1
    request_id, status = statuses[0]
    assert request_id == "retry-boundary"
    assert status["retry_count"] == retry_count + 1
    parsed_next_retry = datetime.fromisoformat(status["next_retry_at"])
    assert parsed_next_retry.timestamp() == pytest.approx(now + delay)
    assert sleeps == [delay]


@pytest.mark.parametrize(
    ("payload", "expected_owner"),
    [
        ({"status": "queued", "actor_id": 3}, 3),
        ({"status": "queued", "user_id": 3}, 3),
        ({"status": "queued", "actor_id": 3, "user_id": 3}, 3),
    ],
)
def test_request_status_normalization_unifies_owner_aliases(payload, expected_owner):
    from portfolio import state_store

    owner_id, normalized = state_store._normalized_request_status("req-owner", payload)

    assert owner_id == expected_owner
    assert normalized["request_id"] == "req-owner"
    assert normalized["actor_id"] == expected_owner
    assert normalized["user_id"] == expected_owner


@pytest.mark.parametrize(
    ("request_id", "payload", "error"),
    [
        ("", {"user_id": 3}, "Invalid request status request_id"),
        (
            "req-owner",
            {"request_id": "other", "user_id": 3},
            "request_id mismatch",
        ),
        ("req-owner", {"status": "queued"}, "owner is missing"),
        ("req-owner", {"user_id": True}, "Invalid request status user_id"),
        ("req-owner", {"actor_id": 0}, "Invalid request status actor_id"),
        (
            "req-owner",
            {"actor_id": 3, "user_id": 4},
            "Conflicting request status actor_id/user_id",
        ),
        (
            "req-owner",
            {"user_id": 3, "lag": json.loads("1e1000000")},
            "JSON numbers must be finite",
        ),
    ],
)
def test_request_status_normalization_rejects_invalid_identity_and_json(
    request_id,
    payload,
    error,
):
    from portfolio import state_store

    with pytest.raises(ValueError, match=error):
        state_store._normalized_request_status(request_id, payload)


@pytest.mark.parametrize(
    ("existing_owner", "expect_conflict"),
    [(None, False), (3, False), (4, True)],
    ids=("legacy_null_adopt", "same_owner", "other_owner_race"),
)
def test_request_status_upsert_adopts_only_null_or_same_owner(
    existing_owner,
    expect_conflict,
):
    from portfolio import state_store

    class FakeCursor:
        def __init__(self):
            self.sql = ""
            self.params = None

        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params

        def fetchone(self):
            if existing_owner is None or existing_owner == 3:
                return {"user_id": 3}
            return None

    cursor = FakeCursor()
    call = lambda: state_store.upsert_request_status(
        cursor,
        "req-owner",
        {"status": "queued", "user_id": 3},
    )

    if expect_conflict:
        with pytest.raises(state_store.RequestStatusOwnerConflict):
            call()
    else:
        normalized = call()
        assert normalized["actor_id"] == 3
        assert normalized["user_id"] == 3

    assert "WHERE request_statuses.user_id IS NULL" in cursor.sql
    assert "request_statuses.user_id = EXCLUDED.user_id" in cursor.sql
    assert "RETURNING user_id" in cursor.sql
    assert cursor.params[:2] == ("req-owner", 3)


def test_request_status_owner_conflict_rolls_back_transaction():
    from portfolio import state_store

    class FakeConn:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, _exc, _tb):
            if exc_type is not None:
                self.rollbacks += 1
            return False

        def commit(self):
            self.commits += 1

    class ConflictCursor:
        def execute(self, _sql, _params=None):
            pass

        def fetchone(self):
            return None

    conn = FakeConn()

    @contextmanager
    def fake_cursor(_conn):
        yield ConflictCursor()

    original_get_conn = state_store.get_conn
    original_get_cursor = state_store.get_cursor
    state_store.get_conn = lambda: conn
    state_store.get_cursor = fake_cursor
    try:
        with pytest.raises(state_store.RequestStatusOwnerConflict):
            state_store.store_request_status(
                "req-owner",
                {"status": "queued", "user_id": 3},
            )
    finally:
        state_store.get_conn = original_get_conn
        state_store.get_cursor = original_get_cursor

    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_request_status_store_skips_owner_and_database_failures(
    monkeypatch,
):
    from portfolio import state_store

    stored_calls = []
    stored = {
        "request_id": "req-owner",
        "status": "queued",
        "user_id": 3,
        "actor_id": 3,
    }
    monkeypatch.setattr(
        worker_main,
        "store_request_status",
        lambda request_id, payload: stored_calls.append((request_id, dict(payload))) or dict(stored),
    )

    worker_main.update_request_status(
        "req-owner",
        {"status": "forged-input", "user_id": 3},
    )
    assert stored_calls == [("req-owner", {"status": "forged-input", "user_id": 3})]

    for failure in (
        state_store.RequestStatusOwnerConflict("owner race"),
        ValueError("invalid status"),
        RuntimeError("database down"),
    ):
        monkeypatch.setattr(
            worker_main,
            "store_request_status",
            lambda *_args, failure=failure: (_ for _ in ()).throw(failure),
        )
        worker_main.update_request_status(
            "req-owner",
            {"status": "queued", "user_id": 3},
        )

    assert len(stored_calls) == 1


def test_status_owner_conflict_still_allows_terminal_ingress_offset_commit(monkeypatch):
    from portfolio import state_store

    dlq = []
    monkeypatch.setattr(
        worker_main,
        "persist_ingress_job",
        lambda _payload: (_ for _ in ()).throw(worker_main.DataError("invalid row")),
    )
    monkeypatch.setattr(
        worker_main,
        "store_request_status",
        lambda *_args: (_ for _ in ()).throw(
            state_store.RequestStatusOwnerConflict("owner race")
        ),
    )
    monkeypatch.setattr(
        worker_main,
        "publish_dlq_job",
        lambda key, payload: dlq.append((key, dict(payload))),
    )
    consumer = FakeConsumer()
    partition = TopicPartition("message-ingress", 7)
    records = {
        partition: [
            message(
                "message-ingress",
                7,
                70,
                generic_ingress_payload(request_id="owner-race-terminal"),
                key=b"7",
            )
        ]
    }

    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main._handle_ingress_record,
        "ingress",
        pass_message=True,
    )

    assert dlq[0][1]["failed_reason"] == "invalid_persistence_data:DataError"
    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 71)]


@pytest.mark.parametrize(
    "existing_identity",
    [
        {"room_id": 8, "user_id": 3},
        {"room_id": 7, "user_id": 4},
    ],
    ids=("different_room", "different_user"),
)
def test_existing_request_identity_conflict_rolls_back_and_terminally_commits(
    monkeypatch,
    existing_identity,
):
    class FakeConn:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, _exc, _tb):
            if exc_type is not None:
                self.rollbacks += 1
            return False

        def commit(self):
            self.commits += 1

    class ExistingCursor:
        def execute(self, _sql, _params=None):
            pass

        def fetchone(self):
            return dict(existing_identity)

    conn = FakeConn()
    statuses = []

    @contextmanager
    def fake_cursor(_conn):
        yield ExistingCursor()

    monkeypatch.setattr(worker_main, "get_conn", lambda: conn)
    monkeypatch.setattr(worker_main, "get_cursor", fake_cursor)
    monkeypatch.setattr(
        worker_main,
        "update_request_status",
        lambda request_id, payload: statuses.append((request_id, dict(payload))),
    )
    consumer = FakeConsumer()
    partition = TopicPartition("message-ingress", 7)
    records = {
        partition: [
            message(
                "message-ingress",
                7,
                71,
                generic_ingress_payload(request_id="identity-conflict"),
                key=b"7",
            )
        ]
    }

    worker_main._process_worker_batch(
        consumer,
        records,
        worker_main._handle_ingress_record,
        "ingress",
        pass_message=True,
    )

    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert statuses[0][1]["failed_reason"] == "Event authorization rejected"
    assert consumer.seeks == []
    assert committed_offsets(consumer) == [(partition, 72)]


@pytest.mark.parametrize(
    ("authorization", "expected_reason"),
    [
        (
            {"stream_exists": False, "actor_exists": True, "member_exists": True},
            "stream_not_found",
        ),
        (
            {"stream_exists": True, "actor_exists": False, "member_exists": True},
            "actor_not_found",
        ),
        (
            {"stream_exists": True, "actor_exists": True, "member_exists": False},
            "membership_missing",
        ),
    ],
)
def test_worker_combined_authorization_read_preserves_rejection_reason(
    authorization,
    expected_reason,
):
    class FakeCursor:
        def __init__(self):
            self.rows = [None, authorization]

        def execute(self, _sql, _params=None):
            pass

        def fetchone(self):
            return self.rows.pop(0)

    with pytest.raises(worker_main.IngressAuthorizationError, match=expected_reason):
        worker_main._persist_message_with_cursor(
            {
                "route": "POST:/v2/streams/7/events",
                "request_id": "req-auth",
                "room_id": 7,
                "user_id": 3,
                "event_type": "example.created",
                "schema_version": 2,
                "payload": {"message": "auth"},
                "metadata": {},
            },
            FakeCursor(),
        )


def test_worker_persists_generic_envelope_without_projecting_order_columns():
    class FakeCursor:
        def __init__(self):
            self.executed = []
            self.rows = [
                None,
                {
                    "stream_exists": True,
                    "actor_exists": True,
                    "member_exists": True,
                },
                {"last_seq": 1},
                {
                    "id": 10,
                    "request_id": "req-generic",
                    "room_id": 7,
                    "user_id": 3,
                    "event_type": "sensor.threshold.exceeded",
                    "category": None,
                    "payment_id": None,
                    "schema_version": 2,
                    "payload": {"message": "temperature high", "value": 88},
                    "metadata": {"classification": "alert", "site": "seoul"},
                    "body": "temperature high",
                    "room_seq": 1,
                    "created_at": datetime(2026, 7, 14, tzinfo=timezone.utc),
                },
            ]

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

        def fetchone(self):
            return self.rows.pop(0)

    cursor = FakeCursor()
    response = worker_main._persist_message_with_cursor(
        {
            "route": "POST:/v2/streams/7/events",
            "request_id": "req-generic",
            "room_id": 7,
            "user_id": 3,
            "event_type": "sensor.threshold.exceeded",
            "schema_version": 2,
            "payload": {"message": "temperature high", "value": 88},
            "metadata": {"classification": "alert", "site": "seoul"},
        },
        cursor,
    )

    insert_params = next(
        params
        for sql, params in cursor.executed
        if "INSERT INTO messages (" in sql
    )
    assert insert_params[:7] == (
        "req-generic",
        7,
        3,
        "sensor.threshold.exceeded",
        None,
        None,
        2,
    )
    assert json.loads(insert_params[7]) == {"message": "temperature high", "value": 88}
    assert json.loads(insert_params[8]) == {"classification": "alert", "site": "seoul"}
    assert insert_params[9:] == ("temperature high", 1)
    assert response["schema_version"] == 2
    assert response["payload"] == {"message": "temperature high", "value": 88}
    assert response["metadata"] == {"classification": "alert", "site": "seoul"}
    assert response["category"] is None
    assert "persisted_at" not in response

    consistency_reads = [sql for sql, _params in cursor.executed if "SELECT" in sql]
    assert consistency_reads
    assert all("/*NO LOAD BALANCE*/" in sql for sql in consistency_reads)


def test_worker_reclassifies_deprecated_order_adapter_payload_before_insert():
    class FakeCursor:
        def __init__(self):
            self.executed = []
            self.rows = [
                None,
                {
                    "stream_exists": True,
                    "actor_exists": True,
                    "member_exists": True,
                },
                {"last_seq": 1},
                {
                    "id": 10,
                    "request_id": "req-order",
                    "room_id": 7,
                    "user_id": 3,
                    "event_type": "payment_completed",
                    "category": "payment",
                    "payment_id": "pay-1",
                    "schema_version": 1,
                    "payload": {"text": "paid"},
                    "metadata": {
                        "classification": "payment",
                        "external_references": {"payment": "pay-1"},
                    },
                    "body": "paid",
                    "room_seq": 1,
                    "created_at": datetime(2026, 7, 14, tzinfo=timezone.utc),
                },
            ]

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

        def fetchone(self):
            return self.rows.pop(0)

    cursor = FakeCursor()
    response = worker_main._persist_message_with_cursor(
        {
            "route": "POST:/v1/orders/7/events",
            "request_id": "req-order",
            "room_id": 7,
            "user_id": 3,
            "event_type": "payment_completed",
            "category": "forged-category",
            "payment_id": "pay-1",
            "body": "paid",
        },
        cursor,
    )

    insert_params = next(
        params for sql, params in cursor.executed if "INSERT INTO messages (" in sql
    )
    assert insert_params[4] == "payment"
    assert json.loads(insert_params[8])["classification"] == "payment"
    assert response["category"] == "payment"


def test_worker_serializes_idempotency_key_before_reading_cached_response():
    class FakeCursor:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

        def fetchone(self):
            return {
                "response_json": {
                    "id": 10,
                    "request_id": "original-request",
                    "room_id": 7,
                    "room_seq": 1,
                    "user_id": 3,
                    "body": "original",
                    "created_at": "2026-07-14T00:00:00+00:00",
                }
            }

    cursor = FakeCursor()
    response = worker_main._persist_message_with_cursor(
        {
            "route": "POST:/v1/streams/7/events",
            "request_id": "duplicate-request",
            "room_id": 7,
            "user_id": 3,
            "body": "duplicate",
            "x_idempotency_key": "idem-1",
        },
        cursor,
    )

    assert "pg_advisory_xact_lock" in cursor.executed[0][0]
    advisory_key = cursor.executed[0][1][0]
    assert json.loads(advisory_key) == [
        "POST:/v1/streams/7/events|actor:3",
        "idem-1",
    ]
    assert "\x00" not in advisory_key
    adapted_advisory_key = adapt(advisory_key).getquoted()
    assert isinstance(adapted_advisory_key, bytes)
    assert b"\x00" not in adapted_advisory_key
    assert "SELECT response_json" in cursor.executed[1][0]
    assert cursor.executed[1][1] == (
        "POST:/v1/streams/7/events|actor:3",
        "idem-1",
    )
    assert response["_idempotency_hit"] is True


def test_worker_adopts_same_actor_legacy_idempotency_and_ignores_other_actor():
    route = "POST:/v2/streams/7/events"
    idem_key = "shared-key"
    actor_3_response = {
        "id": 10,
        "request_id": "actor-3-request",
        "room_id": 7,
        "room_seq": 1,
        "user_id": 3,
        "actor_id": 3,
        "body": "actor 3",
        "event_type": "example.created",
        "category": None,
        "payment_id": None,
        "schema_version": 2,
        "payload": {"message": "actor 3"},
        "metadata": {},
        "created_at": "2026-07-14T00:00:00+00:00",
    }

    class FakeCursor:
        def __init__(self):
            self.executed = []
            self.next_row = None
            self.cache = {
                (route, idem_key): actor_3_response,
            }
            self.inserted_idempotency = None

        def execute(self, sql, params=None):
            self.executed.append((sql, params))
            normalized = " ".join(sql.split())
            self.next_row = None
            if "SELECT response_json FROM idempotency_keys" in normalized:
                cached = self.cache.get(params)
                self.next_row = None if cached is None else {"response_json": cached}
            elif "FROM messages WHERE request_id=%s" in normalized:
                self.next_row = None
            elif "AS stream_exists" in normalized:
                self.next_row = {
                    "stream_exists": True,
                    "actor_exists": True,
                    "member_exists": True,
                }
            elif "INSERT INTO room_sequences" in normalized:
                self.next_row = {"last_seq": 2}
            elif "INSERT INTO messages (" in normalized:
                self.next_row = {
                    "id": 11,
                    "request_id": "actor-4-request",
                    "room_id": 7,
                    "room_seq": 2,
                    "user_id": 4,
                    "event_type": "example.created",
                    "category": None,
                    "payment_id": None,
                    "schema_version": 2,
                    "payload": {"message": "actor 4"},
                    "metadata": {},
                    "body": "actor 4",
                    "created_at": datetime(2026, 7, 14, tzinfo=timezone.utc),
                }
            elif "INSERT INTO idempotency_keys" in normalized:
                self.inserted_idempotency = params

        def fetchone(self):
            return self.next_row

    actor_4_cursor = FakeCursor()
    actor_4_response = worker_main._persist_message_with_cursor(
        {
            "route": route,
            "request_id": "actor-4-request",
            "room_id": 7,
            "user_id": 4,
            "event_type": "example.created",
            "schema_version": 2,
            "payload": {"message": "actor 4"},
            "metadata": {},
            "x_idempotency_key": idem_key,
        },
        actor_4_cursor,
    )

    assert actor_4_response["id"] == 11
    assert "_idempotency_hit" not in actor_4_response
    advisory = actor_4_cursor.executed[0]
    cache_select = actor_4_cursor.executed[1]
    assert json.loads(advisory[1][0]) == [f"{route}|actor:4", idem_key]
    assert "\x00" not in advisory[1][0]
    assert cache_select[1] == (f"{route}|actor:4", idem_key)
    assert actor_4_cursor.executed[2][1] == (route, idem_key)
    assert actor_4_cursor.inserted_idempotency[:2] == (
        f"{route}|actor:4",
        idem_key,
    )

    actor_3_cursor = FakeCursor()
    replayed_actor_3 = worker_main._persist_message_with_cursor(
        {
            "route": route,
            "request_id": "actor-3-duplicate",
            "room_id": 7,
            "user_id": 3,
            "event_type": "example.created",
            "schema_version": 2,
            "payload": {"message": "duplicate"},
            "metadata": {},
            "x_idempotency_key": idem_key,
        },
        actor_3_cursor,
    )
    assert replayed_actor_3["id"] == 10
    assert replayed_actor_3["user_id"] == 3
    assert replayed_actor_3["_idempotency_hit"] is True
    assert json.loads(actor_3_cursor.executed[0][1][0]) == [
        f"{route}|actor:3",
        idem_key,
    ]
    assert actor_3_cursor.executed[1][1] == (f"{route}|actor:3", idem_key)
    assert actor_3_cursor.executed[2][1] == (route, idem_key)
    assert actor_3_cursor.inserted_idempotency[:2] == (
        f"{route}|actor:3",
        idem_key,
    )


@pytest.mark.parametrize(
    "missing_field",
    ("id", "room_id", "room_seq", "user_id", "request_id", "body", "created_at"),
)
def test_idempotency_cache_requires_minimum_response_fields(missing_field):
    response = {
        "id": 10,
        "request_id": "cached-request",
        "room_id": 7,
        "room_seq": 1,
        "user_id": 3,
        "actor_id": 3,
        "body": "cached",
        "event_type": "example.created",
        "schema_version": 2,
        "payload": {"message": "cached"},
        "metadata": {},
        "created_at": "2026-07-14T00:00:00+00:00",
    }
    response.pop(missing_field)

    assert (
        worker_main._validated_idempotency_response(
            {"response_json": response},
            expected_user_id=3,
            expected_room_id=7,
        )
        is None
    )


@pytest.mark.parametrize(
    "malformed_case",
    ("missing_id", "actor_user_conflict", "different_room"),
)
def test_malformed_scoped_idempotency_cache_falls_through_and_is_overwritten(
    malformed_case,
):
    route = "POST:/v2/streams/7/events"
    idem_key = "repair-cache"
    cached_response = {
        "id": 10,
        "request_id": "cached-request",
        "room_id": 7,
        "room_seq": 1,
        "user_id": 3,
        "actor_id": 3,
        "body": "cached",
        "event_type": "example.created",
        "schema_version": 2,
        "payload": {"message": "cached"},
        "metadata": {},
        "created_at": "2026-07-14T00:00:00+00:00",
    }
    if malformed_case == "missing_id":
        cached_response.pop("id")
    elif malformed_case == "actor_user_conflict":
        cached_response["actor_id"] = 4
    else:
        cached_response["room_id"] = 8

    class FakeCursor:
        def __init__(self):
            self.next_row = None
            self.executed = []
            self.cache = {
                (f"{route}|actor:3", idem_key): cached_response,
            }
            self.idempotency_write = None

        def execute(self, sql, params=None):
            self.executed.append((sql, params))
            normalized = " ".join(sql.split())
            self.next_row = None
            if "SELECT response_json FROM idempotency_keys" in normalized:
                cached = self.cache.get(params)
                self.next_row = None if cached is None else {"response_json": cached}
            elif "FROM messages WHERE request_id=%s" in normalized:
                self.next_row = None
            elif "AS stream_exists" in normalized:
                self.next_row = {
                    "stream_exists": True,
                    "actor_exists": True,
                    "member_exists": True,
                }
            elif "INSERT INTO room_sequences" in normalized:
                self.next_row = {"last_seq": 1}
            elif "INSERT INTO messages (" in normalized:
                self.next_row = {
                    "id": 22,
                    "request_id": "new-request",
                    "room_id": 7,
                    "room_seq": 1,
                    "user_id": 3,
                    "actor_id": 3,
                    "event_type": "example.created",
                    "category": None,
                    "payment_id": None,
                    "schema_version": 2,
                    "payload": {"message": "new"},
                    "metadata": {},
                    "body": "new",
                    "created_at": datetime(2026, 7, 14, tzinfo=timezone.utc),
                }
            elif "INSERT INTO idempotency_keys" in normalized:
                self.idempotency_write = (normalized, params)

        def fetchone(self):
            return self.next_row

    cursor = FakeCursor()
    response = worker_main._persist_message_with_cursor(
        {
            "route": route,
            "request_id": "new-request",
            "room_id": 7,
            "user_id": 3,
            "event_type": "example.created",
            "schema_version": 2,
            "payload": {"message": "new"},
            "metadata": {},
            "x_idempotency_key": idem_key,
        },
        cursor,
    )

    assert response["id"] == 22
    assert "_idempotency_hit" not in response
    assert cursor.idempotency_write is not None
    write_sql, write_params = cursor.idempotency_write
    assert "DO UPDATE SET response_json = EXCLUDED.response_json" in write_sql
    assert write_params[:2] == (f"{route}|actor:3", idem_key)
    assert json.loads(write_params[2])["id"] == 22


def test_worker_rejects_different_request_that_reuses_persisted_room_sequence():
    class FakeCursor:
        def __init__(self):
            self.rows = [
                None,
                {
                    "stream_exists": True,
                    "actor_exists": True,
                    "member_exists": True,
                },
                {"last_seq": 5},
                {
                    "id": 9,
                    "request_id": "original-request",
                    "room_id": 7,
                    "user_id": 3,
                    "event_type": None,
                    "category": None,
                    "payment_id": None,
                    "body": "original",
                    "room_seq": 4,
                    "created_at": datetime(2026, 7, 14, tzinfo=timezone.utc),
                },
            ]

        def execute(self, _sql, _params=None):
            pass

        def fetchone(self):
            return self.rows.pop(0)

    with pytest.raises(ValueError, match="Room sequence conflict"):
        worker_main._persist_message_with_cursor(
            {
                "route": "POST:/v1/streams/7/events",
                "request_id": "different-request",
                "room_id": 7,
                "user_id": 3,
                "body": "different",
                "room_seq": 4,
            },
            FakeCursor(),
        )


def test_generic_envelope_and_persisted_at_propagate_to_internal_payloads(monkeypatch):
    response = {
        "id": 10,
        "request_id": "req-generic",
        "room_id": 7,
        "room_seq": 1,
        "user_id": 3,
        "event_type": "deployment.finished",
        "category": None,
        "payment_id": None,
        "schema_version": 2,
        "payload": {"message": "deployment finished", "revision": "abc123"},
        "metadata": {"environment": "staging"},
        "body": "deployment finished",
        "created_at": "2026-07-14T00:00:00+00:00",
        "persisted_at": "2026-07-14T00:00:00.001000+00:00",
    }
    status = worker_main.persisted_status_payload("req-generic", response)
    notification = worker_main.notification_attempt_payload(response)

    assert status["event_type"] == "deployment.finished"
    assert status["schema_version"] == 2
    assert status["payload"] == response["payload"]
    assert status["metadata"] == {"environment": "staging"}
    assert status["persisted_at"] == response["persisted_at"]
    assert notification["event_id"] == 10
    assert notification["stream_id"] == 7
    assert notification["event_type"] == "deployment.finished"
    assert notification["metadata"] == {"environment": "staging"}
    assert notification["payload_preview"] == "deployment finished"


def test_notification_publish_failure_does_not_fail_committed_persistence(monkeypatch, caplog):
    response = {
        "id": 10,
        "request_id": "req-1",
        "room_id": 7,
        "room_seq": 1,
        "user_id": 3,
        "event_type": "legacy.message",
        "schema_version": 1,
        "payload": {"text": "hello"},
        "metadata": {},
        "body": "hello",
        "created_at": "2026-07-14T00:00:00+00:00",
        "persisted_at": "2026-07-14T00:00:00.001000+00:00",
    }

    class FakeConn:
        def __init__(self):
            self.commits = 0

        def commit(self):
            self.commits += 1

    conn = FakeConn()
    upserted_statuses = []

    @contextmanager
    def fake_get_conn():
        yield conn

    @contextmanager
    def fake_get_cursor(_conn):
        yield object()

    monkeypatch.setattr(worker_main, "get_conn", fake_get_conn)
    monkeypatch.setattr(worker_main, "get_cursor", fake_get_cursor)
    monkeypatch.setattr(
        worker_main,
        "_persist_message_with_cursor",
        lambda _payload, _cursor: dict(response),
    )
    timestamps = iter(
        [
            "2026-07-14T00:00:00.001000+00:00",
            "2026-07-14T00:00:00.002000+00:00",
        ]
    )
    monkeypatch.setattr(worker_main, "now_iso", lambda: next(timestamps))
    monkeypatch.setattr(
        worker_main,
        "upsert_request_status",
        lambda _cursor, request_id, payload: upserted_statuses.append(
            (request_id, dict(payload))
        ),
    )
    def fail_after_commit(*_args):
        assert conn.commits == 1
        raise RuntimeError("Kafka notification unavailable")

    monkeypatch.setattr(worker_main, "publish_notification_job", fail_after_commit)

    result = worker_main.persist_ingress_job({"request_id": "req-1"})

    assert result["id"] == response["id"]
    assert result["request_id"] == response["request_id"]
    assert datetime.fromisoformat(result["persisted_at"])
    assert upserted_statuses[0][1]["persisted_at"] == "2026-07-14T00:00:00.001000+00:00"
    assert result["persisted_at"] == "2026-07-14T00:00:00.002000+00:00"
    assert conn.commits == 1
    assert "core persistence remains committed" in caplog.text


def test_notification_attempt_insert_and_migration_enforce_message_id_uniqueness():
    class FakeCursor:
        def __init__(self):
            self.sql = ""
            self.params = None

        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params

        def fetchone(self):
            return {"target_exists": True, "inserted": True}

    cursor = FakeCursor()
    worker_main.insert_notification_attempt(
        cursor,
        {
            "event_id": 10,
            "stream_id": 7,
            "message_id": 10,
            "room_id": 7,
            "body_preview": "hello",
        },
    )
    migration = (
        ROOT / "alembic/versions/0006_notification_attempt_idempotency.py"
    ).read_text(encoding="utf-8")

    assert "WITH target AS" in cursor.sql
    assert "WHERE id=%s AND room_id=%s" in cursor.sql
    assert "FROM target" in cursor.sql
    assert "ON CONFLICT (message_id) DO NOTHING" in cursor.sql
    assert "target_exists" in cursor.sql
    assert "inserted" in cursor.sql
    assert cursor.params[:4] == (10, 7, 10, 7)
    assert json.loads(cursor.params[4])["event_id"] == 10
    assert "ALTER TABLE alembic_version" in migration
    assert "ALTER COLUMN version_num TYPE VARCHAR(64)" in migration
    assert "uq_notification_attempts_message_id" in migration
    assert "DELETE FROM notification_attempts newer" in migration
