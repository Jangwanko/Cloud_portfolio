"""Canonical, credential-free identities for configured HTTP evidence sources."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


ENDPOINT_IDENTITY_VERSION = "ops.endpoint.v1"
_SAFE_HOST_HEADER = re.compile(
    r"^[A-Za-z0-9.-]+(?::[0-9]{1,5})?$", flags=re.ASCII
)


def _canonical_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("endpoint must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("endpoint must not contain a query or fragment")

    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("endpoint hostname is missing")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("endpoint port is invalid") from exc

    scheme = parsed.scheme.lower()
    host = hostname.lower()
    authority_host = f"[{host}]" if ":" in host else host
    default_port = 80 if scheme == "http" else 443
    authority = (
        authority_host
        if port is None or port == default_port
        else f"{authority_host}:{port}"
    )
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, authority, path, "", ""))


def endpoint_provenance(
    *,
    base_url: str,
    host_header: str | None,
    configuration_source: str,
) -> dict[str, Any]:
    """Return a stable identity without retaining credentials or URL parameters."""

    if configuration_source not in {"policy", "operator_override"}:
        raise ValueError("unsupported endpoint configuration source")
    canonical_url = _canonical_base_url(base_url)
    canonical_host_header = host_header.lower() if host_header is not None else None
    if (
        canonical_host_header is not None
        and _SAFE_HOST_HEADER.fullmatch(canonical_host_header) is None
    ):
        raise ValueError("endpoint Host header is invalid")

    identity_payload = {
        "base_url": canonical_url,
        "host_header": canonical_host_header,
    }
    canonical_payload = json.dumps(
        identity_payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "identity_version": ENDPOINT_IDENTITY_VERSION,
        **identity_payload,
        "configuration_source": configuration_source,
        "identity_sha256": hashlib.sha256(
            canonical_payload.encode("utf-8")
        ).hexdigest(),
    }


def safe_endpoint_provenance(
    *,
    base_url: str,
    host_header: str | None,
    configuration_source: str,
) -> dict[str, Any]:
    """Build endpoint provenance while keeping invalid input out of artifacts."""

    try:
        return {
            "status": "OK",
            "value": endpoint_provenance(
                base_url=base_url,
                host_header=host_header,
                configuration_source=configuration_source,
            ),
            "error": None,
        }
    except (TypeError, ValueError) as exc:
        return {
            "status": "ERROR",
            "value": None,
            "error": {
                "type": type(exc).__name__,
                "message": "effective endpoint identity could not be recorded safely",
            },
        }
