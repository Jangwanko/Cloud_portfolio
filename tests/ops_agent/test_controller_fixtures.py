from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from ops_agent.artifacts import write_raw_artifact
from ops_agent import controller
from ops_agent.controller import build_bundle_from_results
from ops_agent.models import CollectionStatus, EvidenceStatus, FreshnessStatus
from ops_agent.policies import load_policy


FIXTURES = Path(__file__).resolve().parents[2] / "ops_agent" / "fixtures"
STARTED = datetime(2026, 8, 12, 1, 2, 2, tzinfo=timezone.utc)
COMPLETED = datetime(2026, 8, 12, 1, 2, 3, tzinfo=timezone.utc)


def _load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _build(tmp_path, results, *, bundle_id="fixture-bundle"):
    return build_bundle_from_results(
        policy=load_policy("local-ha"),
        collector_results=results,
        incident_id="fixture-incident",
        cluster_context="kind-messaging-ha",
        artifact_root=tmp_path / "raw",
        bundle_id=bundle_id,
        started_at=STARTED,
        completed_at=COMPLETED,
    )


def _metric(bundle, name, **labels):
    return next(
        item
        for item in bundle.evidence
        if item.metric.name == name
        and all(item.labels.get(key) == value for key, value in labels.items())
    )


def test_all_sources_fixture_builds_schema_v1_with_runtime_context(tmp_path) -> None:
    results = _load("all_sources_available")

    bundle = _build(tmp_path, results)

    assert bundle.schema_version == "ops.evidence.v1"
    assert bundle.collection.status == CollectionStatus.COMPLETE
    assert bundle.scope.context == "kind-messaging-ha"
    assert bundle.context.desired_image == "ghcr.io/example/app:abc123"
    assert bundle.context.pod_image_ids == ["ghcr.io/example/app@sha256:deadbeef"]
    assert bundle.context.argocd_revision == "abc123"
    argocd = _metric(bundle, "argocd_application_observation")
    assert argocd.source_timestamp == datetime(
        2026, 8, 12, 1, 2, 0, tzinfo=timezone.utc
    )
    assert argocd.freshness.status == FreshnessStatus.FRESH
    assert argocd.freshness.basis == "argocd_application_reconciled_at"
    readiness = _metric(bundle, "application_readiness_observation")
    assert readiness.metric.value["http_status"] == 200
    assert readiness.metric.value["body_status"] == "ready"
    postgres = _metric(bundle, "application_postgres_runtime_observation")
    assert postgres.metric.value == {
        "ha_mode": True,
        "primary_reachable": True,
        "standby_count": 2,
        "sync_standby_count": 1,
        "max_replication_delay_bytes": 0,
    }

    terminal = _metric(bundle, "messaging_worker_processed_total", result="success")
    assert terminal.semantic.type == "worker_terminal_processing_counter"
    assert terminal.semantic.is_db_commit_rate is False
    assert "DB commit" in terminal.semantic.notes
    observed = _metric(bundle, "messaging_event_persist_lag_seconds_count")
    assert observed.semantic.type == "api_queued_at_to_post_commit_observed_lag_histogram"
    assert "not_isolated_postgresql_commit_latency" in observed.semantic.flags
    persist_stage = _metric(bundle, "messaging_worker_stage_latency_seconds_count")
    assert "excludes_transaction_commit" in persist_stage.semantic.flags

    unavailable = [item for item in bundle.evidence if item.status == EvidenceStatus.UNAVAILABLE]
    assert {item.metric.name for item in unavailable} == {
        "exact_postgres_commit_insert_rate",
        "postgres_transaction_commit_latency",
        "consumer_rebalance_event",
        "cpu_throttling",
    }
    assert all(item.metric.value is None for item in unavailable)
    assert all(item.raw_ref and Path(item.raw_ref).is_file() for item in bundle.evidence if item.raw_ref)


def test_evidence_ids_are_deterministic_across_bundle_ids(tmp_path) -> None:
    results = _load("all_sources_available")

    first = _build(tmp_path / "one", results, bundle_id="one")
    second = _build(tmp_path / "two", results, bundle_id="two")

    assert [item.evidence_id for item in first.evidence] == [
        item.evidence_id for item in second.evidence
    ]
    assert len({item.evidence_id for item in first.evidence}) == len(first.evidence)


