from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]


def nested_json_object(container_depth: int, leaf):
    value = leaf
    for _ in range(container_depth):
        value = {"nested": value}
    return value


def test_v2_generic_event_queues_canonical_envelope_and_rollout_aliases(monkeypatch):
    from portfolio import api
    from portfolio.schemas import GenericEventCreate

    queued = []
    monkeypatch.setattr(
        api,
        "_store_request_and_queue_job",
        lambda request_id, response, job: queued.append((request_id, response, job)),
    )

    response = api.create_generic_event(
        stream_id=42,
        payload=GenericEventCreate(
            event_type="sensor.threshold.exceeded",
            payload={"message": "temperature high", "value": 88},
            metadata={"site": "seoul", "trace_id": "trace-1"},
        ),
        x_idempotency_key="generic-idem-1",
        current_user={"id": 7},
    )

    assert response == queued[0][1]
    assert response["status"] == "accepted"
    assert response["persistence"] == "queued"
    assert response["stream_id"] == 42
    assert response["actor_id"] == 7
    assert response["schema_version"] == 2
    assert response["event_type"] == "sensor.threshold.exceeded"
    assert response["payload"] == {"message": "temperature high", "value": 88}
    assert response["metadata"] == {"site": "seoul", "trace_id": "trace-1"}

    request_id, _accepted, job = queued[0]
    assert job["request_id"] == request_id
    assert job["route"] == "POST:/v2/streams/42/events"
    assert job["stream_id"] == 42
    assert job["actor_id"] == 7
    assert job["room_id"] == 42
    assert job["user_id"] == 7
    assert job["body"] == "temperature high"
    assert job["x_idempotency_key"] == "generic-idem-1"


def test_v1_stream_event_remains_body_only_compatible(monkeypatch):
    from portfolio import api
    from portfolio.schemas import EventCreate

    queued = []
    monkeypatch.setattr(api, "_store_request_and_queue_job", lambda *args: queued.append(args))

    response = api.create_event(
        stream_id=42,
        payload=EventCreate(body="legacy body"),
        x_idempotency_key="legacy-idem-1",
        current_user={"id": 7},
    )

    assert response["stream_id"] == 42
    assert response["user_id"] == 7
    assert response["body"] == "legacy body"
    assert "event_type" not in response
    job = queued[0][2]
    assert job["route"] == "POST:/v1/streams/42/events"
    assert job["body"] == "legacy body"
    assert "payload" not in job
    assert "metadata" not in job


def test_v1_order_adapter_maps_reference_fields_into_generic_metadata(monkeypatch):
    from portfolio import api
    from portfolio.schemas import OrderEventCreate

    queued = []
    monkeypatch.setattr(api, "_store_request_and_queue_job", lambda *args: queued.append(args))

    response = api.create_order_event(
        order_id=42,
        payload=OrderEventCreate(
            event_type="payment_completed",
            body="payment accepted",
            payment_id="pay-42",
        ),
        x_idempotency_key=None,
        current_user={"id": 7},
    )

    assert response["order_id"] == 42
    assert response["stream_id"] == 42
    assert response["category"] == "payment"
    job = queued[0][2]
    assert job["schema_version"] == 1
    assert job["payload"] == {"text": "payment accepted"}
    assert job["metadata"] == {
        "reference_scenario": "order-lifecycle",
        "classification": "payment",
        "external_references": {"payment": "pay-42"},
    }
    assert job["category"] == "payment"
    assert job["payment_id"] == "pay-42"


@pytest.mark.parametrize(
    ("schema_name", "payload"),
    [
        ("event", {"body": "before\x00after"}),
        (
            "order",
            {
                "event_type": "payment\x00completed",
                "body": "paid",
                "payment_id": "pay-1",
            },
        ),
        (
            "order",
            {
                "event_type": "payment_completed",
                "body": "before\x00after",
                "payment_id": "pay-1",
            },
        ),
        (
            "order",
            {
                "event_type": "payment_completed",
                "body": "paid",
                "payment_id": "pay\x00-1",
            },
        ),
    ],
)
def test_legacy_event_schemas_reject_nul_strings(schema_name, payload):
    from portfolio.schemas import EventCreate, OrderEventCreate

    schema = EventCreate if schema_name == "event" else OrderEventCreate
    with pytest.raises(ValidationError, match="value_error"):
        schema(**payload)


