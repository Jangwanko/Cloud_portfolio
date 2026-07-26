"""
Unit tests for small pure helpers.

These tests intentionally avoid live PostgreSQL and Kafka dependencies so they
can run as a fast compile/import sanity check.
"""

from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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


class TestMaterializedCache:
    def test_message_snapshot_cache_returns_db_snapshot_shape(self):
        from portfolio.materialized_cache import cache_message_snapshot, list_cached_events

        cache_message_snapshot(
            {
                "id": 9001,
                "request_id": "req-cache",
                "stream_id": 77,
                "stream_seq": 1,
                "user_id": 3,
                "body": "persisted snapshot",
                "created_at": "2026-04-30T00:00:00+00:00",
            }
        )

        cached, snapshot_age = list_cached_events(77, limit=10)

        assert cached[0]["id"] == 9001
        assert cached[0]["body"] == "persisted snapshot"
        assert snapshot_age is not None

    def test_stream_snapshot_cache_checks_membership(self):
        from portfolio.materialized_cache import cache_stream_snapshot, is_cached_stream_member

        cache_stream_snapshot(
            {"stream_id": 88, "name": "Membership stream", "member_ids": [1, 2, 3]}
        )

        assert is_cached_stream_member(88, 2) is True
        assert is_cached_stream_member(88, 9) is False


class TestWorkerUtils:
    """Small worker helper checks."""

    def test_now_iso_returns_string(self):
        from worker.main import now_iso

        ts = now_iso()
        assert isinstance(ts, str)
        assert "T" in ts

    def test_room_sequence_gap_error_is_runtime(self):
        from worker.main import RoomSequenceGapError

        assert issubclass(RoomSequenceGapError, RuntimeError)


class TestOrderEventClassification:
    """Order-domain event classification for operator queues."""

    def test_known_order_events_map_to_operational_categories(self):
        from portfolio.order_events import classify_order_event

        assert classify_order_event("payment_completed") == "payment"
        assert classify_order_event("delivery_started") == "delivery"
        assert classify_order_event("refund_requested") == "refund"
        assert classify_order_event("support_requested") == "support"

    def test_unknown_order_event_needs_review(self):
        from portfolio.order_events import classify_order_event

        assert classify_order_event("unknown_event") == "needs_review"


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
        assert result["oldest_sample_age_seconds"] == 120
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
        monkeypatch.setattr(
            dlq_replayer,
            "claim_dlq_replay",
            lambda *_args: ("claimed", "test-owner"),
        )
        monkeypatch.setattr(dlq_replayer, "mark_dlq_replay_published", lambda *_args: True)

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
        monkeypatch.setattr(
            dlq_replayer,
            "claim_dlq_replay",
            lambda *_args: ("claimed", "test-owner"),
        )
        monkeypatch.setattr(dlq_replayer, "mark_dlq_replay_published", lambda *_args: True)

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


