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


class TestConfig:
    """Basic settings/module import checks."""

    def test_settings_loads_successfully(self):
        from portfolio.config import settings

        assert settings is not None

    def test_kafka_settings_exist(self):
        from portfolio.config import settings

        assert settings.kafka_ingress_topic
        assert settings.kafka_dlq_topic
