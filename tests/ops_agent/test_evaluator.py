from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ops_agent.evaluation_models import (
    AssessmentName,
    ConditionName,
    ConditionState,
    DependencyRequirement,
    EvaluationPolicy,
    EvaluationStatus,
    canonical_sha256,
)
from ops_agent.evaluator import (
    CONDITION_DEPENDENCIES,
    EVALUATOR_VERSION,
    RULESET_VERSION,
    evaluate_bundle,
)
import ops_agent.evaluator as evaluator_module


CAPTURE = (
    Path(__file__).resolve().parents[2]
    / "results"
    / "ops-agent"
    / "live-baseline"
    / "no-backlog-20260812.json"
)


@pytest.fixture
def captured_bundle() -> dict:
    return json.loads(CAPTURE.read_text(encoding="utf-8"))


def _condition(evaluation, name: ConditionName):
    return evaluation.conditions[name]


def _evidence(bundle: dict, metric_name: str) -> list[dict]:
    return [
        item
        for item in bundle["evidence"]
        if item["metric"]["name"] == metric_name
    ]


def _dependency(result, name: str, requirement: DependencyRequirement):
    traces = (
        result.required_evidence
        if requirement == DependencyRequirement.REQUIRED
        else result.optional_evidence
    )
    return next(trace for trace in traces if trace.dependency == name)


def _set_latest_value(item: dict, value: str) -> None:
    item["metric"]["value"][-1]["value"] = value


def _set_consistent_partition_lag(bundle: dict, partition: str, lag: int) -> None:
    committed = next(
        item
        for item in _evidence(bundle, "kafka_consumergroup_current_offset")
        if item["labels"]["partition"] == partition
    )
    end_offset = next(
        item
        for item in _evidence(bundle, "kafka_topic_partition_current_offset")
        if item["labels"]["partition"] == partition
    )
    lag_item = next(
        item
        for item in _evidence(bundle, "kafka_consumergroup_lag")
        if item["labels"]["partition"] == partition
    )
    for committed_sample, end_sample, lag_sample in zip(
        committed["metric"]["value"],
        end_offset["metric"]["value"],
        lag_item["metric"]["value"],
    ):
        committed_value = int(committed_sample["value"])
        end_sample["value"] = str(committed_value + lag)
        lag_sample["value"] = str(lag)


def _set_postgres_readiness_reason(
    bundle: dict,
    *,
    reason: str,
    field: str,
    value,
) -> None:
    readiness = _evidence(bundle, "application_readiness_observation")[0]
    postgres = _evidence(
        bundle,
        "application_postgres_runtime_observation",
    )[0]
    readiness["metric"]["value"]["body_status"] = "degraded"
    body = readiness["metric"]["value"]["body"]
    body["status"] = "degraded"
    body["reason"] = [reason]
    body["postgres"][field] = value
    postgres["metric"]["value"][field] = value


def _set_postgres_field(bundle: dict, field: str, value) -> None:
    readiness = _evidence(bundle, "application_readiness_observation")[0]
    postgres = _evidence(
        bundle,
        "application_postgres_runtime_observation",
    )[0]
    readiness["metric"]["value"]["body"]["postgres"][field] = value
    postgres["metric"]["value"][field] = value


def test_captured_partial_bundle_has_complete_no_backlog_evaluation(
    captured_bundle,
) -> None:
    evaluation = evaluate_bundle(captured_bundle)

    assert evaluation.schema_version == "ops.conditions.v1"
    assert evaluation.evaluator_version == EVALUATOR_VERSION
    assert evaluation.ruleset_version == RULESET_VERSION
    assert evaluation.evaluation_status == EvaluationStatus.COMPLETE
    assert evaluation.source_bundle.collection_status.value == "PARTIAL"
    assert evaluation.source_bundle.bundle_id == captured_bundle["bundle_id"]
    assert evaluation.source_bundle.cluster_profile == "local-ha"
    assert evaluation.policy.cluster_profile == "local-ha"
    assert evaluation.policy.policy_version == "local-ha.conditions.v1"
    assert {
        name: result.state for name, result in evaluation.conditions.items()
    } == {
        ConditionName.CORE_BACKLOG_PRESSURE: ConditionState.ABSENT,
        ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED: ConditionState.ABSENT,
        ConditionName.DB_DEGRADED: ConditionState.ABSENT,
        ConditionName.WORKER_REPLICA_UNAVAILABLE: ConditionState.ABSENT,
    }
    assert (
        evaluation.assessments[
            AssessmentName.NO_BACKLOG_PRESSURE_DETECTED
        ].state
        == ConditionState.PRESENT
    )


