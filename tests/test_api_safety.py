import asyncio
import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from psycopg2 import InterfaceError, OperationalError
from psycopg2.pool import PoolError
from starlette.requests import Request
from starlette.responses import Response


def _legacy_password_hash(password: str, salt: bytes) -> str:
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"pbkdf2_sha256${encode(salt)}${encode(digest)}"


def _signed_token(auth, payload: dict, *, header_raw: bytes | None = None) -> str:
    header_bytes = header_raw or json.dumps(
        {"alg": "HS256", "typ": "JWT"}, separators=(",", ":")
    ).encode("utf-8")
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header_segment = auth._b64url_encode(header_bytes)
    payload_segment = auth._b64url_encode(payload_bytes)
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = hmac.new(
        auth._SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    return f"{header_segment}.{payload_segment}.{auth._b64url_encode(signature)}"


def test_stream_membership_check_does_not_reveal_stream_existence():
    from portfolio import api

    class Cursor:
        def __init__(self):
            self.sql = ""
            self.params = None

        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params

        def fetchone(self):
            return None

    cursor = Cursor()
    with pytest.raises(HTTPException) as exc_info:
        api._ensure_room_member(cursor, 7, 3)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Stream not found"
    assert "JOIN room_members" in cursor.sql
    assert cursor.params == (7, 3)


def test_read_receipt_lookup_does_not_reveal_event_existence():
    from portfolio import api

    class Cursor:
        def __init__(self):
            self.sql = ""
            self.params = None

        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params

        def fetchone(self):
            return None

    cursor = Cursor()
    with pytest.raises(HTTPException) as exc_info:
        api._message_room_id_for_member(cursor, 11, 3)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Event not found"
    assert "JOIN room_members" in cursor.sql
    assert cursor.params == (11, 3)


def test_password_hash_records_work_factor_and_verifies_legacy_hashes():
    from portfolio.auth import hash_password, verify_password

    current = hash_password("correct horse battery staple")
    assert current.startswith("pbkdf2_sha256$600000$")
    assert verify_password("correct horse battery staple", current)
    assert not verify_password("wrong", current)

    legacy = _legacy_password_hash("legacy-password", b"0123456789abcdef")
    assert verify_password("legacy-password", legacy)
    assert not verify_password("wrong", legacy)


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        "@@@.e30.invalid",
        "e30.e30.",
    ],
)
def test_malformed_access_tokens_are_rejected_as_401(token):
    from portfolio.auth import decode_access_token

    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token)
    assert exc_info.value.status_code == 401


def test_access_token_is_expired_at_exact_expiry_second(monkeypatch):
    from portfolio import auth

    monkeypatch.setattr(auth.time, "time", lambda: 1000)
    monkeypatch.setattr(auth, "_TOKEN_TTL_SECONDS", 0)
    token = auth.create_access_token(1, "user")

    with pytest.raises(HTTPException) as exc_info:
        auth.decode_access_token(token)
    assert exc_info.value.status_code == 401


def test_access_token_rejects_non_ascii_segments_and_deep_json_as_401():
    from portfolio import auth

    valid_header = auth._b64url_encode(b'{"alg":"HS256","typ":"JWT"}')
    valid_length_signature = auth._b64url_encode(b"x" * hashlib.sha256().digest_size)
    malformed_segment = f"{valid_header}.\u00e9.{valid_length_signature}"
    with pytest.raises(HTTPException) as exc_info:
        auth.decode_access_token(malformed_segment)
    assert exc_info.value.status_code == 401

    with pytest.raises(HTTPException) as exc_info:
        auth.decode_access_token("A" * 4097)
    assert exc_info.value.status_code == 401

    deep_header = (
        b'{"alg":"HS256","typ":"JWT","nested":'
        + (b"[" * 1100)
        + b"0"
        + (b"]" * 1100)
        + b"}"
    )
    token = _signed_token(
        auth,
        {"sub": "1", "username": "user", "exp": 4_000_000_000},
        header_raw=deep_header,
    )
    assert len(token) <= 4096
    with pytest.raises(HTTPException) as exc_info:
        auth.decode_access_token(token)
    assert exc_info.value.status_code == 401