class TestKafkaIntakeBoundary:
    def test_event_intake_does_not_use_postgres_before_kafka_append(self):
        api = (ROOT / "portfolio/api.py").read_text(encoding="utf-8")
        create_event_body = api.split("def create_event(", 1)[1].split(
            "@router.get(\"/event-requests/{request_id}\"", 1
        )[0]

        before_queue_append = create_event_body.split("_store_request_and_queue_job(", 1)[0]

        assert "_ensure_room_member_for_ingress" not in before_queue_append
        assert "_claim_or_load_request" not in before_queue_append

    def test_reader_membership_checks_use_primary_routing_hint(self):
        api = (ROOT / "portfolio/api.py").read_text(encoding="utf-8")

        assert "/*NO LOAD BALANCE*/\n        SELECT 1" in api
        assert "JOIN room_members rm ON rm.room_id = r.id" in api

    def test_worker_validates_membership_before_persistence(self):
        worker = (ROOT / "worker/main.py").read_text(encoding="utf-8")

        assert "SELECT 1 FROM room_members WHERE room_id=%s AND user_id=%s" in worker
        assert '"membership_missing"' in worker
        assert '"Event authorization rejected"' in worker

    def test_request_status_updates_publish_to_compacted_topic(self):
        worker = (ROOT / "worker/main.py").read_text(encoding="utf-8")

        assert "publish_request_status(request_id, payload)" in worker
        assert '"user_id": job_payload.get("user_id")' in worker

    def test_worker_publishes_message_snapshot_after_persistence(self):
        worker = (ROOT / "worker/main.py").read_text(encoding="utf-8")

        assert "publish_persisted_message_snapshot(response)" in worker
        assert "publish_message_snapshot(response[\"id\"], snapshot)" in worker

    def test_worker_success_path_uses_single_db_transaction_helper(self):
        worker = (ROOT / "worker/main.py").read_text(encoding="utf-8")

        assert "def persist_ingress_job(" in worker
        assert "persist_ingress_job(job_payload)" in worker

    def test_persist_ingress_job_keeps_notification_out_of_core_transaction(self, monkeypatch):
        from worker import main as worker_main

        class CreatedAt:
            def isoformat(self):
                return "2026-06-09T00:00:00+00:00"

        class FakeCursor:
            def __init__(self):
                self.executed = []
                self.rows = [
                    None,
                    {"id": 1},
                    {"id": 2},
                    {"member": 1},
                    {"last_seq": 0},
                    {
                        "id": 10,
                        "request_id": "req-1",
                        "room_id": 7,
                        "user_id": 3,
                        "body": "hello",
                        "room_seq": 1,
                        "created_at": CreatedAt(),
                    },
                    {"user_id": 3},
                ]

            def execute(self, sql, params=None):
                self.executed.append(sql)

            def fetchone(self):
                return self.rows.pop(0)

        class FakeConn:
            closed = False

            def __init__(self):
                self.commits = 0

            def commit(self):
                self.commits += 1

        conn = FakeConn()
        cur = FakeCursor()

        @contextmanager
        def fake_get_conn():
            yield conn

        @contextmanager
        def fake_get_cursor(_conn):
            yield cur

        monkeypatch.setattr(worker_main, "get_conn", fake_get_conn)
        monkeypatch.setattr(worker_main, "get_cursor", fake_get_cursor)
        published_notifications = []

        monkeypatch.setattr(worker_main, "publish_persisted_status", lambda *_args: None)
        monkeypatch.setattr(worker_main, "publish_persisted_message_snapshot", lambda *_args: None)
        monkeypatch.setattr(
            worker_main,
            "publish_notification_job",
            lambda key, payload: published_notifications.append((key, payload)),
        )

        response = worker_main.persist_ingress_job(
            {
                "route": "send_event",
                "request_id": "req-1",
                "room_id": 7,
                "user_id": 3,
                "body": "hello",
            }
        )

        executed_sql = "\n".join(cur.executed)
        assert conn.commits == 1
        assert response["room_seq"] == 1
        assert "INSERT INTO messages" in executed_sql
        assert "INSERT INTO request_statuses" in executed_sql
        assert "INSERT INTO notification_attempts" not in executed_sql
        assert len(published_notifications) == 1
        notification_key, notification = published_notifications[0]
        assert notification_key == 7
        assert notification == {
            "event_id": 10,
            "stream_id": 7,
            "message_id": 10,
            "room_id": 7,
            "event_type": "legacy.message",
            "payload_preview": "hello",
            "metadata": {},
            "body_preview": "hello",
        }

    def test_event_list_response_exposes_cache_metadata(self):
        from portfolio.api import _event_list_response

        response = _event_list_response("cache", True, [{"id": 1}], 1.2345)

        assert response["source"] == "cache"
        assert response["degraded"] is True
        assert response["snapshot_age_seconds"] == 1.234
        assert response["items"] == [{"id": 1}]


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
            "DemoResetRequest",
            "DemoResetResponse",
        ):
            assert model in components

        expected_refs = {
            "/health/ready": "ReadinessResponse",
            "/v1/event-requests/{request_id}": "EventRequestStatusResponse",
            "/v1/dlq/ingress": "DlqListResponse",
            "/v1/dlq/ingress/summary": "DlqSummaryResponse",
            "/v1/admin/demo/reset-events": "DemoResetResponse",
        }
        for path, model in expected_refs.items():
            method = "post" if path == "/v1/admin/demo/reset-events" else "get"
            response_schema = paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
            assert response_schema["$ref"] == f"#/components/schemas/{model}"

        dlq_summary = components["DlqSummaryResponse"]["properties"]
        for field in (
            "total",
            "replayable",
            "blocked",
            "oldest_sample_age_seconds",
            "by_reason",
            "by_stream",
            "recent_samples",
        ):
            assert field in dlq_summary

        demo_reset = components["DemoResetResponse"]["properties"]
        assert "reset_dlq_topic" in demo_reset

        worker_health = components["WorkerHealthResponse"]["properties"]
        assert "max_replicas" in worker_health


class TestOrderEventApiContract:
    """The order reference adapter remains available during the v2 rollout."""

    def test_openapi_contains_order_event_intake_contract(self):
        from portfolio.main import app

        schema = app.openapi()
        components = schema["components"]["schemas"]
        paths = schema["paths"]

        assert "OrderEventCreate" in components
        assert "OrderEventAcceptedResponse" in components
        assert "/v1/orders/{order_id}/events" in paths
        assert paths["/v1/orders/{order_id}/events"]["post"]["deprecated"] is True

        response_schema = paths["/v1/orders/{order_id}/events"]["post"]["responses"]["202"]["content"][
            "application/json"
        ]["schema"]
        assert response_schema["$ref"] == "#/components/schemas/OrderEventAcceptedResponse"