def test_generic_event_schema_rejects_invalid_type_and_oversized_json():
    from portfolio.schemas import GenericEventCreate

    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        GenericEventCreate(event_type="contains spaces", payload={})

    with pytest.raises(ValidationError, match="payload must not exceed 65536"):
        GenericEventCreate(
            event_type="large.payload",
            payload={"value": "가" * 22_000},
        )

    with pytest.raises(ValidationError, match="metadata must not exceed 16384"):
        GenericEventCreate(
            event_type="large.metadata",
            payload={},
            metadata={"value": "가" * 5_500},
        )

    with pytest.raises(ValidationError, match="JSON numbers must be finite"):
        GenericEventCreate(
            event_type="invalid.number",
            payload={"value": float("nan")},
        )

    with pytest.raises(ValidationError, match="must not contain NUL"):
        GenericEventCreate(
            event_type="nested.nul",
            payload={"items": [{"message": "before\x00after"}]},
        )

    with pytest.raises(ValidationError, match="must not contain NUL"):
        GenericEventCreate(
            event_type="metadata.nul",
            payload={},
            metadata={"nested": {"bad\x00key": "value"}},
        )


def test_api_generic_envelope_accepts_depth_64_and_rejects_depth_65_without_recursion_error():
    from portfolio.schemas import GenericEventCreate

    accepted = GenericEventCreate(
        event_type="depth.boundary",
        payload=nested_json_object(64, "leaf"),
    )
    assert accepted.payload == nested_json_object(64, "leaf")

    with pytest.raises(ValidationError, match="must not exceed 64 container levels"):
        GenericEventCreate(
            event_type="depth.exceeded",
            payload=nested_json_object(65, "leaf"),
        )


def test_api_generic_envelope_iteratively_rejects_deep_nul_and_circular_objects():
    from portfolio.schemas import GenericEventCreate

    with pytest.raises(ValidationError, match="must not contain NUL"):
        GenericEventCreate(
            event_type="deep.nul",
            payload=nested_json_object(64, "before\x00after"),
        )

    circular = {}
    circular["self"] = circular
    with pytest.raises(ValidationError):
        GenericEventCreate(
            event_type="circular.object",
            payload=circular,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"value": "\ud800"},
        {"\udfff": "value"},
    ],
)
def test_api_generic_envelope_rejects_lone_utf16_surrogates(payload):
    from portfolio.schemas import GenericEventCreate

    with pytest.raises(ValidationError, match="valid Unicode scalars"):
        GenericEventCreate(
            event_type="unicode.invalid",
            payload=payload,
        )


def test_api_generic_envelope_accepts_unicode_scalar_emoji():
    from portfolio.schemas import GenericEventCreate

    event = GenericEventCreate(
        event_type="unicode.valid",
        payload={"value": "😀"},
        metadata={"label": "emoji 😀"},
    )

    assert event.payload == {"value": "😀"}
    assert event.metadata == {"label": "emoji 😀"}


@pytest.mark.parametrize("event_type", ["", None, False])
def test_v2_schema_rejects_explicit_empty_null_or_bool_event_type(event_type):
    from portfolio.schemas import GenericEventCreate

    with pytest.raises(ValidationError):
        GenericEventCreate(event_type=event_type, payload={}, metadata={})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("payload", None),
        ("metadata", None),
    ],
)
def test_v2_schema_rejects_null_payload_or_metadata(field, value):
    from portfolio.schemas import GenericEventCreate

    request = {
        "event_type": "example.created",
        "payload": {},
        "metadata": {},
    }
    request[field] = value
    with pytest.raises(ValidationError):
        GenericEventCreate(**request)


