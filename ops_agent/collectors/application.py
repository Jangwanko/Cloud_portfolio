"""Read-only collection of the application's operational HTTP endpoints."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import json
import re
import threading
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


APPLICATION_ENDPOINTS = {
    "readiness": "/health/ready",
    "ops_summary": "/ops/summary",
}
OPS_SUMMARY_CACHE_MAX_AGE_SECONDS = 15

HttpGet = Callable[[str, float], tuple[int, bytes]]
_MAX_RESPONSE_BYTES = 1024 * 1024
_SAFE_HOST_HEADER = re.compile(r"^[A-Za-z0-9.-]+(?::[0-9]{1,5})?$", flags=re.ASCII)

_APP_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]*$", flags=re.ASCII)
_DEPLOYMENT_NAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$", flags=re.ASCII
)
_ERROR_TYPE = re.compile(r"^[A-Z][A-Za-z0-9.]{0,127}$", flags=re.ASCII)
_READINESS_REASONS = {
    "schema_not_ready",
    "kafka_unreachable",
    "postgres_primary_unreachable",
    "postgres_ready_standbys_below_minimum",
    "postgres_sync_standbys_below_minimum",
    "postgres_replication_delay_high",
    "unsafe_auth_secret",
}


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


_HTTP_OPENER = build_opener(ProxyHandler({}), _NoRedirectHandler())


class ApplicationCollectionError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validated_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("application base URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("application base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("application base URL must not contain a query or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("application base URL path must be empty")
    return base_url.rstrip("/")


def _validated_host_header(host_header: str | None) -> str | None:
    if host_header is None:
        return None
    if _SAFE_HOST_HEADER.fullmatch(host_header) is None:
        raise ValueError("application Host header is invalid")
    return host_header


def _integer_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _boolean_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _project_application_body(
    endpoint_name: str, body: Mapping[str, Any]
) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    app_version = body.get("app_version")
    if isinstance(app_version, str) and _APP_VERSION.fullmatch(app_version):
        projected["app_version"] = app_version

    if endpoint_name == "readiness":
        status = body.get("status")
        if isinstance(status, str) and status in {"ready", "degraded", "not_ready"}:
            projected["status"] = status
        reasons = body.get("reason")
        projected["reason"] = (
            [
                reason
                for reason in reasons
                if isinstance(reason, str) and reason in _READINESS_REASONS
            ]
            if isinstance(reasons, list)
            else None
        )
        projected["grace_remaining_seconds"] = _integer_or_none(
            body.get("grace_remaining_seconds")
        )
        if body.get("queue_backend") == "kafka":
            projected["queue_backend"] = "kafka"

        kafka = body.get("kafka")
        if isinstance(kafka, Mapping):
            projected["kafka"] = {
                "bootstrap_reachable": _boolean_or_none(
                    kafka.get("bootstrap_reachable")
                )
            }
        postgres = body.get("postgres")
        if isinstance(postgres, Mapping):
            projected["postgres"] = {
                "ha_mode": _boolean_or_none(postgres.get("ha_mode")),
                "primary_reachable": _boolean_or_none(
                    postgres.get("primary_reachable")
                ),
                "standby_count": _integer_or_none(postgres.get("standby_count")),
                "sync_standby_count": _integer_or_none(
                    postgres.get("sync_standby_count")
                ),
                "max_replication_delay_bytes": _integer_or_none(
                    postgres.get("max_replication_delay_bytes")
                ),
            }
        return projected

    worker = body.get("worker")
    if not isinstance(worker, Mapping):
        return projected
    projected_worker: dict[str, Any] = {}
    deployment = worker.get("deployment")
    if isinstance(deployment, str) and _DEPLOYMENT_NAME.fullmatch(deployment):
        projected_worker["deployment"] = deployment
    for field in (
        "desired_replicas",
        "available_replicas",
        "hpa_desired_replicas",
        "max_replicas",
    ):
        projected_worker[field] = _integer_or_none(worker.get(field))
    source = worker.get("source")
    if isinstance(source, str) and source in {"prometheus", "unavailable"}:
        projected_worker["source"] = source
    error = worker.get("error")
    projected_worker["error"] = (
        error
        if isinstance(error, str) and _ERROR_TYPE.fullmatch(error)
        else None
    )
    projected["worker"] = projected_worker
    return projected


def _default_http_get(
    url: str, timeout_seconds: float, *, host_header: str | None = None
) -> tuple[int, bytes]:
    headers = {"Accept": "application/json"}
    if host_header is not None:
        headers["Host"] = host_header
    request = Request(url, headers=headers, method="GET")
    try:
        with _HTTP_OPENER.open(request, timeout=timeout_seconds) as response:  # noqa: S310
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(payload) > _MAX_RESPONSE_BYTES:
                raise ApplicationCollectionError(
                    "application response exceeded the size limit"
                )
            return int(response.status), payload
    except HTTPError as exc:
        # A non-2xx response is still collected evidence. In particular,
        # /health/ready intentionally returns a structured body with 503.
        payload = exc.read(_MAX_RESPONSE_BYTES + 1)
        if len(payload) > _MAX_RESPONSE_BYTES:
            raise ApplicationCollectionError(
                "application response exceeded the size limit"
            )
        return int(exc.code), payload


def _http_get_with_deadline(
    http_get: HttpGet, url: str, timeout_seconds: float
) -> tuple[int, bytes]:
    outcome: list[tuple[bool, object]] = []

    def invoke() -> None:
        try:
            outcome.append((True, http_get(url, timeout_seconds)))
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller thread.
            outcome.append((False, exc))

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise TimeoutError("application endpoint exceeded the total time limit")
    if not outcome:
        raise ApplicationCollectionError("application endpoint returned no result")
    succeeded, value = outcome[0]
    if not succeeded:
        raise value  # type: ignore[misc]
    return value  # type: ignore[return-value]


def _error(error_type: str, message: str) -> dict[str, Any]:
    return {"type": error_type, "message": message}


def _collect_endpoint(
    *,
    base_url: str,
    endpoint_name: str,
    path: str,
    timeout_seconds: float,
    http_get: HttpGet,
    collected_at: str,
) -> dict[str, Any]:
    semantic: dict[str, Any] = {
        "type": (
            "application_readiness"
            if endpoint_name == "readiness"
            else "application_ops_summary"
        ),
        "advisory": endpoint_name == "ops_summary",
    }
    if endpoint_name == "ops_summary":
        semantic.update(
            {
                "cache_max_age_seconds": OPS_SUMMARY_CACHE_MAX_AGE_SECONDS,
                "notes": "advisory application summary with an in-process 15 second cache",
            }
        )

    result: dict[str, Any] = {
        "status": "ERROR",
        "path": path,
        "http_status": None,
        "body": None,
        "body_status": None,
        "source_timestamp": None,
        "collected_at": collected_at,
        "semantic": semantic,
        "error": None,
    }
    try:
        http_status, payload = _http_get_with_deadline(
            http_get, f"{base_url}{path}", timeout_seconds
        )
        result["http_status"] = http_status
        body = json.loads(payload.decode("utf-8"))
        if not isinstance(body, Mapping):
            raise ValueError("response body must be a JSON object")
    except (OSError, TimeoutError) as exc:
        result["error"] = _error(type(exc).__name__, "application endpoint unreachable")
        return result
    except ApplicationCollectionError as exc:
        result["error"] = _error(type(exc).__name__, str(exc))
        return result
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        result["error"] = _error(type(exc).__name__, "application endpoint returned invalid JSON")
        return result

    projected_body = _project_application_body(endpoint_name, body)
    result["status"] = "OK"
    result["body"] = projected_body
    result["body_status"] = projected_body.get("status")
    return result


def collect_application(
    base_url: str,
    *,
    timeout_seconds: float = 5.0,
    host_header: str | None = None,
    http_get: HttpGet | None = None,
    collected_at: str | None = None,
) -> dict[str, Any]:
    """Collect readiness and advisory operations responses without judging them."""

    timestamp = collected_at or _utc_now()
    try:
        if timeout_seconds <= 0:
            raise ValueError("application timeout_seconds must be positive")
        normalized_base_url = _validated_base_url(base_url)
        normalized_host_header = _validated_host_header(host_header)
    except ValueError as exc:
        return {
            "status": "ERROR",
            "source": "application_http",
            "collected_at": timestamp,
            "partial": False,
            "data": {},
            "error": _error(type(exc).__name__, str(exc)),
        }

    requester = http_get or (
        lambda url, timeout: _default_http_get(
            url, timeout, host_header=normalized_host_header
        )
    )
    data = {
        name: _collect_endpoint(
            base_url=normalized_base_url,
            endpoint_name=name,
            path=path,
            timeout_seconds=timeout_seconds,
            http_get=requester,
            collected_at=timestamp,
        )
        for name, path in APPLICATION_ENDPOINTS.items()
    }
    failed = [name for name, item in data.items() if item["status"] != "OK"]
    return {
        "status": "OK" if not failed else "ERROR",
        "source": "application_http",
        "collected_at": timestamp,
        "partial": bool(failed) and len(failed) < len(data),
        "data": data,
        "error": (
            None
            if not failed
            else _error("PartialCollectionError", f"failed endpoints: {', '.join(failed)}")
        ),
    }
