"""
tests/test_api_basic.py

포트폴리오 기본 동작 단위 테스트 (pytest 기반)
실제 DB/Redis 없이 순수 로직 및 유틸 함수를 검증합니다.
"""

import pytest
from unittest.mock import MagicMock, patch
import json


# ────────────────────────────────────────────────
# 유틸 함수 단위 테스트
# ────────────────────────────────────────────────

class TestRequestStatusKey:
    """Redis 키 생성 함수 검증"""

    def test_request_status_key_format(self):
        """request_id 기반 Redis 키가 올바른 prefix를 가지는지 확인"""
        from portfolio.api import _request_status_key
        key = _request_status_key("abc-123")
        assert key == "message_request_status:abc-123"

    def test_request_status_key_unique(self):
        """서로 다른 request_id가 서로 다른 키를 생성하는지 확인"""
        from portfolio.api import _request_status_key
        assert _request_status_key("id-1") != _request_status_key("id-2")

    def test_fallback_idem_key_format(self):
        """idempotency 키가 route + idem_key 조합으로 생성되는지 확인"""
        from portfolio.api import _fallback_idem_key
        key = _fallback_idem_key("send_event", "idem-xyz")
        assert "send_event" in key
        assert "idem-xyz" in key


class TestExternalizeRequestStatus:
    """내부 필드명을 외부 API 응답 형식으로 변환하는 함수 검증"""

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


# ────────────────────────────────────────────────
# Worker 유틸 단위 테스트
# ────────────────────────────────────────────────

class TestWorkerUtils:
    """worker/main.py 유틸 함수 검증"""

    def test_request_status_key_format(self):
        from worker.main import request_status_key
        key = request_status_key("req-abc")
        assert key == "message_request_status:req-abc"

    def test_now_iso_returns_string(self):
        from worker.main import now_iso
        ts = now_iso()
        assert isinstance(ts, str)
        assert "T" in ts  # ISO 8601 형식 확인

    def test_room_sequence_gap_error_is_runtime(self):
        from worker.main import RoomSequenceGapError
        assert issubclass(RoomSequenceGapError, RuntimeError)


# ────────────────────────────────────────────────
# Config 단위 테스트
# ────────────────────────────────────────────────

class TestConfig:
    """portfolio/config.py 설정 로드 검증"""

    def test_settings_loads_successfully(self):
        from portfolio.config import settings
        assert settings is not None

    def test_queues_module_has_ingress_queue(self):
        from portfolio import queues
        assert hasattr(queues, "ingress_partition_queue")