def test_openapi_exposes_v2_generic_contract_and_deprecates_order_adapter():
    from portfolio.main import app

    schema = app.openapi()
    paths = schema["paths"]
    components = schema["components"]["schemas"]

    assert "GenericEventCreate" in components
    assert "GenericEventAcceptedResponse" in components
    assert "GenericEventResponse" in components
    assert "GenericEventListResponse" in components
    assert "GenericEventRequestStatusResponse" in components
    generic_post = paths["/v2/streams/{stream_id}/events"]["post"]
    assert generic_post["requestBody"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/GenericEventCreate"
    )
    assert generic_post["responses"]["202"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/GenericEventAcceptedResponse"
    )
    assert "200" not in generic_post["responses"]

    generic_get = paths["/v2/streams/{stream_id}/events"]["get"]
    assert generic_get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/GenericEventListResponse"
    )
    generic_status = paths["/v2/event-requests/{request_id}"]["get"]
    assert generic_status["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ] == "#/components/schemas/GenericEventRequestStatusResponse"

    generic_event_fields = components["GenericEventResponse"]["properties"]
    generic_status_fields = components["GenericEventRequestStatusResponse"]["properties"]
    for field in ("actor_id", "event_type", "schema_version", "payload", "metadata"):
        assert field in generic_event_fields
        assert field in generic_status_fields
    for legacy_field in ("user_id", "body", "category", "payment_id"):
        assert legacy_field not in generic_event_fields
        assert legacy_field not in generic_status_fields

    legacy_post = paths["/v1/streams/{stream_id}/events"]["post"]
    assert legacy_post["requestBody"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/EventCreate"
    )
    assert legacy_post["responses"]["202"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/EventAcceptedResponse"
    )
    legacy_get = paths["/v1/streams/{stream_id}/events"]["get"]
    assert legacy_get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/EventListResponse"
    )
    legacy_status = paths["/v1/event-requests/{request_id}"]["get"]
    assert legacy_status["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ] == "#/components/schemas/EventRequestStatusResponse"

    order_post = paths["/v1/orders/{order_id}/events"]["post"]
    assert order_post["deprecated"] is True
    assert order_post["responses"]["202"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/OrderEventAcceptedResponse"
    )


def test_http_middleware_maps_json_recursion_to_422_and_records_metric(monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace

    from starlette.requests import Request

    from portfolio import main

    monkeypatch.setattr(main, "_db_startup_ready", True)

    class MetricCapture:
        def __init__(self):
            self.labels_seen = []
            self.increment_count = 0
            self.observations = []

        def labels(self, **labels):
            self.labels_seen.append(labels)
            return self

        def inc(self):
            self.increment_count += 1

        def observe(self, value):
            self.observations.append(value)

    requests_metric = MetricCapture()
    latency_metric = MetricCapture()
    monkeypatch.setattr(main, "api_requests_total", requests_metric)
    monkeypatch.setattr(main, "api_request_latency_seconds", latency_metric)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v2/streams/7/events",
            "raw_path": b"/v2/streams/7/events",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json; charset=utf-8")],
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "root_path": "",
            "route": SimpleNamespace(path="/v2/streams/{stream_id}/events"),
        }
    )

    async def parser_recursion(_request):
        raise RecursionError("JSON decoder nesting limit")

    response = asyncio.run(main.collect_http_metrics(request, parser_recursion))

    assert response.status_code == 422
    assert json.loads(response.body) == {"detail": "JSON nesting is too deep"}
    assert requests_metric.labels_seen == [
        {
            "method": "POST",
            "path": "/v2/streams/{stream_id}/events",
            "status": "422",
        }
    ]
    assert requests_metric.increment_count == 1
    assert latency_metric.labels_seen == [
        {
            "method": "POST",
            "path": "/v2/streams/{stream_id}/events",
        }
    ]
    assert len(latency_metric.observations) == 1


def test_http_middleware_reraises_non_json_recursion_and_records_500(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from starlette.requests import Request

    from portfolio import main

    class MetricCapture:
        def __init__(self):
            self.labels_seen = []

        def labels(self, **labels):
            self.labels_seen.append(labels)
            return self

        def inc(self):
            pass

        def observe(self, _value):
            pass

    requests_metric = MetricCapture()
    latency_metric = MetricCapture()
    monkeypatch.setattr(main, "api_requests_total", requests_metric)
    monkeypatch.setattr(main, "api_request_latency_seconds", latency_metric)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/upload",
            "raw_path": b"/upload",
            "query_string": b"",
            "headers": [(b"content-type", b"text/plain")],
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "root_path": "",
            "route": SimpleNamespace(path="/upload"),
        }
    )

    async def application_recursion(_request):
        raise RecursionError("unrelated application recursion")

    with pytest.raises(RecursionError, match="unrelated application recursion"):
        asyncio.run(main.collect_http_metrics(request, application_recursion))

    assert requests_metric.labels_seen == [
        {"method": "POST", "path": "/upload", "status": "500"}
    ]
    assert latency_metric.labels_seen == [
        {"method": "POST", "path": "/upload"}
    ]


