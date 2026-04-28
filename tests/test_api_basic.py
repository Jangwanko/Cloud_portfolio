"""
Unit tests for small pure helpers.

These tests intentionally avoid live PostgreSQL and Kafka dependencies so they
can run as a fast compile/import sanity check.
"""


class TestRequestStatusKey:
    """Request key generation helpers."""

    def test_request_status_key_format(self):
        from portfolio.api import _request_status_key

        key = _request_status_key("abc-123")
        assert key == "message_request_status:abc-123"

    def test_request_status_key_unique(self):
        from portfolio.api import _request_status_key

        assert _request_status_key("id-1") != _request_status_key("id-2")

    def test_fallback_idem_key_format(self):
        from portfolio.api import _fallback_idem_key

        key = _fallback_idem_key("send_event", "idem-xyz")
        assert "send_event" in key
        assert "idem-xyz" in key


class TestExternalizeRequestStatus:
    """Internal status fields are renamed for the external API response."""

    def test_message_id_renamed_to_event_id(self):
        from portfolio.api import _externalize_request_status

        result = _externalize_request_status({"message_id": "m-001", "status": "accepted"})
        assert "event_id" in result
        assert "message_id" not in result
        assert result["event_id"] == "m-001"

    def test_room_id_renamed_to_stream_id(self):
        from portfolio.api import _externalize_request_status

        result = _externalize_request_status({"room_id": "r-001"})
        assert "stream_id" in result
        assert "room_id" not in result

    def test_room_seq_renamed_to_stream_seq(self):
        from portfolio.api import _externalize_request_status

        result = _externalize_request_status({"room_seq": 5})
        assert "stream_seq" in result
        assert result["stream_seq"] == 5

    def test_no_rename_when_fields_absent(self):
        from portfolio.api import _externalize_request_status

        original = {"status": "persisted", "request_id": "req-1"}
        result = _externalize_request_status(original)
        assert result["status"] == "persisted"
        assert result["request_id"] == "req-1"


class TestWorkerUtils:
    """Small worker helper checks."""

    def test_request_status_key_format(self):
        from worker.main import request_status_key

        key = request_status_key("req-abc")
        assert key == "message_request_status:req-abc"

    def test_now_iso_returns_string(self):
        from worker.main import now_iso

        ts = now_iso()
        assert isinstance(ts, str)
        assert "T" in ts

    def test_room_sequence_gap_error_is_runtime(self):
        from worker.main import RoomSequenceGapError

        assert issubclass(RoomSequenceGapError, RuntimeError)