def test_prometheus_missing_series_is_not_zero(tmp_path) -> None:
    results = _load("all_sources_available")
    query = results["prometheus"]["queries"][0]
    query.update(_load("prometheus_missing_series"))
    query["coverage"] = {
        "expected_partition_ids": [str(value) for value in range(8)],
        "observed_partition_ids": [],
        "missing_partition_ids": [str(value) for value in range(8)],
        "unexpected_partition_ids": [],
        "complete": False,
    }
    results["prometheus"]["partial"] = True

    bundle = _build(tmp_path, results)
    missing = _metric(bundle, "kafka_topic_partition_current_offset")

    assert missing.status == EvidenceStatus.MISSING
    assert missing.metric.value == []
    assert missing.metric.sample_count == 0
    assert missing.freshness.status == FreshnessStatus.UNKNOWN
    assert bundle.collection.status == CollectionStatus.PARTIAL


def test_partial_partition_coverage_remains_explicit(tmp_path) -> None:
    results = _load("all_sources_available")
    query = results["prometheus"]["queries"][0]
    query["coverage"] = _load("partition_partial_coverage")

    bundle = _build(tmp_path, results)
    partition = _metric(bundle, "kafka_topic_partition_current_offset", partition="0")

    assert partition.coverage.complete is False
    assert partition.coverage.missing_items == ["1"]
    assert "partial_partition_coverage" in partition.semantic.flags


def test_range_evaluation_timestamp_is_never_used_as_source_freshness(tmp_path) -> None:
    results = _load("all_sources_available")
    query = results["prometheus"]["queries"][0]
    query["freshness"] = {
        "query_status": "ERROR",
        "status": "UNKNOWN",
        "basis": "prometheus_timestamp_function",
        "source_timestamp": None,
        "age_seconds": None,
        "max_age_seconds": 15,
        "coverage": {"labels_match_range": None},
        "series": [],
        "error": "timestamp query unavailable",
    }
    results["prometheus"]["partial"] = True

    bundle = _build(tmp_path, results)
    partition = _metric(bundle, "kafka_topic_partition_current_offset", partition="0")

    assert partition.status == EvidenceStatus.OK
    assert partition.metric.value[-1]["timestamp"] == 1786496520
    assert partition.source_timestamp is None
    assert partition.freshness.status == FreshnessStatus.UNKNOWN
    assert "source_timestamp_query_error" in partition.semantic.flags
    assert bundle.collection.status == CollectionStatus.PARTIAL


def test_minus_one_and_offset_decrease_are_preserved(tmp_path) -> None:
    minus_one = _load("offset_minus_one")
    results = _load("all_sources_available")
    committed = results["prometheus"]["queries"][1]
    committed["series"] = [{"labels": minus_one["labels"], "samples": minus_one["samples"]}]
    committed["anomalies"] = [{**minus_one["anomaly"], "labels": minus_one["labels"]}]
    committed["sample_count"] = 1

    bundle = _build(tmp_path / "minus", results)
    evidence = _metric(bundle, "kafka_consumergroup_current_offset", partition="0")
    assert evidence.metric.value[0]["value"] == "-1"
    assert minus_one["expected_flag"] in evidence.semantic.flags

    decrease = _load("offset_decrease")
    results = _load("all_sources_available")
    end_offset = results["prometheus"]["queries"][0]
    end_offset["series"] = [{"labels": decrease["labels"], "samples": decrease["samples"]}]
    end_offset["anomalies"] = [{**decrease["anomaly"], "labels": decrease["labels"]}]
    end_offset["sample_count"] = 2
    bundle = _build(tmp_path / "decrease", results)
    evidence = _metric(bundle, "kafka_topic_partition_current_offset", partition="0")
    assert [sample["value"] for sample in evidence.metric.value] == ["101", "99"]
    assert "offset_decrease" in evidence.semantic.flags


def test_application_failure_and_stale_ops_summary_are_structured(tmp_path) -> None:
    results = _load("all_sources_available")
    results["application"] = _load("application_unreachable")
    bundle = _build(tmp_path / "unreachable", results)
    readiness = _metric(bundle, "application_readiness_observation")
    assert readiness.status == EvidenceStatus.ERROR
    assert readiness.error.type == "ConnectionError"
    assert bundle.collection.status == CollectionStatus.PARTIAL

    results = _load("all_sources_available")
    stale = _load("ops_summary_stale")
    results["application"]["data"]["ops_summary"]["collected_at"] = stale["collected_at"]
    bundle = _build(tmp_path / "stale", results)
    ops_summary = _metric(bundle, "application_ops_summary_observation")
    assert ops_summary.freshness.status.value == stale["expected_freshness"]
    assert ops_summary.freshness.age_seconds >= stale["cache_bound_seconds"]


def test_kubernetes_partial_failure_keeps_null_and_successful_resources(tmp_path) -> None:
    results = _load("all_sources_available")
    results["kubernetes"] = _load("kubernetes_partial_failure")

    bundle = _build(tmp_path, results)
    deployment = _metric(bundle, "kubernetes_worker_deployment_observation")
    pods = _metric(bundle, "kubernetes_worker_pod_observations")

    assert deployment.status == EvidenceStatus.OK
    assert deployment.metric.value["unavailable_replicas"] is None
    assert pods.status == EvidenceStatus.ERROR
    assert bundle.collection.status == CollectionStatus.PARTIAL