def test_event_routes_validate_optional_idempotency_header_as_nonempty_and_nul_free():
    from portfolio.main import app

    route_specs = (
        ("/v1/streams/{stream_id}/events", "POST"),
        ("/v1/orders/{order_id}/events", "POST"),
        ("/v2/streams/{stream_id}/events", "POST"),
    )
    included_routes = [
        route
        for included in app.routes
        for route in getattr(getattr(included, "original_router", None), "routes", [])
    ]
    openapi = app.openapi()

    for path, method in route_specs:
        route = next(
            route
            for route in included_routes
            if route.path == path and method in route.methods
        )
        header_field = next(
            field for field in route.dependant.header_params if field.alias == "x-idempotency-key"
        )
        assert header_field.validate(None, {}, loc=("header", header_field.alias)) == (None, [])
        assert header_field.validate("valid-key", {}, loc=("header", header_field.alias)) == (
            "valid-key",
            [],
        )
        assert header_field.validate("", {}, loc=("header", header_field.alias))[1]
        assert header_field.validate(
            "before\x00after", {}, loc=("header", header_field.alias)
        )[1]

        parameter = next(
            item
            for item in openapi["paths"][path][method.lower()]["parameters"]
            if item["name"] == "x-idempotency-key"
        )
        string_schema = next(
            item for item in parameter["schema"]["anyOf"] if item.get("type") == "string"
        )
        assert string_schema == {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": r"^[^\x00]+$",
        }


def test_event_read_shape_backfills_legacy_rows_and_preserves_generic_rows():
    from portfolio.api import _event_row_to_response

    legacy = _event_row_to_response(
        {
            "id": 1,
            "request_id": "legacy-1",
            "room_id": 42,
            "room_seq": 1,
            "user_id": 7,
            "event_type": None,
            "body": "legacy body",
            "created_at": "2026-07-14T00:00:00+00:00",
        }
    )
    assert legacy["stream_id"] == 42
    assert legacy["actor_id"] == 7
    assert legacy["event_type"] == "legacy.message"
    assert legacy["schema_version"] == 1
    assert legacy["payload"] == {"text": "legacy body"}
    assert legacy["metadata"] == {}

    generic = _event_row_to_response(
        {
            "id": 2,
            "request_id": "generic-1",
            "room_id": 42,
            "room_seq": 2,
            "user_id": 7,
            "event_type": "deployment.finished",
            "schema_version": 2,
            "payload": {},
            "metadata": {"environment": "staging"},
            "body": "{}",
            "created_at": "2026-07-14T00:00:01+00:00",
        }
    )
    assert generic["event_type"] == "deployment.finished"
    assert generic["actor_id"] == 7
    assert generic["schema_version"] == 2
    assert generic["payload"] == {}
    assert generic["metadata"] == {"environment": "staging"}


def test_request_status_authorizes_canonical_actor_id_without_legacy_alias(monkeypatch):
    from fastapi import HTTPException
    from portfolio import api

    status = {
        "request_id": "actor-only-1",
        "status": "persisted",
        "actor_id": 7,
    }
    monkeypatch.setattr(api, "_load_request_status", lambda _request_id: dict(status))

    result = api.get_event_request_status("actor-only-1", current_user={"id": 7})
    assert result["actor_id"] == 7
    assert "user_id" not in result

    with pytest.raises(HTTPException) as exc_info:
        api.get_event_request_status("actor-only-1", current_user={"id": 8})
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Request access denied"


@pytest.mark.parametrize(
    "status",
    [
        {
            "request_id": "conflicting-owner-1",
            "status": "persisted",
            "actor_id": 7,
            "user_id": 8,
        },
        {
            "request_id": "missing-owner-1",
            "status": "persisted",
        },
    ],
)
def test_request_status_rejects_conflicting_alias_or_missing_owner(monkeypatch, status):
    from fastapi import HTTPException
    from portfolio import api

    monkeypatch.setattr(api, "_load_request_status", lambda _request_id: dict(status))

    with pytest.raises(HTTPException) as exc_info:
        api.get_event_request_status(status["request_id"], current_user={"id": 7})
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Request access denied"