@pytest.mark.parametrize(
    "claim,value",
    [
        ("sub", 1),
        ("sub", True),
        ("sub", "01"),
        ("sub", "9223372036854775808"),
        ("exp", True),
        ("exp", 4_000_000_000.5),
        ("exp", "4000000000"),
        ("username", "bad\x00name"),
        ("username", "bad\ud800name"),
    ],
)
def test_access_token_claims_are_strictly_typed_and_unicode_safe(claim, value):
    from portfolio import auth

    payload = {"sub": "1", "username": "user", "exp": 4_000_000_000}
    payload[claim] = value
    token = _signed_token(auth, payload)

    with pytest.raises(HTTPException) as exc_info:
        auth.decode_access_token(token)
    assert exc_info.value.status_code == 401


def test_access_token_accepts_canonical_postgres_bigint_subject():
    from portfolio import auth

    token = _signed_token(
        auth,
        {
            "sub": "9223372036854775807",
            "username": "user",
            "exp": 4_000_000_000,
        },
    )
    payload = auth.decode_access_token(token)

    assert payload["sub"] == "9223372036854775807"


def test_auth_secret_safety_rejects_empty_short_and_default_values(monkeypatch):
    from portfolio import auth

    for unsafe in (
        "",
        "a",
        "dev-secret-change-me",
        "replace-with-a-random-local-secret",
    ):
        monkeypatch.setattr(auth, "_SECRET_KEY", unsafe)
        assert auth.is_unsafe_auth_secret() is True

    monkeypatch.setattr(auth, "_SECRET_KEY", "x" * 32)
    assert auth.is_unsafe_auth_secret() is False


@pytest.mark.parametrize("invalid_text", ["bad\x00text", "bad\ud800text"])
def test_text_input_models_reject_non_scalar_unicode(invalid_text):
    from pydantic import ValidationError

    from portfolio.schemas import (
        DemoResetRequest,
        DlqReplayRequest,
        EventCreate,
        LoginRequest,
        OrderEventCreate,
        StreamCreate,
        UserCreate,
    )

    cases = (
        (UserCreate, {"username": invalid_text, "password": "password-123"}),
        (UserCreate, {"username": "valid-user", "password": invalid_text + "12345678"}),
        (LoginRequest, {"username": invalid_text, "password": "password-123"}),
        (StreamCreate, {"name": invalid_text, "member_ids": []}),
        (EventCreate, {"body": invalid_text}),
        (
            OrderEventCreate,
            {"event_type": "reference.created", "body": invalid_text},
        ),
        (DlqReplayRequest, {"request_id": invalid_text}),
        (DemoResetRequest, {"confirmation": invalid_text}),
    )
    for model, values in cases:
        with pytest.raises(ValidationError):
            model(**values)


def test_request_body_limit_rejects_declared_and_streamed_oversize_bodies():
    from portfolio.main import RequestBodyLimitMiddleware

    async def run_case(messages, *, headers=()):
        downstream_bodies = []
        sent = []
        pending = iter(messages)

        async def downstream(scope, receive, send):
            received = await receive()
            downstream_bodies.append(received.get("body", b""))
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def receive():
            return next(pending)

        async def send(message):
            sent.append(message)

        middleware = RequestBodyLimitMiddleware(downstream, max_body_bytes=5)
        await middleware(
            {"type": "http", "headers": list(headers)},
            receive,
            send,
        )
        return sent, downstream_bodies

    declared, declared_bodies = asyncio.run(
        run_case([], headers=[(b"content-length", b"6")])
    )
    assert declared[0]["status"] == 413
    assert declared_bodies == []

    streamed, streamed_bodies = asyncio.run(
        run_case(
            [
                {"type": "http.request", "body": b"abc", "more_body": True},
                {"type": "http.request", "body": b"def", "more_body": False},
            ]
        )
    )
    assert streamed[0]["status"] == 413
    assert streamed_bodies == []

    accepted, accepted_bodies = asyncio.run(
        run_case([{"type": "http.request", "body": b"abcde", "more_body": False}])
    )
    assert accepted[0]["status"] == 204
    assert accepted_bodies == [b"abcde"]

    with pytest.raises(ValueError, match="positive integer"):
        RequestBodyLimitMiddleware(lambda *_args: None, max_body_bytes=0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"request_body_max_bytes": 0},
        {"request_body_max_bytes": 16_777_217},
        {"db_pool_minconn": 5, "db_pool_maxconn": 4},
        {"dlq_replay_max_count": 0},
        {"dlq_replay_max_count": 9_223_372_036_854_775_808},
        {"kafka_topic_replication_factor": 1, "kafka_min_insync_replicas": 2},
    ],
)
def test_runtime_settings_reject_unsafe_numeric_bounds(overrides):
    from portfolio.config import Settings

    with pytest.raises(ValueError):
        Settings(**overrides)