def test_argocd_not_applicable_is_not_a_collection_failure(tmp_path) -> None:
    results = _load("all_sources_available")
    results["argocd"] = _load("argocd_not_applicable")

    bundle = _build(tmp_path, results)
    argocd = _metric(bundle, "argocd_application_observation")

    assert argocd.status == EvidenceStatus.NOT_APPLICABLE
    assert argocd.freshness.status == FreshnessStatus.NOT_APPLICABLE
    assert bundle.collection.status == CollectionStatus.COMPLETE


def test_context_only_success_does_not_turn_all_source_failure_into_partial(tmp_path) -> None:
    error = {"type": "Unavailable", "message": "source unavailable"}
    collected_at = "2026-08-12T01:02:03Z"
    results = {
        "application": _load("application_unreachable"),
        "prometheus": {
            "source": "prometheus",
            "status": "ERROR",
            "partial": False,
            "collected_at": collected_at,
            "queries": [],
            "error": error,
        },
        "kubernetes": {
            "source": "kubernetes_api_via_kubectl",
            "status": "ERROR",
            "partial": True,
            "collected_at": collected_at,
            "context": {"status": "OK", "context": "kind-messaging-ha"},
            "data": {
                name: {
                    "status": "ERROR",
                    "collected_at": collected_at,
                    "data": None,
                    "error": error,
                }
                for name in ("deployment", "pods", "scaled_object")
            },
            "error": error,
        },
        "argocd": {
            "source": "argocd_application_cr",
            "status": "ERROR",
            "partial": False,
            "collected_at": collected_at,
            "data": {"applicability": "APPLICABLE", "application": None},
            "error": error,
        },
    }

    bundle = _build(tmp_path, results)

    assert bundle.scope.context == "kind-messaging-ha"
    assert bundle.collection.status == CollectionStatus.FAILED


def test_raw_redaction_fixture_never_reaches_artifact(tmp_path) -> None:
    payload = _load("raw_redaction")

    raw_ref, _digest = write_raw_artifact(tmp_path, "bundle", "raw_redaction", payload)
    written = Path(raw_ref).read_text(encoding="utf-8")

    assert "do-not-store" not in written
    assert "[REDACTED]" in written


def test_normalized_evidence_redacts_future_sensitive_fields(tmp_path) -> None:
    results = _load("all_sources_available")
    results["application"]["data"]["readiness"]["body"].update(
        _load("raw_redaction")
    )

    bundle = _build(tmp_path, results)
    encoded = json.dumps(bundle.model_dump(mode="json"))

    assert "do-not-store" not in encoded
    assert "[REDACTED]" in encoded


def test_raw_artifact_failure_is_isolated_as_error_evidence(monkeypatch, tmp_path) -> None:
    original = controller.write_raw_artifact

    def fail_one_source(artifact_root, bundle_id, source, payload):
        if source == "application":
            raise PermissionError("sensitive filesystem detail")
        return original(artifact_root, bundle_id, source, payload)

    monkeypatch.setattr(controller, "write_raw_artifact", fail_one_source)

    bundle = _build(tmp_path, _load("all_sources_available"))
    artifact_error = _metric(bundle, "raw_artifact_write")

    assert bundle.collection.status == CollectionStatus.PARTIAL
    assert artifact_error.status == EvidenceStatus.ERROR
    assert artifact_error.labels == {"collector_source": "application"}
    assert artifact_error.raw_ref is None
    assert artifact_error.error.message == "redacted raw artifact could not be written"
    assert "sensitive filesystem detail" not in json.dumps(bundle.model_dump(mode="json"))


def test_runtime_context_rejects_credentials_and_opaque_tokens(tmp_path) -> None:
    results = _load("all_sources_available")
    results["kubernetes"]["data"]["deployment"]["data"]["desired_containers"] = [
        {"name": "worker", "image": "https://user:IMAGEPASS@registry.example/app"}
    ]
    github_token = "ghp_012345678901234567890123456789012345"
    results["kubernetes"]["data"]["pods"]["data"][0]["containers"][0][
        "image_id"
    ] = f"registry.example/app:{github_token}"
    results["argocd"]["data"]["application"]["revision"] = github_token

    bundle = _build(tmp_path, results)
    encoded = json.dumps(bundle.model_dump(mode="json"))
    raw = "".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.json"))

    assert bundle.context.desired_image is None
    assert bundle.context.pod_image_ids == []
    assert bundle.context.argocd_revision is None
    assert "IMAGEPASS" not in encoded + raw
    assert github_token not in encoded + raw