def test_optional_missing_worker_metrics_do_not_make_core_unknown(
    captured_bundle,
) -> None:
    result = _condition(
        evaluate_bundle(captured_bundle),
        ConditionName.CORE_BACKLOG_PRESSURE,
    )

    processed = _dependency(
        result,
        "worker_terminal_processing",
        DependencyRequirement.OPTIONAL,
    )
    db_stage = _dependency(
        result,
        "worker_db_persist_stage_samples",
        DependencyRequirement.OPTIONAL,
    )
    assert result.state == ConditionState.ABSENT
    assert processed.missing is True
    assert db_stage.missing is True
    assert processed.missing_evidence_ids
    assert db_stage.missing_evidence_ids
    assert "worker_terminal_processing" not in result.missing_required_dependencies
    assert "worker_db_persist_stage_samples" not in result.missing_required_dependencies


def test_evaluation_is_deterministic_for_the_same_frozen_bundle(
    captured_bundle,
) -> None:
    first = evaluate_bundle(captured_bundle)
    second = evaluate_bundle(deepcopy(captured_bundle))

    assert first == second
    assert first.evaluation_id == second.evaluation_id


def test_freshness_age_cannot_understate_source_age(captured_bundle) -> None:
    captured_bundle["collection"]["completed_at"] = "2036-08-12T00:39:09Z"
    captured_bundle["collection"]["started_at"] = "2036-08-12T00:39:07Z"

    with pytest.raises(ValidationError, match="must not understate source age"):
        evaluate_bundle(captured_bundle)


def test_fresh_status_requires_a_coherent_age_contract(captured_bundle) -> None:
    lag = _evidence(captured_bundle, "kafka_consumergroup_lag")[0]
    lag["freshness"]["age_seconds"] = 999_999

    with pytest.raises(ValidationError, match="must not exceed max age"):
        evaluate_bundle(captured_bundle)


def test_shifted_kafka_range_grid_cannot_prove_absence(captured_bundle) -> None:
    for metric_name in (
        "kafka_topic_partition_current_offset",
        "kafka_consumergroup_current_offset",
        "kafka_consumergroup_lag",
    ):
        for item in _evidence(captured_bundle, metric_name):
            for sample in item["metric"]["value"]:
                sample["timestamp"] -= 86_400

    evaluation = evaluate_bundle(captured_bundle)
    core = evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
    concentration = evaluation.conditions[
        ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED
    ]
    assert core.state == ConditionState.UNKNOWN
    assert concentration.state == ConditionState.UNKNOWN
    assert "REQUIRED_RANGE_END_COLLECTION_TIME_MISMATCH" in core.reason_codes
    assert evaluation.evaluation_status == EvaluationStatus.PARTIAL


def test_missing_partition_makes_both_lag_conditions_unknown(
    captured_bundle,
) -> None:
    lag_items = _evidence(captured_bundle, "kafka_consumergroup_lag")
    removed_id = lag_items[-1]["evidence_id"]
    captured_bundle["evidence"] = [
        item
        for item in captured_bundle["evidence"]
        if item["evidence_id"] != removed_id
    ]

    evaluation = evaluate_bundle(captured_bundle)
    core = _condition(evaluation, ConditionName.CORE_BACKLOG_PRESSURE)
    concentration = _condition(
        evaluation,
        ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED,
    )
    assert core.state == ConditionState.UNKNOWN
    assert concentration.state == ConditionState.UNKNOWN
    assert evaluation.evaluation_status == EvaluationStatus.PARTIAL
    assert any(
        issue.code == "REQUIRED_PARTITION_COVERAGE_MISMATCH"
        for issue in _dependency(
            core,
            "consumer_partition_lag",
            DependencyRequirement.REQUIRED,
        ).issues
    )