def test_business_api_is_blocked_until_schema_startup_completes(monkeypatch):
    from portfolio import main

    monkeypatch.setattr(main, "_db_startup_ready", False)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v2/streams/1/events",
            "raw_path": b"/v2/streams/1/events",
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "root_path": "",
        }
    )

    async def must_not_run(_request):
        pytest.fail("business route must not run before schema startup")

    response = asyncio.run(main.collect_http_metrics(request, must_not_run))
    assert response.status_code == 503


def test_database_startup_retry_uses_capped_backoff_and_throttled_warnings(
    monkeypatch, caplog
):
    from portfolio import main

    class FakeStop:
        def __init__(self):
            self.waits = []

        def wait(self, delay):
            self.waits.append(delay)
            return False

    stop = FakeStop()
    attempts = 0

    def initialize():
        nonlocal attempts
        attempts += 1
        if attempts <= 6:
            raise RuntimeError("database unavailable")
        main._db_startup_ready = True

    monotonic_values = iter(range(6))
    monkeypatch.setattr(main, "_db_startup_ready", False)
    monkeypatch.setattr(main, "_db_startup_stop", stop)
    monkeypatch.setattr(main, "_initialize_db_startup", initialize)
    monkeypatch.setattr(main, "settings", SimpleNamespace(startup_retry_delay=2.0))
    monkeypatch.setattr(main.time, "monotonic", lambda: next(monotonic_values))
    caplog.set_level("WARNING")

    main._retry_db_startup_until_ready()

    assert stop.waits == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]
    warnings = [
        record
        for record in caplog.records
        if "PostgreSQL startup retry still failing" in record.message
    ]
    assert len(warnings) == 1


def test_nonlocal_business_api_is_blocked_for_unsafe_auth_secret(monkeypatch):
    from portfolio import main

    monkeypatch.setattr(main, "_db_startup_ready", True)
    monkeypatch.setattr(main, "settings", SimpleNamespace(app_env="production"))
    monkeypatch.setattr(main, "is_unsafe_auth_secret", lambda: True)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/dlq/ingress",
            "raw_path": b"/v1/dlq/ingress",
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "root_path": "",
        }
    )

    async def must_not_run(_request):
        pytest.fail("unsafe non-local auth configuration must block business routes")

    response = asyncio.run(main.collect_http_metrics(request, must_not_run))
    assert response.status_code == 503


def test_http_metrics_use_route_template_instead_of_raw_identifier(monkeypatch):
    from portfolio import main
    from portfolio.metrics import api_requests_total

    monkeypatch.setattr(main, "_db_startup_ready", True)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/event-requests/550e8400-e29b-41d4-a716-446655440000",
            "raw_path": b"/v1/event-requests/550e8400-e29b-41d4-a716-446655440000",
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "root_path": "",
        }
    )

    async def call_next(incoming_request):
        incoming_request.scope["route"] = SimpleNamespace(
            path="/v1/event-requests/{request_id}"
        )
        return Response(status_code=200)

    asyncio.run(main.collect_http_metrics(request, call_next))
    samples = [sample for metric in api_requests_total.collect() for sample in metric.samples]
    assert any(
        sample.labels.get("path") == "/v1/event-requests/{request_id}"
        for sample in samples
    )
    assert all("550e8400" not in sample.labels.get("path", "") for sample in samples)