class TestDlqHelpers:
    """DLQ API payload shaping and replay guard checks."""

    def test_summarize_dlq_item_marks_replayable(self):
        from portfolio.api import _summarize_dlq_item

        item = {
            "topic": "message-ingress-dlq",
            "partition": 2,
            "offset": 10,
            "timestamp": 12345,
            "key": "7",
            "value": {
                "request_id": "req-1",
                "room_id": 7,
                "user_id": 3,
                "failed_reason": "transient_error",
                "retry_count": 3,
                "replay_count": 1,
            },
        }

        result = _summarize_dlq_item(item)

        assert result["request_id"] == "req-1"
        assert result["stream_id"] == 7
        assert result["failed_reason"] == "transient_error"
        assert result["replayable"] is True
        assert result["payload"] == item["value"]

    def test_summarize_dlq_item_marks_max_replay_exceeded(self):
        from portfolio.api import _summarize_dlq_item
        from portfolio.config import settings

        result = _summarize_dlq_item({"value": {"replay_count": settings.dlq_replay_max_count}})

        assert result["replayable"] is False
        assert result["max_replay_count"] == settings.dlq_replay_max_count

    def test_summarize_dlq_items_groups_operational_fields(self):
        from datetime import datetime, timezone

        from portfolio.api import _summarize_dlq_items

        now = datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc)
        items = [
            {
                "request_id": "req-1",
                "stream_id": 10,
                "failed_reason": "room_sequence_gap",
                "replayable": True,
                "failed_at": "2026-04-29T11:59:30+00:00",
            },
            {
                "request_id": "req-2",
                "stream_id": 10,
                "failed_reason": "room_sequence_gap",
                "replayable": False,
                "timestamp": 1777463940000,
            },
            {
                "request_id": "req-3",
                "stream_id": 11,
                "failed_reason": "transient_error_max_retries:OperationalError",
                "replayable": True,
                "failed_at": "2026-04-29T11:58:00Z",
            },
        ]

        result = _summarize_dlq_items(items, now=now, sample_limit=2)

        assert result["total"] == 3
        assert result["replayable"] == 2
        assert result["blocked"] == 1
        assert result["oldest_age_seconds"] == 120
        assert result["by_reason"] == {
            "room_sequence_gap": 2,
            "transient_error_max_retries:OperationalError": 1,
        }
        assert result["by_stream"] == [
            {"stream_id": 10, "count": 2},
            {"stream_id": 11, "count": 1},
        ]
        assert [item["request_id"] for item in result["recent_samples"]] == ["req-1", "req-2"]

    def test_replay_one_skips_when_max_replay_count_reached(self, monkeypatch):
        from portfolio.config import settings
        from worker import dlq_replayer

        published = []
        monkeypatch.setattr(dlq_replayer, "publish_ingress_job", lambda *args: published.append(args))

        moved = dlq_replayer.replay_one(
            {
                "request_id": "req-max",
                "room_id": 1,
                "replay_count": settings.dlq_replay_max_count,
            }
        )

        assert moved is False
        assert published == []

    def test_replay_one_records_replay_result(self, monkeypatch):
        from worker import dlq_replayer

        published = []
        monkeypatch.setattr(dlq_replayer, "publish_ingress_job", lambda *args: published.append(args))

        moved = dlq_replayer.replay_one(
            {
                "request_id": "req-replay",
                "room_id": 1,
                "replay_count": 0,
            }
        )

        assert moved is True
        assert len(published) == 1

    def test_dlq_metrics_are_defined(self):
        from portfolio.metrics import dlq_events_total, dlq_replay_total

        assert dlq_events_total is not None
        assert dlq_replay_total is not None


class TestSecurityHelpers:
    """Security defaults stay visible to tests and documentation."""

    def test_default_auth_secret_is_detectable(self):
        from portfolio.auth import is_default_auth_secret

        assert isinstance(is_default_auth_secret(), bool)


class TestConfig:
    """Basic settings/module import checks."""

    def test_settings_loads_successfully(self):
        from portfolio.config import settings

        assert settings is not None

    def test_kafka_settings_exist(self):
        from portfolio.config import settings

        assert settings.kafka_ingress_topic
        assert settings.kafka_dlq_topic

    def test_dlq_replay_limit_exists(self):
        from portfolio.config import settings

        assert settings.dlq_replay_max_count >= 1

    def test_dlq_replayer_metrics_port_exists(self):
        from portfolio.config import settings

        assert settings.dlq_replayer_metrics_port == 9102


class TestOpenApiContract:
    """FastAPI OpenAPI schema exposes the public API contract."""

    def test_openapi_contains_operational_response_models(self):
        from portfolio.main import app

        schema = app.openapi()
        components = schema["components"]["schemas"]
        paths = schema["paths"]

        for model in (
            "ReadinessResponse",
            "EventRequestStatusResponse",
            "DlqListResponse",
            "DlqSummaryResponse",
        ):
            assert model in components

        expected_refs = {
            "/health/ready": "ReadinessResponse",
            "/v1/event-requests/{request_id}": "EventRequestStatusResponse",
            "/v1/dlq/ingress": "DlqListResponse",
            "/v1/dlq/ingress/summary": "DlqSummaryResponse",
        }
        for path, model in expected_refs.items():
            response_schema = paths[path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
            assert response_schema["$ref"] == f"#/components/schemas/{model}"

        dlq_summary = components["DlqSummaryResponse"]["properties"]
        for field in (
            "total",
            "replayable",
            "blocked",
            "oldest_age_seconds",
            "by_reason",
            "by_stream",
            "recent_samples",
        ):
            assert field in dlq_summary