def test_committed_minus_one_makes_core_unknown_with_exact_trace(
    captured_bundle,
) -> None:
    committed = _evidence(
        captured_bundle,
        "kafka_consumergroup_current_offset",
    )[0]
    _set_latest_value(committed, "-1")

    evaluation = evaluate_bundle(captured_bundle)
    core = _condition(evaluation, ConditionName.CORE_BACKLOG_PRESSURE)
    committed_trace = _dependency(
        core,
        "consumer_committed_offsets",
        DependencyRequirement.REQUIRED,
    )
    assert core.state == ConditionState.UNKNOWN
    assert "COMMITTED_OFFSET_UNINITIALIZED" in core.reason_codes
    assert any(
        issue.code == "COMMITTED_OFFSET_UNINITIALIZED"
        and issue.evidence_id == committed["evidence_id"]
        for issue in committed_trace.issues
    )


def test_inconsistent_end_committed_lag_arithmetic_makes_core_unknown(
    captured_bundle,
) -> None:
    lag = _evidence(captured_bundle, "kafka_consumergroup_lag")[0]
    _set_latest_value(lag, "1")

    evaluation = evaluate_bundle(captured_bundle)
    core = _condition(evaluation, ConditionName.CORE_BACKLOG_PRESSURE)
    lag_trace = _dependency(
        core,
        "consumer_partition_lag",
        DependencyRequirement.REQUIRED,
    )
    assert core.state == ConditionState.UNKNOWN
    assert evaluation.evaluation_status == EvaluationStatus.PARTIAL
    assert "KAFKA_OFFSET_LAG_ARITHMETIC_MISMATCH" in core.reason_codes
    assert any(
        issue.code == "KAFKA_OFFSET_LAG_ARITHMETIC_MISMATCH"
        and issue.evidence_id == lag["evidence_id"]
        and "partition=0" in (issue.detail or "")
        and "expected_lag=0" in (issue.detail or "")
        for issue in lag_trace.issues
    )


def test_required_stale_evidence_makes_only_dependent_conditions_unknown(
    captured_bundle,
) -> None:
    lag = _evidence(captured_bundle, "kafka_consumergroup_lag")[0]
    lag["freshness"]["status"] = "STALE"
    lag["freshness"]["age_seconds"] = 60

    evaluation = evaluate_bundle(captured_bundle)
    core = _condition(evaluation, ConditionName.CORE_BACKLOG_PRESSURE)
    concentration = _condition(
        evaluation,
        ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED,
    )
    assert core.state == ConditionState.UNKNOWN
    assert concentration.state == ConditionState.UNKNOWN
    assert lag["evidence_id"] in core.stale_required_evidence_ids
    assert (
        _condition(evaluation, ConditionName.DB_DEGRADED).state
        == ConditionState.ABSENT
    )
    assert (
        _condition(evaluation, ConditionName.WORKER_REPLICA_UNAVAILABLE).state
        == ConditionState.ABSENT
    )


def test_offset_decrease_is_a_semantic_unknown_for_core(captured_bundle) -> None:
    end_offset = _evidence(
        captured_bundle,
        "kafka_topic_partition_current_offset",
    )[0]
    end_offset["semantic"]["flags"].append("offset_decrease")

    core = _condition(
        evaluate_bundle(captured_bundle),
        ConditionName.CORE_BACKLOG_PRESSURE,
    )
    trace = _dependency(
        core,
        "topic_end_offsets",
        DependencyRequirement.REQUIRED,
    )
    assert core.state == ConditionState.UNKNOWN
    assert end_offset["evidence_id"] in trace.semantic_anomaly_evidence_ids
    assert "REQUIRED_SEMANTIC_ANOMALY" in core.reason_codes


@pytest.mark.parametrize(
    ("field_path", "value", "reason"),
    [
        (("source",), "kubernetes", "REQUIRED_SOURCE_SELECTOR_MISMATCH"),
        (
            ("semantic", "type"),
            "unrelated_metric",
            "REQUIRED_SEMANTIC_SELECTOR_MISMATCH",
        ),
        (("labels", "job"), "other-exporter", "REQUIRED_LABEL_SELECTOR_MISMATCH"),
        (
            ("freshness", "basis"),
            "untrusted_freshness",
            "REQUIRED_FRESHNESS_BASIS_MISMATCH",
        ),
    ],
)
def test_required_kafka_selector_mismatch_is_unknown(
    captured_bundle,
    field_path,
    value,
    reason,
) -> None:
    lag = _evidence(captured_bundle, "kafka_consumergroup_lag")[0]
    target = lag
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = value

    evaluation = evaluate_bundle(captured_bundle)
    assert (
        evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE].state
        == ConditionState.UNKNOWN
    )
    assert (
        evaluation.conditions[
            ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED
        ].state
        == ConditionState.UNKNOWN
    )
    assert reason in evaluation.conditions[
        ConditionName.CORE_BACKLOG_PRESSURE
    ].reason_codes