@pytest.mark.parametrize("malformed_status", [[], "malformed-status"])
def test_request_status_rejects_non_object_payload(monkeypatch, malformed_status):
    from fastapi import HTTPException
    from portfolio import api

    monkeypatch.setattr(api, "_load_request_status", lambda _request_id: malformed_status)

    with pytest.raises(HTTPException) as exc_info:
        api.get_event_request_status("malformed-1", current_user={"id": 7})
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Request access denied"


def test_worker_dual_reads_legacy_and_generic_ingress_envelopes():
    from worker.main import _validate_ingress_payload

    legacy = _validate_ingress_payload(
        {
            "request_id": "legacy-1",
            "route": "POST:/v1/streams/42/events",
            "room_id": 42,
            "user_id": 7,
            "body": "legacy body",
            "category": "support",
            "payment_id": "pay-legacy",
        }
    )
    assert legacy["event_type"] == "legacy.message"
    assert legacy["schema_version"] == 1
    assert legacy["payload"] == {"text": "legacy body"}
    assert legacy["metadata"] == {
        "classification": "support",
        "external_references": {"payment": "pay-legacy"},
    }

    generic = _validate_ingress_payload(
        {
            "request_id": "generic-1",
            "route": "POST:/v2/streams/42/events",
            "stream_id": 42,
            "actor_id": 7,
            "stream_seq": 3,
            "schema_version": 2,
            "event_type": "deployment.finished",
            "payload": {"message": "deployed", "revision": "abc123"},
            "metadata": {"environment": "staging"},
        }
    )
    assert generic["room_id"] == 42
    assert generic["user_id"] == 7
    assert generic["room_seq"] == 3
    assert generic["body"] == "deployed"
    assert generic["payload"] == {"message": "deployed", "revision": "abc123"}
    assert generic["metadata"] == {"environment": "staging"}


def test_worker_rejects_conflicting_aliases():
    from worker.main import _validate_ingress_payload

    with pytest.raises(ValueError, match="Conflicting stream_id/room_id"):
        _validate_ingress_payload(
            {
                "request_id": "conflict-1",
                "route": "POST:/v2/streams/42/events",
                "stream_id": 42,
                "room_id": 43,
                "actor_id": 7,
                "payload": {},
            }
        )


@pytest.mark.parametrize("event_type", ["", None, False])
def test_worker_rejects_explicit_empty_null_or_bool_v2_event_type(event_type):
    from worker.main import _validate_ingress_payload

    with pytest.raises(ValueError, match="event_type"):
        _validate_ingress_payload(
            {
                "request_id": "invalid-type-1",
                "route": "POST:/v2/streams/42/events",
                "stream_id": 42,
                "actor_id": 7,
                "schema_version": 2,
                "event_type": event_type,
                "payload": {},
                "metadata": {},
            }
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("payload", None, "Invalid payload"),
        ("metadata", None, "Invalid metadata"),
    ],
)
def test_worker_rejects_null_v2_payload_or_metadata(field, value, message):
    from worker.main import _validate_ingress_payload

    envelope = {
        "request_id": "null-envelope-1",
        "route": "POST:/v2/streams/42/events",
        "stream_id": 42,
        "actor_id": 7,
        "schema_version": 2,
        "event_type": "example.created",
        "payload": {},
        "metadata": {},
    }
    envelope[field] = value
    with pytest.raises(ValueError, match=message):
        _validate_ingress_payload(envelope)


@pytest.mark.parametrize("schema_version", [None, False, "2", 0, 1, 3, 32_768])
def test_worker_rejects_invalid_v2_schema_version(schema_version):
    from worker.main import _validate_ingress_payload

    with pytest.raises(ValueError, match="schema_version"):
        _validate_ingress_payload(
            {
                "request_id": "invalid-schema-1",
                "route": "POST:/v2/streams/42/events",
                "stream_id": 42,
                "actor_id": 7,
                "schema_version": schema_version,
                "event_type": "example.created",
                "payload": {},
                "metadata": {},
            }
        )


def test_worker_rejects_missing_v2_schema_version():
    from worker.main import _validate_ingress_payload

    with pytest.raises(ValueError, match="V2 ingress missing schema_version"):
        _validate_ingress_payload(
            {
                "request_id": "missing-schema-1",
                "route": "POST:/v2/streams/42/events",
                "stream_id": 42,
                "actor_id": 7,
                "event_type": "example.created",
                "payload": {},
                "metadata": {},
            }
        )


