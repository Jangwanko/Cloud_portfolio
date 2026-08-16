from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

import pytest
from pydantic import ValidationError

from ops_agent.artifacts import redact, sanitize_text, write_raw_artifact
from ops_agent.models import (
    BundleContext,
    CollectionMetadata,
    CollectionStatus,
    Coverage,
    EvidenceBundle,
    EvidenceItem,
    EvidenceStatus,
    Freshness,
    FreshnessStatus,
    MetricObservation,
    Scope,
    Semantic,
)
from ops_agent.policies import load_policy


NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


def _item(evidence_id: str = "prometheus.kafka_lag.abc") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        status=EvidenceStatus.OK,
        source="prometheus",
        tool_id="prom.kafka_consumer_lag.v1",
        source_timestamp=NOW,
        collected_at=NOW,
        freshness=Freshness(
            status=FreshnessStatus.FRESH,
            age_seconds=0,
            max_age_seconds=15,
            basis="prometheus_sample_timestamp",
        ),
        metric=MetricObservation(
            name="kafka_consumergroup_lag",
            value=-1,
            unit="records",
            window="60s",
            aggregation="raw_partition_range",
            sample_count=1,
        ),
        labels={"topic": "message-ingress", "partition": "0"},
        coverage=Coverage(expected_count=8, observed_count=1, complete=False),
        semantic=Semantic(
            type="consumer_partition_lag",
            notes="raw exporter value; -1 is not zero",
            flags=["uninitialized_offset"],
        ),
    )


def _bundle(*items: EvidenceItem) -> EvidenceBundle:
    return EvidenceBundle(
        bundle_id="bundle-1",
        incident_id="incident-1",
        cluster_profile="local-ha",
        scope=Scope(
            context="kind-messaging-ha",
            namespace="messaging-app",
            topic="message-ingress",
            consumer_group="message-worker",
        ),
        context=BundleContext(
            source_sha="abc123",
            collector_version="0.1.0",
            tool_registry_version="ops.readonly.v1",
            policy_version="local-ha.evidence.v1",
        ),
        collection=CollectionMetadata(
            started_at=NOW,
            completed_at=NOW,
            status=CollectionStatus.COMPLETE,
        ),
        evidence=list(items),
    )


def test_evidence_bundle_schema_preserves_minus_one() -> None:
    payload = _bundle(_item()).model_dump(mode="json")

    assert payload["schema_version"] == "ops.evidence.v1"
    assert payload["evidence"][0]["metric"]["value"] == -1
    assert payload["evidence"][0]["semantic"]["flags"] == ["uninitialized_offset"]


def test_bundle_rejects_duplicate_evidence_ids() -> None:
    with pytest.raises(ValidationError, match="must be unique"):
        _bundle(_item(), _item())


def test_error_status_requires_structured_error() -> None:
    item = _item().model_copy(update={"status": EvidenceStatus.ERROR})

    with pytest.raises(ValidationError, match="requires error detail"):
        EvidenceItem.model_validate(item.model_dump())


def test_raw_artifact_redacts_credentials_and_hashes_written_bytes(tmp_path) -> None:
    raw_ref, digest = write_raw_artifact(
        tmp_path,
        "bundle-1",
        "application",
        {
            "Authorization": "Bearer top-secret",
            "token": "top-secret",
            "apiKey": "camel-secret",
            "x-api-key": "header-secret",
            "AccessKeyId": "access-secret",
            "nested": {
                "client-key-data": "private-key",
                "url": "postgresql://portfolio:password@db/portfolio?api_key=abc",
            },
            "safe": "ready",
        },
    )

    encoded = (tmp_path / "bundle-1" / "application.json").read_bytes()
    payload = json.loads(encoded)
    assert payload["Authorization"] == "[REDACTED]"
    assert payload["token"] == "[REDACTED]"
    assert payload["apiKey"] == "[REDACTED]"
    assert payload["x-api-key"] == "[REDACTED]"
    assert payload["AccessKeyId"] == "[REDACTED]"
    assert payload["nested"]["client-key-data"] == "[REDACTED]"
    assert "password" not in payload["nested"]["url"]
    assert "abc" not in payload["nested"]["url"]
    assert payload["safe"] == "ready"
    assert digest == hashlib.sha256(encoded).hexdigest()
    assert raw_ref.endswith("bundle-1/application.json")


def test_redaction_covers_inline_environment_style_secrets() -> None:
    value = sanitize_text(
        "API_KEY=one AWS_ACCESS_KEY_ID:two token='three four' safe=ready"
    )

    assert "one" not in value
    assert "two" not in value
    assert "three four" not in value
    assert "safe=ready" in value
    assert redact({"clientSecret": "five"}) == {"clientSecret": "[REDACTED]"}
    opaque = sanitize_text(
        "ghp_012345678901234567890123456789012345 "
        "https://hooks.slack.com/services/T/B/SECRET"
    )
    assert "ghp_" not in opaque
    assert "hooks.slack.com" not in opaque


def test_local_ha_policy_separates_rate_window_from_sample_age() -> None:
    policy = load_policy("local-ha")

    assert policy["prometheus"]["range_window_seconds"] == 60
    assert policy["prometheus"]["sample_max_age_seconds"] == 15
    assert policy["application"]["ops_summary_cache_seconds"] == 15
    assert policy["application"]["base_url"] == "http://127.0.0.1"
    assert policy["application"]["host_header"] == "localhost"
    assert policy["prometheus"]["base_url"] == "http://127.0.0.1/prometheus"
    assert policy["prometheus"]["host_header"] == "localhost"
    assert policy["argocd"]["reconciliation_max_age_seconds"] == 300
    assert "pressure" not in policy
    assert "recovery" not in policy