def test_one_sample_range_cannot_prove_backlog_absence(captured_bundle) -> None:
    for metric_name in (
        "kafka_topic_partition_current_offset",
        "kafka_consumergroup_current_offset",
        "kafka_consumergroup_lag",
    ):
        for item in _evidence(captured_bundle, metric_name):
            item["metric"]["value"] = item["metric"]["value"][-1:]
            item["metric"]["sample_count"] = 1

    evaluation = evaluate_bundle(captured_bundle)
    assert (
        evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE].state
        == ConditionState.UNKNOWN
    )
    assert (
        evaluation.assessments[
            AssessmentName.NO_BACKLOG_PRESSURE_DETECTED
        ].state
        == ConditionState.UNKNOWN
    )


def test_earlier_offset_lag_mismatch_is_unknown(captured_bundle) -> None:
    lag = _evidence(captured_bundle, "kafka_consumergroup_lag")[0]
    lag["metric"]["value"][2]["value"] = "1"

    core = evaluate_bundle(captured_bundle).conditions[
        ConditionName.CORE_BACKLOG_PRESSURE
    ]
    assert core.state == ConditionState.UNKNOWN
    assert "KAFKA_OFFSET_LAG_ARITHMETIC_MISMATCH" in core.reason_codes


def test_fractional_offset_is_unknown(captured_bundle) -> None:
    end_offset = _evidence(
        captured_bundle,
        "kafka_topic_partition_current_offset",
    )[0]
    end_offset["metric"]["value"][0]["value"] = "4590.5"

    core = evaluate_bundle(captured_bundle).conditions[
        ConditionName.CORE_BACKLOG_PRESSURE
    ]
    assert core.state == ConditionState.UNKNOWN
    assert "REQUIRED_RECORD_VALUE_NON_INTEGRAL" in core.reason_codes


def test_mixed_exporter_identity_clears_usable_evidence(captured_bundle) -> None:
    lag = _evidence(captured_bundle, "kafka_consumergroup_lag")[0]
    lag["labels"]["instance"] = "other-exporter:9308"

    concentration = evaluate_bundle(captured_bundle).conditions[
        ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED
    ]
    trace = concentration.required_evidence[0]
    assert concentration.state == ConditionState.UNKNOWN
    assert "REQUIRED_EXPORTER_IDENTITY_MIXED" in concentration.reason_codes
    assert trace.usable_evidence_ids == []


def test_mixed_prometheus_raw_source_cannot_prove_absence(captured_bundle) -> None:
    lag = _evidence(captured_bundle, "kafka_consumergroup_lag")[0]
    lag["raw_ref"] = "results/ops-agent/raw/other/prometheus.json"
    lag["raw_sha256"] = "a" * 64

    evaluation = evaluate_bundle(captured_bundle)
    assert (
        evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE].state
        == ConditionState.UNKNOWN
    )
    concentration = evaluation.conditions[
        ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED
    ]
    assert concentration.state == ConditionState.UNKNOWN
    assert "REQUIRED_RAW_SOURCE_MIXED" in concentration.reason_codes


def test_unbound_optional_selector_never_advertises_usable_evidence(
    captured_bundle,
) -> None:
    db = evaluate_bundle(captured_bundle).conditions[ConditionName.DB_DEGRADED]
    optional = _dependency(
        db,
        "event_persist_lag_samples",
        DependencyRequirement.OPTIONAL,
    )
    assert optional.evidence_ids
    assert optional.usable_evidence_ids == []
    assert any(issue.code == "OPTIONAL_SELECTOR_UNBOUND" for issue in optional.issues)