@pytest.mark.parametrize("idempotency_key", ["", "before\x00after", "x" * 129, False])
def test_worker_rejects_invalid_idempotency_key(idempotency_key):
    from worker.main import _validate_ingress_payload

    with pytest.raises(ValueError, match="Invalid x_idempotency_key"):
        _validate_ingress_payload(
            {
                "request_id": "invalid-idem-1",
                "route": "POST:/v2/streams/42/events",
                "stream_id": 42,
                "actor_id": 7,
                "schema_version": 2,
                "event_type": "example.created",
                "payload": {},
                "metadata": {},
                "x_idempotency_key": idempotency_key,
            }
        )


def test_worker_rejects_non_finite_oversized_schema_and_nested_nul():
    from worker.main import _validate_ingress_payload

    with pytest.raises(ValueError, match="JSON numbers must be finite"):
        _validate_ingress_payload(
            {
                "request_id": "nan-1",
                "route": "POST:/v2/streams/42/events",
                "stream_id": 42,
                "actor_id": 7,
                "schema_version": 2,
                "event_type": "invalid.number",
                "payload": {"value": float("inf")},
                "metadata": {},
            }
        )

    with pytest.raises(ValueError, match="Invalid schema_version"):
        _validate_ingress_payload(
            {
                "request_id": "schema-overflow-1",
                "route": "POST:/v2/streams/42/events",
                "stream_id": 42,
                "actor_id": 7,
                "schema_version": 32_768,
                "event_type": "schema.overflow",
                "payload": {},
                "metadata": {},
            }
        )

    with pytest.raises(ValueError, match="must not contain NUL"):
        _validate_ingress_payload(
            {
                "request_id": "nul-1",
                "route": "POST:/v2/streams/42/events",
                "stream_id": 42,
                "actor_id": 7,
                "schema_version": 2,
                "event_type": "nested.nul",
                "payload": {"items": [{"message": "before\x00after"}]},
                "metadata": {},
            }
        )


def test_worker_enforces_generic_event_type_for_v2_or_schema_v2_only():
    from worker.main import _validate_ingress_payload

    common = {
        "request_id": "type-1",
        "stream_id": 42,
        "actor_id": 7,
        "event_type": "contains spaces",
        "payload": {},
        "metadata": {},
    }
    with pytest.raises(ValueError, match="Invalid generic event_type"):
        _validate_ingress_payload(
            {
                **common,
                "route": "POST:/v2/streams/42/events",
                "schema_version": 2,
            }
        )

    with pytest.raises(ValueError, match="Invalid generic event_type"):
        _validate_ingress_payload(
            {
                **common,
                "request_id": "type-2",
                "route": "POST:/v1/streams/42/events",
                "schema_version": 2,
            }
        )

    legacy = _validate_ingress_payload(
        {
            **common,
            "request_id": "type-legacy",
            "route": "POST:/v1/orders/42/events",
            "schema_version": 1,
        }
    )
    assert legacy["event_type"] == "contains spaces"


def test_compatibility_metadata_fields_require_bounded_strings():
    from worker.main import _compatibility_text

    assert _compatibility_text("a" * 80, max_length=80) == "a" * 80
    assert _compatibility_text("a" * 81, max_length=80) is None
    assert _compatibility_text({"unexpected": "object"}, max_length=80) is None
    assert _compatibility_text("", max_length=80) is None


def test_invalid_or_conflicting_dlq_payload_is_not_replayable():
    from portfolio.api import _summarize_dlq_item
    from portfolio.kafka_client import INVALID_KAFKA_PAYLOAD_MARKER

    invalid = _summarize_dlq_item(
        {
            "value": {
                INVALID_KAFKA_PAYLOAD_MARKER: True,
                "request_id": "invalid-1",
                "room_id": 42,
            }
        }
    )
    assert invalid["replayable"] is False

    conflicting = _summarize_dlq_item(
        {
            "value": {
                "request_id": "conflict-1",
                "stream_id": 42,
                "room_id": 43,
                "actor_id": 7,
                "user_id": 7,
            }
        }
    )
    assert conflicting["replayable"] is False
    assert conflicting["failed_reason"] == "invalid_dlq_payload"