def test_readiness_rejects_traffic_until_schema_startup_completes(monkeypatch):
    from portfolio import main

    monkeypatch.setattr(main, "_db_startup_ready", False)
    monkeypatch.setattr(main, "ping_kafka", lambda: True)
    monkeypatch.setattr(
        main,
        "get_postgres_runtime_status",
        lambda: {
            "ha_mode": True,
            "primary_reachable": True,
            "write_available": True,
            "standby_count": 2,
            "sync_standby_count": 1,
            "max_replication_delay_bytes": 0,
        },
    )
    status_code, payload = main._build_readiness_payload()
    assert status_code == 503
    assert payload["status"] == "not_ready"
    assert "schema_not_ready" in payload["reason"]


def test_worker_operations_summary_caches_prometheus_result(monkeypatch):
    from portfolio import main

    calls = []
    clock = {"now": 100.0}

    def fetch():
        calls.append(clock["now"])
        return {
            "deployment": "worker",
            "desired_replicas": 2,
            "available_replicas": 2,
            "hpa_desired_replicas": 2,
            "max_replicas": 8,
            "source": "prometheus",
            "error": None,
        }

    monkeypatch.setattr(main, "_worker_status_cached", None)
    monkeypatch.setattr(main, "_worker_status_cached_until", 0.0)
    monkeypatch.setattr(main, "_fetch_worker_runtime_status", fetch)
    monkeypatch.setattr(main.time, "monotonic", lambda: clock["now"])

    first = main._worker_runtime_status()
    first["desired_replicas"] = 99
    second = main._worker_runtime_status()
    assert second["desired_replicas"] == 2
    assert calls == [100.0]

    clock["now"] = 116.0
    main._worker_runtime_status()
    assert calls == [100.0, 116.0]


def test_worker_operations_summary_reports_prometheus_failure(monkeypatch):
    from portfolio import main

    monkeypatch.setattr(main, "_worker_status_cached", None)
    monkeypatch.setattr(main, "_worker_status_cached_until", 0.0)
    monkeypatch.setattr(
        main,
        "_fetch_worker_runtime_status",
        lambda: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )

    status = main._worker_runtime_status()

    assert status["source"] == "unavailable"
    assert status["error"] == "RuntimeError"
    assert status["desired_replicas"] is None


def test_application_maps_uncaught_database_dependency_errors_to_503():
    from portfolio.main import app, database_unavailable_handler

    for error_type in (OperationalError, InterfaceError, PoolError):
        assert app.exception_handlers[error_type] is database_unavailable_handler


def test_create_user_maps_pool_checkout_failure_to_503(monkeypatch):
    from contextlib import contextmanager

    from portfolio import api
    from portfolio.schemas import UserCreate

    @contextmanager
    def unavailable_connection():
        raise OperationalError("database unavailable")
        yield

    monkeypatch.setattr(api, "get_conn", unavailable_connection)

    with pytest.raises(HTTPException) as exc_info:
        api.create_user(UserCreate(username="tester", password="Password123!"))
    assert exc_info.value.status_code == 503


















def test_request_status_healthy_db_miss_returns_404(monkeypatch):
    from portfolio import api

    monkeypatch.setattr(api, "_load_request_status", lambda _request_id: None)

    with pytest.raises(HTTPException) as exc_info:
        api.get_event_request_status("req-deleted", current_user={"id": 7})
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Request not found"


def test_request_status_db_exception_returns_503(monkeypatch):
    from portfolio import api

    def db_failure(_request_id):
        raise OperationalError("database unavailable")

    monkeypatch.setattr(api, "_load_request_status", db_failure)

    with pytest.raises(HTTPException) as exc_info:
        api.get_event_request_status("req-fallback", current_user={"id": 7})
    assert exc_info.value.status_code == 503