def test_positive_lag_is_unknown_until_pressure_policies_are_calibrated(
    captured_bundle,
) -> None:
    _set_consistent_partition_lag(captured_bundle, "0", 12)

    evaluation = evaluate_bundle(captured_bundle)
    core = _condition(evaluation, ConditionName.CORE_BACKLOG_PRESSURE)
    concentration = _condition(
        evaluation,
        ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED,
    )
    assert core.state == ConditionState.UNKNOWN
    assert core.facts["latest_total_lag_records"] == 12
    assert "PRESSURE_POLICY_UNCALIBRATED" in core.reason_codes
    assert concentration.state == ConditionState.UNKNOWN
    assert "CONCENTRATION_POLICY_UNCALIBRATED" in concentration.reason_codes
    assert concentration.facts["maximum_partition_share"] == 1.0
    assert (
        evaluation.assessments[
            AssessmentName.NO_BACKLOG_PRESSURE_DETECTED
        ].state
        == ConditionState.UNKNOWN
    )


@pytest.mark.parametrize(
    ("field", "value", "readiness_reason"),
    [
        ("primary_reachable", False, "postgres_primary_unreachable"),
        ("standby_count", 1, "postgres_ready_standbys_below_minimum"),
        ("sync_standby_count", 0, "postgres_sync_standbys_below_minimum"),
        (
            "max_replication_delay_bytes",
            1_048_577,
            "postgres_replication_delay_high",
        ),
    ],
)
def test_explicit_application_db_readiness_reasons_are_present(
    captured_bundle,
    field,
    value,
    readiness_reason,
) -> None:
    _set_postgres_readiness_reason(
        captured_bundle,
        reason=readiness_reason,
        field=field,
        value=value,
    )

    result = _condition(
        evaluate_bundle(captured_bundle),
        ConditionName.DB_DEGRADED,
    )
    assert result.state == ConditionState.PRESENT
    assert f"APPLICATION_READINESS_{readiness_reason.upper()}" in result.reason_codes


def test_non_database_readiness_failure_does_not_mark_db_degraded(
    captured_bundle,
) -> None:
    readiness = _evidence(captured_bundle, "application_readiness_observation")[0]
    readiness["metric"]["value"].update(
        {"http_status": 503, "body_status": "not_ready"}
    )
    readiness["metric"]["value"]["body"].update(
        {"status": "not_ready", "reason": ["schema_not_ready"]}
    )

    result = evaluate_bundle(captured_bundle).conditions[
        ConditionName.DB_DEGRADED
    ]
    assert result.state == ConditionState.ABSENT
    assert result.reason_codes == ["NO_POSTGRES_DEGRADATION_REASON"]


@pytest.mark.parametrize(
    ("field", "value", "reason_code"),
    [
        ("ha_mode", False, "LOCAL_HA_MODE_CONTRACT_MISMATCH"),
        (
            "standby_count",
            0,
            "READINESS_POSTGRES_REASON_COMPONENT_CONFLICT",
        ),
        (
            "sync_standby_count",
            0,
            "READINESS_POSTGRES_REASON_COMPONENT_CONFLICT",
        ),
        (
            "max_replication_delay_bytes",
            999_999_999,
            "READINESS_POSTGRES_REASON_COMPONENT_CONFLICT",
        ),
    ],
)
def test_local_ha_db_contract_conflict_is_unknown(
    captured_bundle,
    field,
    value,
    reason_code,
) -> None:
    _set_postgres_field(captured_bundle, field, value)

    result = evaluate_bundle(captured_bundle).conditions[ConditionName.DB_DEGRADED]
    assert result.state == ConditionState.UNKNOWN
    assert reason_code in result.reason_codes


def test_unknown_postgres_readiness_reason_is_unknown(captured_bundle) -> None:
    readiness = _evidence(captured_bundle, "application_readiness_observation")[0]
    readiness["metric"]["value"].update(
        {"http_status": 200, "body_status": "degraded"}
    )
    readiness["metric"]["value"]["body"].update(
        {"status": "degraded", "reason": ["postgres_unversioned_reason"]}
    )

    result = evaluate_bundle(captured_bundle).conditions[ConditionName.DB_DEGRADED]
    assert result.state == ConditionState.UNKNOWN
    assert "READINESS_POSTGRES_REASON_UNRECOGNIZED" in result.reason_codes