def test_kafka_json_boundary_rejects_non_finite_values_on_write_and_read():
    from portfolio.kafka_client import _deserialize_json, _serialize_json, is_invalid_kafka_payload

    with pytest.raises(ValueError):
        _serialize_json({"value": float("nan")})

    decoded = _deserialize_json(b'{"value":NaN}')
    assert is_invalid_kafka_payload(decoded)
    assert decoded["decode_error"] == "ValueError"


def test_alembic_head_is_generic_envelope_migration_0008():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("path_separator", "os")
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["0008_generic_event_envelope"]
    migration = (ROOT / "alembic/versions/0008_generic_event_envelope.py").read_text(
        encoding="utf-8"
    )
    assert 'down_revision: Union[str, None] = "0007_drop_legacy_room_sequence_allocations"' in migration
    assert "ADD COLUMN IF NOT EXISTS schema_version" in migration
    assert "ADD COLUMN IF NOT EXISTS payload JSONB" in migration
    assert "ADD COLUMN IF NOT EXISTS metadata JSONB" in migration
    assert "jsonb_build_object('text', body)" in migration
    assert "chk_messages_payload_object" in migration
    assert "chk_messages_metadata_object" in migration
    assert "chk_messages_schema_version_positive" in migration
    assert "chk_messages_generic_envelope" in migration
    assert "VALIDATE CONSTRAINT chk_messages_payload_object" in migration
    assert "refusing lossy downgrade" in migration
    assert "payload IS DISTINCT FROM jsonb_build_object('text', body)" in migration
    assert "idx_messages_metadata_gin" not in migration