def test_dlq_api_safely_represents_malformed_numeric_fields(monkeypatch):
    from portfolio import api

    malformed = {
        "topic": "message-ingress-dlq",
        "partition": "bad",
        "offset": [],
        "timestamp": "not-a-time",
        "key": "bad-record",
        "value": {
            "request_id": "req-bad",
            "room_id": "not-a-stream",
            "user_id": "7",
            "retry_count": [],
            "replay_count": "NaN",
        },
    }
    monkeypatch.setattr(api, "list_recent_topic_messages", lambda *_args: [malformed])

    listing = api.get_ingress_dlq(limit=20, current_user={"id": 7})
    summary = api.get_ingress_dlq_summary(
        limit=200,
        sample_limit=5,
        current_user={"id": 7},
    )

    # A malformed owner identifier must not be coerced into the authenticated
    # user's id; otherwise another tenant's DLQ record could be disclosed.
    assert listing["count"] == 0
    assert summary["total"] == 0
    assert summary["by_stream"] == []

    shaped = api._summarize_dlq_item(malformed)
    assert shaped["stream_id"] is None
    assert shaped["user_id"] is None
    assert shaped["retry_count"] == 0
    assert shaped["replay_count"] == 0
    assert shaped["replayable"] is False
    assert shaped["failed_reason"] == "invalid_dlq_payload"


@pytest.mark.parametrize(
    "field,value",
    [
        ("room_id", 7.9),
        ("room_id", "7"),
        ("room_id", True),
        ("user_id", 3.9),
        ("user_id", "3"),
        ("retry_count", -1),
        ("retry_count", 1.5),
        ("replay_count", -1),
        ("replay_count", 0.9),
        ("replay_count", True),
        ("replay_count", 9_223_372_036_854_775_808),
    ],
)
def test_manual_dlq_summary_never_coerces_replay_identity_or_counters(field, value):
    from portfolio import api

    payload = {
        "request_id": "req-strict",
        "room_id": 7,
        "user_id": 3,
        "retry_count": 0,
        "replay_count": 0,
    }
    payload[field] = value

    shaped = api._summarize_dlq_item({"value": payload})

    assert shaped["replayable"] is False
    assert shaped["failed_reason"] == "invalid_dlq_payload"


def test_manual_dlq_replay_rechecks_counter_before_claim_and_publish(monkeypatch):
    from portfolio import api

    monkeypatch.setattr(
        api,
        "claim_dlq_replay",
        lambda *_args: pytest.fail("invalid replay counter must not be claimed"),
    )
    monkeypatch.setattr(
        api,
        "publish_ingress_job",
        lambda *_args: pytest.fail("invalid replay counter must not be published"),
    )
    item = {
        "request_id": "req-strict",
        "replayable": True,
        "replay_count": 0.9,
        "payload": {
            "request_id": "req-strict",
            "room_id": 7,
            "user_id": 3,
            "replay_count": 0.9,
        },
    }

    with pytest.raises(HTTPException) as exc_info:
        api._claim_manual_replay(item)
    assert exc_info.value.status_code == 409

    with pytest.raises(HTTPException) as exc_info:
        api._replay_dlq_payload(item)
    assert exc_info.value.status_code == 409


def test_dlq_replay_claim_identity_rejects_invalid_unicode_and_numbers():
    from portfolio.state_store import _dlq_replay_claim_key, _dlq_replay_claim_value

    for invalid_request_id in ("", "x" * 81, "bad\x00id", "bad\ud800id"):
        with pytest.raises(ValueError):
            _dlq_replay_claim_key(invalid_request_id, 0)

    for invalid_generation in (True, 0.5, -1, 9_223_372_036_854_775_808):
        with pytest.raises(ValueError):
            _dlq_replay_claim_key("req-1", invalid_generation)

    for invalid_owner in ("", "x" * 81, "bad\x00owner", "bad\ud800owner"):
        with pytest.raises(ValueError):
            _dlq_replay_claim_value("req-1", invalid_owner)