def test_missing_db_field_is_unknown_and_traced(captured_bundle) -> None:
    postgres = _evidence(
        captured_bundle,
        "application_postgres_runtime_observation",
    )[0]
    readiness = _evidence(captured_bundle, "application_readiness_observation")[0]
    del postgres["metric"]["value"]["sync_standby_count"]
    del readiness["metric"]["value"]["body"]["postgres"]["sync_standby_count"]

    evaluation = evaluate_bundle(captured_bundle)
    result = _condition(evaluation, ConditionName.DB_DEGRADED)
    assert result.state == ConditionState.UNKNOWN
    assert evaluation.evaluation_status == EvaluationStatus.PARTIAL
    assert any(
        issue.code == "REQUIRED_FIELD_MISSING"
        and issue.detail == "sync_standby_count"
        for issue in result.required_evidence[1].issues
    )
    assert result.required_evidence[1].usable_evidence_ids == []


def test_worker_replica_shortfall_is_unknown_without_grace_history(
    captured_bundle,
) -> None:
    deployment = _evidence(
        captured_bundle,
        "kubernetes_worker_deployment_observation",
    )[0]
    deployment["metric"]["value"].update(
        {"desired_replicas": 2, "current_replicas": 1, "available_replicas": 1}
    )

    result = _condition(
        evaluate_bundle(captured_bundle),
        ConditionName.WORKER_REPLICA_UNAVAILABLE,
    )
    assert result.state == ConditionState.UNKNOWN
    assert result.reason_codes == [
        "WORKER_CURRENT_REPLICA_SHORTFALL_OBSERVED",
        "WORKER_AVAILABLE_REPLICA_SHORTFALL_OBSERVED",
        "UNAVAILABLE_GRACE_NOT_PROVEN_BY_SINGLE_SNAPSHOT",
    ]


def test_unobserved_deployment_generation_is_unknown(captured_bundle) -> None:
    deployment = _evidence(
        captured_bundle,
        "kubernetes_worker_deployment_observation",
    )[0]
    deployment["metric"]["value"]["observed_generation"] -= 1

    result = evaluate_bundle(captured_bundle).conditions[
        ConditionName.WORKER_REPLICA_UNAVAILABLE
    ]
    assert result.state == ConditionState.UNKNOWN
    assert "DEPLOYMENT_GENERATION_NOT_OBSERVED" in result.reason_codes


def test_incoherent_deployment_replica_counts_are_unknown(captured_bundle) -> None:
    deployment = _evidence(
        captured_bundle,
        "kubernetes_worker_deployment_observation",
    )[0]
    deployment["metric"]["value"].update(
        {"current_replicas": 2, "available_replicas": 3}
    )

    result = evaluate_bundle(captured_bundle).conditions[
        ConditionName.WORKER_REPLICA_UNAVAILABLE
    ]
    assert result.state == ConditionState.UNKNOWN
    assert "DEPLOYMENT_REPLICA_COUNTS_INCOHERENT" in result.reason_codes


@pytest.mark.parametrize(
    ("field", "value", "reason_code"),
    [
        ("name", "other-worker", "DEPLOYMENT_NAME_SCOPE_MISMATCH"),
        ("namespace", "other-namespace", "DEPLOYMENT_NAMESPACE_SCOPE_MISMATCH"),
    ],
)
def test_worker_metadata_must_match_local_ha_scope(
    captured_bundle,
    field,
    value,
    reason_code,
) -> None:
    deployment = _evidence(
        captured_bundle,
        "kubernetes_worker_deployment_observation",
    )[0]
    deployment["metric"]["value"]["metadata"][field] = value

    result = evaluate_bundle(captured_bundle).conditions[
        ConditionName.WORKER_REPLICA_UNAVAILABLE
    ]
    assert result.state == ConditionState.UNKNOWN
    assert reason_code in result.reason_codes


def test_dependency_registry_separates_required_and_optional() -> None:
    core = CONDITION_DEPENDENCIES[ConditionName.CORE_BACKLOG_PRESSURE]
    assert [
        dependency.name
        for dependency in core[DependencyRequirement.REQUIRED]
    ] == [
        "topic_end_offsets",
        "consumer_committed_offsets",
        "consumer_partition_lag",
    ]
    assert "worker_terminal_processing" in {
        dependency.name
        for dependency in core[DependencyRequirement.OPTIONAL]
    }
    assert "worker_db_persist_stage_samples" in {
        dependency.name
        for dependency in core[DependencyRequirement.OPTIONAL]
    }