def test_demo_uses_v2_generic_events_with_order_as_reference_scenario():
    demo = (ROOT / "demo/order-dashboard.html").read_text(encoding="utf-8")

    for token in (
        "Reliable Event Processing Console",
        'const DEMO_UI_VERSION = "2.2.0"',
        "ver. 2.2.0 / api -",
        "Reference Scenario",
        "범용 stream 처리 경계",
        "reference.payment.completed",
        "reference.exception.detected",
        "schema_version / event_type",
        "payload / metadata",
        "Envelope 검증",
        'id="envelope-verification"',
        'id="run-worker-status"',
        "/v2/streams/${streamId}/events",
        "event_type: event.event_type",
        "payload: {",
        "metadata: {",
        'reference_scenario: "order-lifecycle"',
        "accepted.stream_id",
        "function escapeHtml(value)",
    ):
        assert token in demo

    send_function = demo.split("async function sendQueuedEvent", 1)[1].split(
        "async function processReservedEvents", 1
    )[0]
    assert "/v1/orders/" not in send_function
    assert "accepted.order_id" not in send_function
    assert "payment_id:" not in send_function
    assert "order_id=" not in demo
    assert "escapeHtml(event.event_type)" in demo
    assert "escapeHtml(event.body)" in demo
    assert "safeCssToken(event.category)" in demo
    assert "escapeHtml(event.request_id)" in demo
    assert "escapeHtml(event.db_row || \"-\")" in demo

    process_reserved = demo.split("async function processReservedEvents()", 1)[1].split(
        "async function sendEvent()", 1
    )[0]
    assert "startProcessingRun(reservedEvents.length)" in process_reserved
    assert "const token = await ensureToken" in process_reserved
    assert process_reserved.index("startProcessingRun(reservedEvents.length)") < (
        process_reserved.index("const token = await ensureToken")
    )
    assert "async function sendNextReservedEvent()" in process_reserved
    assert "await sendQueuedEvent" in process_reserved
    assert "recordKafkaAppended(1, uiSession)" in process_reserved
    assert "const persistencePromise = pollStreamPersistence(" in process_reserved
    assert "activeEvents.length," in process_reserved
    assert "producerState," in process_reserved
    assert "void pollRunWorkerScaling(baseUrl, uiSession)" in process_reserved
    assert 'event.status = "send_failed"' in process_reserved
    assert "const senderCount = Math.min(SEND_CONCURRENCY, activeEvents.length)" in process_reserved
    assert "await Promise.all(Array.from({ length: senderCount }, () => sendNextReservedEvent()))" in (
        process_reserved
    )
    assert process_reserved.index("const persistencePromise = pollStreamPersistence(") < (
        process_reserved.index(
            "await Promise.all(Array.from({ length: senderCount }, () => sendNextReservedEvent()))"
        )
    )
    assert process_reserved.index("producerState.completed = true") < process_reserved.index(
        "const persistenceConfirmed = await persistencePromise"
    )

    persistence_poll = demo.split("async function pollStreamPersistence", 1)[1].split(
        "async function refreshDlqSummary", 1
    )[0]
    assert "/persistence-summary" in persistence_poll
    assert "setTimeout(resolve, PERSISTENCE_POLL_INTERVAL_MS)" in persistence_poll
    assert "while (!producerState.completed" in persistence_poll
    assert "producerState.completed ? acceptedTarget : expectedCount" in persistence_poll
    assert "recordDbPersisted(persistedCount, uiSession)" in persistence_poll
    assert "/v1/event-requests/" not in persistence_poll

    worker_scaling_poll = demo.split("async function pollRunWorkerScaling", 1)[1].split(
        "function finishProcessingRun", 1
    )[0]
    assert "/health/ready" in worker_scaling_poll
    assert "recordWorkerScaling(readiness.worker, uiSession)" in worker_scaling_poll
    assert "WORKER_SCALING_POLL_INTERVAL_MS" in worker_scaling_poll
    assert "queueStats.workerPeak" in demo
    assert "queueStats.workerStart}→${queueStats.workerPeak}" in demo

    verify_envelope = demo.split("async function verifyGenericEnvelope", 1)[1].split(
        "async function refreshDlqSummary", 1
    )[0]
    assert "/v2/streams/${streamId}/events?limit=100" in verify_envelope
    assert "sample.schema_version === 2" in verify_envelope
    assert "sample.event_type === expected.event_type" in verify_envelope
    assert "deepEqualJson(sample.payload, expected.payload)" in verify_envelope
    assert "deepEqualJson(sample.metadata, expected.metadata)" in verify_envelope
    assert "sample.payload?.message === expected.body" not in verify_envelope
    assert 'sample.metadata?.reference_scenario === "order-lifecycle"' not in verify_envelope
    assert 'setText("#envelope-verification", evidence)' in verify_envelope
    assert "generic envelope verification failed" in verify_envelope
    assert "return false" in verify_envelope

    deep_equal = demo.split("function deepEqualJson", 1)[1].split(
        "function setDemoVersion", 1
    )[0]
    assert "Array.isArray(left)" in deep_equal
    assert "left.every((value, index) => deepEqualJson(value, right[index]))" in deep_equal
    assert "Object.keys(left).sort()" in deep_equal
    assert "deepEqualJson(left[key], right[key])" in deep_equal
    assert "Object.assign(event, accepted)" in send_function

    assert process_reserved.index("const persistenceConfirmed = await persistencePromise") < (
        process_reserved.index("await verifyGenericEnvelope")
    )
    assert "queueStats.envelopeVerified = persistenceConfirmed" in process_reserved
    update_metrics = demo.split("function updateQueueMetrics()", 1)[1].split(
        "function startProcessingRun", 1
    )[0]
    assert "queueStats.envelopeVerified === true" in update_metrics
    assert "t(\"resultPartial\")" in update_metrics
    advisor = demo.split("function updateOperationsAdvisor", 1)[1].split(
        "let opsRefreshTimer", 1
    )[0]
    assert "queueStats.envelopeVerified === false" in advisor
    assert 'statusKey = "advisorAttention"' in advisor

    auth = demo.split("async function ensureToken", 1)[1].split(
        "async function createDemoReferenceStream", 1
    )[0]
    assert "cachedAuth" in auth
    assert "response.status === 401" in auth

    record_kafka = demo.split("function recordKafkaAppended(count, uiSession)", 1)[1].split(
        "function recordDbPersisted", 1
    )[0]
    assert "queueStats.queued -= appended" in record_kafka
    record_persisted = demo.split("function recordDbPersisted(count, uiSession)", 1)[1].split(
        "function recordQueueProcessed", 1
    )[0]
    assert "queueStats.queued -=" not in record_persisted
    assert "queueStats.dbPersisted = Math.max(queueStats.dbPersisted, count)" in record_persisted

    links = demo.split('<div class="links">', 1)[1].split("</div>", 1)[0]
    assert 'data-ops-link="/docs"' in links
    assert "http://localhost/docs" not in links
    assert "http://localhost/grafana" not in links