def test_request_status_programming_error_is_not_hidden(monkeypatch):
    from portfolio import api

    def corrupt_status(_request_id):
        raise ValueError("corrupt status JSON")

    monkeypatch.setattr(api, "_load_request_status", corrupt_status)
    with pytest.raises(ValueError, match="corrupt status JSON"):
        api.get_event_request_status("req-corrupt", current_user={"id": 7})




def test_uninitialized_pool_checkout_uses_database_availability_exception(monkeypatch):
    from psycopg2.pool import PoolError

    from portfolio import db

    monkeypatch.setattr(db, "_pool", None)
    with pytest.raises(PoolError, match="not initialized"):
        db._checkout_connection()


def test_checked_out_connection_returns_to_originating_pool_after_reconnect(monkeypatch):
    from portfolio import db

    class FakeConnection:
        closed = False

        def __init__(self):
            self.rollbacks = 0

        def rollback(self):
            self.rollbacks += 1

    class FakePool:
        def __init__(self):
            self.connection = FakeConnection()
            self.returned = []
            self.closed = 0

        def getconn(self):
            return self.connection

        def putconn(self, conn, close=False):
            self.returned.append((conn, close))

        def closeall(self):
            self.closed += 1

    old_pool = FakePool()
    new_pool = FakePool()
    monkeypatch.setattr(db, "_pool", old_pool)
    monkeypatch.setattr(db, "_pool_active", {})
    monkeypatch.setattr(db, "_retired_pools", {})
    monkeypatch.setattr(db, "_create_pool", lambda: new_pool)

    with db.get_conn() as connection:
        assert connection is old_pool.connection
        db.reconnect_pool()
        assert db._pool is new_pool
        assert old_pool.closed == 0

    assert old_pool.returned == [(old_pool.connection, False)]
    assert old_pool.connection.rollbacks == 1
    assert old_pool.closed == 1


def test_alembic_migration_url_escapes_special_credentials(monkeypatch):
    from portfolio import db

    monkeypatch.setattr(
        db,
        "settings",
        SimpleNamespace(
            db_user="portfolio@example.com",
            db_password="p%ss:/# word",
            db_host="postgres.internal",
            db_port=5432,
            db_name="portfolio",
        ),
    )
    captured = {}

    def fake_upgrade(config, revision):
        captured["url"] = config.get_main_option("sqlalchemy.url")
        captured["script_location"] = config.get_main_option("script_location")
        captured["revision"] = revision

    monkeypatch.setattr(db.command, "upgrade", fake_upgrade)

    db.run_alembic_migrations()

    assert captured["revision"] == "head"
    assert captured["url"] == (
        "postgresql+psycopg2://portfolio%40example.com:"
        "p%25ss%3A%2F%23%20word@postgres.internal:5432/portfolio"
    )
    assert captured["script_location"].endswith("alembic")


def test_demo_reset_locks_writers_and_does_not_reuse_stream_ids():
    from portfolio.api import _reset_demo_event_data

    class FakeCursor:
        def __init__(self):
            self.executed = []

        def execute(self, sql, _params=None):
            self.executed.append(" ".join(sql.split()))

        def fetchone(self):
            return {
                "deleted_messages": 1,
                "reset_streams": 1,
                "reset_request_statuses": 1,
            }

    cursor = FakeCursor()
    result = _reset_demo_event_data(cursor)
    sql = "\n".join(cursor.executed)

    assert cursor.executed[0].startswith("LOCK TABLE idempotency_keys, messages, rooms")
    assert "TRUNCATE TABLE rooms CASCADE" in sql
    assert "TRUNCATE TABLE rooms RESTART IDENTITY" not in sql
    assert result == {
        "deleted_messages": 1,
        "reset_streams": 1,
        "reset_request_statuses": 1,
    }


def test_event_intake_openapi_contract_is_202():
    from portfolio.main import app

    schema = app.openapi()
    for path in (
        "/v1/streams/{stream_id}/events",
        "/v1/orders/{order_id}/events",
        "/v2/streams/{stream_id}/events",
    ):
        assert "202" in schema["paths"][path]["post"]["responses"]
        assert "200" not in schema["paths"][path]["post"]["responses"]