def test_input_schema_and_policy_are_strict(captured_bundle) -> None:
    wrong_schema = deepcopy(captured_bundle)
    wrong_schema["schema_version"] = "ops.evidence.v2"
    with pytest.raises(ValidationError):
        evaluate_bundle(wrong_schema)

    with pytest.raises(ValidationError):
        EvaluationPolicy(kafka_range_window_seconds=120)

    with pytest.raises(ValidationError):
        EvaluationPolicy(partition_lag_concentration_share_threshold=0.8)


def test_policy_is_bound_to_bundle_cluster_profile(captured_bundle) -> None:
    captured_bundle["cluster_profile"] = "demo-lite"

    with pytest.raises(ValueError, match="cluster profile mismatch"):
        evaluate_bundle(captured_bundle)


def test_policy_is_bound_to_source_scope_and_collection_policy(
    captured_bundle,
) -> None:
    captured_bundle["scope"]["consumer_group"] = "other-group"
    with pytest.raises(ValueError, match="scope mismatch"):
        evaluate_bundle(captured_bundle)


def test_ruleset_version_participates_in_evaluation_id(
    captured_bundle,
    monkeypatch,
) -> None:
    evaluation = evaluate_bundle(captured_bundle)
    source = evaluator_module._validate_bundle(captured_bundle)
    original = evaluator_module._evaluation_id(
        source,
        evaluation.policy,
        evaluation.conditions,
        evaluation.assessments,
    )
    monkeypatch.setattr(
        evaluator_module,
        "RULESET_VERSION",
        "ops.conditions.rules.test",
    )
    changed = evaluator_module._evaluation_id(
        source,
        evaluation.policy,
        evaluation.conditions,
        evaluation.assessments,
    )
    assert changed != original


def test_evaluation_binds_canonical_source_bundle_digest(captured_bundle) -> None:
    evaluation = evaluate_bundle(captured_bundle)

    assert evaluation.source_bundle.source_bundle_sha256 == canonical_sha256(
        captured_bundle
    )


def test_evaluation_id_detects_nested_payload_mutation(captured_bundle) -> None:
    evaluation = evaluate_bundle(captured_bundle)
    evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE].facts[
        "latest_total_lag_records"
    ] = 999

    with pytest.raises(ValueError, match="evaluation_id does not match"):
        evaluation.model_dump(mode="json")


def test_evidence_identifier_length_is_bounded(captured_bundle) -> None:
    captured_bundle["evidence"][0]["evidence_id"] = "x" * 257

    with pytest.raises(ValidationError, match="at most 256 characters"):
        evaluate_bundle(captured_bundle)


def test_output_contract_requires_exact_result_sets(captured_bundle) -> None:
    payload = evaluate_bundle(captured_bundle).model_dump(mode="json")
    del payload["conditions"][ConditionName.DB_DEGRADED.value]

    with pytest.raises(ValidationError, match="exact condition set"):
        type(evaluate_bundle(captured_bundle)).model_validate(payload)


def test_output_contract_requires_status_to_match_unknown_results(
    captured_bundle,
) -> None:
    evaluation = evaluate_bundle(captured_bundle)
    payload = evaluation.model_dump(mode="json")
    payload["conditions"][ConditionName.DB_DEGRADED.value]["state"] = "UNKNOWN"

    with pytest.raises(ValidationError, match="PARTIAL exactly"):
        type(evaluation).model_validate(payload)


def test_output_contract_requires_assessment_dependency_agreement(
    captured_bundle,
) -> None:
    evaluation = evaluate_bundle(captured_bundle)
    payload = evaluation.model_dump(mode="json")
    dependencies = payload["assessments"][
        AssessmentName.NO_BACKLOG_PRESSURE_DETECTED.value
    ]["condition_dependencies"]
    dependencies[0]["state"] = "PRESENT"

    with pytest.raises(ValidationError, match="dependency state"):
        type(evaluation).model_validate(payload)


def test_output_contract_requires_deterministic_assessment_state(
    captured_bundle,
) -> None:
    evaluation = evaluate_bundle(captured_bundle)
    payload = evaluation.model_dump(mode="json")
    payload["assessments"][
        AssessmentName.NO_BACKLOG_PRESSURE_DETECTED.value
    ]["state"] = "ABSENT"

    with pytest.raises(ValidationError, match="deterministic condition logic"):
        type(evaluation).model_validate(payload)
