from __future__ import annotations

from ops_agent.endpoint_provenance import (
    ENDPOINT_IDENTITY_VERSION,
    endpoint_provenance,
    safe_endpoint_provenance,
)


def test_endpoint_identity_is_canonical_and_binds_host_routing() -> None:
    first = endpoint_provenance(
        base_url="HTTP://LOCALHOST:80/prometheus/",
        host_header="LOCALHOST",
        configuration_source="policy",
    )
    equivalent = endpoint_provenance(
        base_url="http://localhost/prometheus",
        host_header="localhost",
        configuration_source="policy",
    )
    different_routing = endpoint_provenance(
        base_url="http://localhost/prometheus",
        host_header=None,
        configuration_source="operator_override",
    )

    assert first["identity_version"] == ENDPOINT_IDENTITY_VERSION
    assert first["base_url"] == "http://localhost/prometheus"
    assert first["host_header"] == "localhost"
    assert first["identity_sha256"] == equivalent["identity_sha256"]
    assert first["identity_sha256"] != different_routing["identity_sha256"]
    assert different_routing["configuration_source"] == "operator_override"


def test_invalid_endpoint_is_not_reflected_into_provenance_error() -> None:
    unsafe_url = "http://operator:super-secret@example.test/prometheus"

    result = safe_endpoint_provenance(
        base_url=unsafe_url,
        host_header=None,
        configuration_source="operator_override",
    )

    assert result["status"] == "ERROR"
    assert result["value"] is None
    assert "super-secret" not in result["error"]["message"]


def test_configuration_source_is_closed_to_known_origins() -> None:
    result = safe_endpoint_provenance(
        base_url="http://127.0.0.1",
        host_header=None,
        configuration_source="environment",
    )

    assert result["status"] == "ERROR"
    assert result["value"] is None
