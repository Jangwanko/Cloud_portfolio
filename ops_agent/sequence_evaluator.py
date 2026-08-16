"""Sequence-aware deterministic evaluation for calibrated backlog activation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from ops_agent.calibration import summarize_bundle
from ops_agent.endpoint_provenance import endpoint_provenance
from ops_agent.evaluation_models import (
    AssessmentName,
    ConditionName,
    ConditionResult,
    ConditionState,
    EvaluationStatus,
    canonical_sha256,
)
from ops_agent.evaluator import _no_backlog_assessment, evaluate_bundle
from ops_agent.models import EvidenceBundle, EvidenceItem, EvidenceStatus, FreshnessStatus
from ops_agent.sequence_models import (
    SequenceCaptureObservation,
    SequenceConditionEvaluation,
    SequenceEvaluationPolicy,
    SequenceSourceBundleReference,
    sequence_evaluation_id,
)


EVALUATOR_VERSION = "ops.evaluator.v2"
RULESET_VERSION = "ops.conditions.rules.v2"
_KAFKA_METRICS = (
    "kafka_topic_partition_current_offset",
    "kafka_consumergroup_current_offset",
    "kafka_consumergroup_lag",
)


@dataclass(frozen=True)
class _Capture:
    bundle: EvidenceBundle
    source_reference: SequenceSourceBundleReference
    observation: SequenceCaptureObservation
    single_evaluation: Any | None


def _validate_policy(
    policy: SequenceEvaluationPolicy | Mapping[str, Any] | None,
) -> SequenceEvaluationPolicy:
    if policy is None:
        return SequenceEvaluationPolicy()
    if isinstance(policy, SequenceEvaluationPolicy):
        return policy
    return SequenceEvaluationPolicy.model_validate(policy)


def _validate_bundles(
    bundles: Sequence[EvidenceBundle | Mapping[str, Any] | str | bytes],
) -> list[EvidenceBundle]:
    if not bundles:
        raise ValueError("sequence evaluation requires at least one evidence bundle")
    if len(bundles) > 256:
        raise ValueError("sequence evaluation accepts at most 256 evidence bundles")
    result: list[EvidenceBundle] = []
    for bundle in bundles:
        if isinstance(bundle, EvidenceBundle):
            result.append(bundle)
        elif isinstance(bundle, (str, bytes)):
            result.append(EvidenceBundle.model_validate_json(bundle))
        else:
            result.append(EvidenceBundle.model_validate(bundle))
    return result


def _scope_issue_codes(
    bundle: EvidenceBundle,
    policy: SequenceEvaluationPolicy,
) -> list[str]:
    checks = (
        ("SEQUENCE_CLUSTER_PROFILE_MISMATCH", bundle.cluster_profile, policy.cluster_profile),
        ("SEQUENCE_CONTEXT_MISMATCH", bundle.scope.context, policy.context),
        ("SEQUENCE_NAMESPACE_MISMATCH", bundle.scope.namespace, policy.namespace),
        ("SEQUENCE_TOPIC_MISMATCH", bundle.scope.topic, policy.topic),
        (
            "SEQUENCE_CONSUMER_GROUP_MISMATCH",
            bundle.scope.consumer_group,
            policy.consumer_group,
        ),
        (
            "SEQUENCE_SOURCE_POLICY_MISMATCH",
            bundle.context.policy_version,
            policy.source_policy_version,
        ),
    )
    return [code for code, actual, expected in checks if actual != expected]


def _items(bundle: EvidenceBundle, metric_name: str) -> list[EvidenceItem]:
    return [item for item in bundle.evidence if item.metric.name == metric_name]


def _values(item: EvidenceItem) -> tuple[Decimal, ...]:
    assert isinstance(item.metric.value, list)
    return tuple(Decimal(str(sample["value"])) for sample in item.metric.value)


def _source_identity(
    bundle: EvidenceBundle,
) -> tuple[str | None, list[str], tuple[str, ...], Any | None]:
    issues: list[str] = []
    endpoint_items = [
        item
        for item in _items(bundle, "source_endpoint_identity")
        if item.labels.get("collector_source") == "prometheus"
    ]
    endpoint_identity: str | None = None
    if len(endpoint_items) != 1:
        issues.append("SEQUENCE_PROMETHEUS_ENDPOINT_IDENTITY_MISSING")
    else:
        item = endpoint_items[0]
        value = item.metric.value
        if (
            item.status != EvidenceStatus.OK
            or item.freshness.status != FreshnessStatus.FRESH
            or item.source != "collector_configuration"
            or item.tool_id != "collector.endpoint.identity.v1"
            or item.semantic.type != "effective_source_endpoint_identity"
            or not isinstance(value, Mapping)
        ):
            issues.append("SEQUENCE_PROMETHEUS_ENDPOINT_IDENTITY_UNUSABLE")
        else:
            try:
                expected = endpoint_provenance(
                    base_url=str(value["base_url"]),
                    host_header=(
                        str(value["host_header"])
                        if value.get("host_header") is not None
                        else None
                    ),
                    configuration_source=str(value["configuration_source"]),
                )
                if (
                    value.get("identity_version") != expected["identity_version"]
                    or value.get("identity_sha256") != expected["identity_sha256"]
                ):
                    issues.append("SEQUENCE_PROMETHEUS_ENDPOINT_IDENTITY_INVALID")
                else:
                    endpoint_identity = expected["identity_sha256"]
            except (KeyError, TypeError, ValueError):
                issues.append("SEQUENCE_PROMETHEUS_ENDPOINT_IDENTITY_INVALID")

    kafka_items = [
        item
        for metric_name in _KAFKA_METRICS
        for item in _items(bundle, metric_name)
        if item.status == EvidenceStatus.OK
    ]
    exporter_identities = {
        (item.labels.get("job"), item.labels.get("instance"))
        for item in kafka_items
    }
    if len(exporter_identities) != 1:
        issues.append("SEQUENCE_KAFKA_EXPORTER_IDENTITY_UNUSABLE")
        exporter_identity: tuple[str | None, str | None] | None = None
    else:
        exporter_identity = next(iter(exporter_identities))

    partition_set = tuple(
        sorted(
            {
                item.labels["partition"]
                for item in kafka_items
                if "partition" in item.labels
            },
            key=int,
        )
    )
    source_timestamps = {
        item.source_timestamp
        for item in kafka_items
        if item.source_timestamp is not None
    }
    kafka_source_timestamp = (
        next(iter(source_timestamps)) if len(source_timestamps) == 1 else None
    )
    if len(source_timestamps) != 1:
        issues.append("SEQUENCE_KAFKA_SOURCE_TIMESTAMP_UNUSABLE")

    if issues or endpoint_identity is None or exporter_identity is None:
        return None, sorted(set(issues)), partition_set, kafka_source_timestamp
    identity = canonical_sha256(
        {
            "cluster_profile": bundle.cluster_profile,
            "context": bundle.scope.context,
            "namespace": bundle.scope.namespace,
            "topic": bundle.scope.topic,
            "consumer_group": bundle.scope.consumer_group,
            "source_sha": bundle.context.source_sha,
            "collector_tree_sha256": bundle.context.collector_tree_sha256,
            "collector_version": bundle.context.collector_version,
            "tool_registry_version": bundle.context.tool_registry_version,
            "source_policy_version": bundle.context.policy_version,
            "prometheus_endpoint_identity_sha256": endpoint_identity,
            "kafka_exporter_identity": exporter_identity,
            "partition_set": partition_set,
        }
    )
    return identity, [], partition_set, kafka_source_timestamp


def _measurement(
    bundle: EvidenceBundle,
) -> tuple[dict[str, Any] | None, list[str]]:
    issues: list[str] = []
    families: dict[str, dict[str, EvidenceItem]] = {}
    for metric_name in _KAFKA_METRICS:
        by_partition: dict[str, EvidenceItem] = {}
        for item in _items(bundle, metric_name):
            partition = item.labels.get("partition")
            if item.status == EvidenceStatus.OK and partition is not None:
                by_partition[partition] = item
        families[metric_name] = by_partition
    partition_sets = [set(items) for items in families.values()]
    if not partition_sets or not all(partition_sets):
        return None, ["SEQUENCE_KAFKA_MEASUREMENT_MISSING"]
    if not all(value == partition_sets[0] for value in partition_sets[1:]):
        return None, ["SEQUENCE_KAFKA_PARTITION_SET_MISMATCH"]

    try:
        end_delta = Decimal(0)
        committed_delta = Decimal(0)
        lag_delta = Decimal(0)
        latest_lag = Decimal(0)
        window: Decimal | None = None
        for partition in sorted(partition_sets[0], key=int):
            end_item = families[_KAFKA_METRICS[0]][partition]
            committed_item = families[_KAFKA_METRICS[1]][partition]
            lag_item = families[_KAFKA_METRICS[2]][partition]
            end_values = _values(end_item)
            committed_values = _values(committed_item)
            lag_values = _values(lag_item)
            timestamps = tuple(
                Decimal(str(sample["timestamp"]))
                for sample in end_item.metric.value
            )
            observed_window = timestamps[-1] - timestamps[0]
            window = observed_window if window is None else window
            if observed_window != window:
                issues.append("SEQUENCE_KAFKA_WINDOW_MISMATCH")
            end_delta += end_values[-1] - end_values[0]
            committed_delta += committed_values[-1] - committed_values[0]
            lag_delta += lag_values[-1] - lag_values[0]
            latest_lag += lag_values[-1]
    except (AssertionError, InvalidOperation, KeyError, TypeError, ValueError):
        return None, ["SEQUENCE_KAFKA_MEASUREMENT_INVALID"]
    if issues or window is None or window <= 0:
        return None, sorted(set(issues or ["SEQUENCE_KAFKA_WINDOW_INVALID"]))

    produce_rate = end_delta / window
    committed_rate = committed_delta / window
    lag_slope = lag_delta / window
    # Compare integral offset deltas before division. Decimal division by 60 can
    # produce recurring representations that differ only at the final digit.
    arithmetic_consistent = end_delta - committed_delta == lag_delta
    if not arithmetic_consistent:
        issues.append("SEQUENCE_RATE_LAG_ARITHMETIC_MISMATCH")
    return {
        "partition_set": tuple(sorted(partition_sets[0], key=int)),
        "total_lag": latest_lag,
        "lag_slope": lag_slope,
        "produce_rate": produce_rate,
        "committed_rate": committed_rate,
        "rate_arithmetic_consistent": arithmetic_consistent,
    }, issues


def _capture(
    bundle: EvidenceBundle,
    index: int,
    policy: SequenceEvaluationPolicy,
) -> _Capture:
    digest = canonical_sha256(bundle.model_dump(mode="json"))
    scope_issues = _scope_issue_codes(bundle, policy)
    source_identity, identity_issues, partition_set, source_timestamp = (
        _source_identity(bundle)
    )
    single_evaluation = None
    core_state = None
    core_reasons: list[str] = []
    core_usable = False
    measurement: dict[str, Any] | None = None
    measurement_issues: list[str] = []
    if not scope_issues:
        single_evaluation = evaluate_bundle(
            bundle,
            policy=policy.single_bundle_policy(),
        )
        core = single_evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
        core_state = core.state
        core_reasons = list(core.reason_codes)
        core_usable = not any(
            trace.issues for trace in core.required_evidence
        )
        if core_usable:
            measurement, measurement_issues = _measurement(bundle)

    summary = summarize_bundle(bundle)
    reasons = sorted(
        set(
            [
                *scope_issues,
                *identity_issues,
                *measurement_issues,
                *([] if core_usable else core_reasons),
            ]
        )
    )
    required_usable = bool(
        core_usable
        and measurement is not None
        and not scope_issues
        and not identity_issues
        and not measurement_issues
    )
    total_lag = int(measurement["total_lag"]) if measurement is not None else None
    lag_slope = (
        float(measurement["lag_slope"]) if measurement is not None else None
    )
    observation = SequenceCaptureObservation(
        sequence_index=index,
        bundle_id=bundle.bundle_id,
        source_bundle_sha256=digest,
        required_evidence_usable=required_usable,
        core_single_bundle_state=core_state,
        reason_codes=reasons,
        source_identity_sha256=source_identity,
        kafka_source_timestamp=source_timestamp,
        partition_set=list(partition_set),
        total_lag_records=total_lag,
        lag_slope_60s_records_per_second=lag_slope,
        produce_rate_60s_records_per_second=(
            float(measurement["produce_rate"]) if measurement is not None else None
        ),
        committed_offset_rate_60s_records_per_second=(
            float(measurement["committed_rate"])
            if measurement is not None
            else None
        ),
        rate_arithmetic_consistent=(
            measurement["rate_arithmetic_consistent"]
            if measurement is not None
            else None
        ),
        meets_lag_floor=(
            total_lag >= policy.activation_total_lag_floor_records
            if total_lag is not None
            else None
        ),
        meets_slope_floor=(
            lag_slope
            >= policy.activation_lag_slope_floor_records_per_second
            if lag_slope is not None
            else None
        ),
        worker_context=summary["worker"],
        keda_context=summary["keda"],
        worker_stage_latency_context=summary[
            "worker_db_persist_stage_latency"
        ],
    )
    reference = SequenceSourceBundleReference(
        sequence_index=index,
        schema_version=bundle.schema_version,
        bundle_id=bundle.bundle_id,
        incident_id=bundle.incident_id,
        cluster_profile=bundle.cluster_profile,
        collection_status=bundle.collection.status,
        collection_started_at=bundle.collection.started_at,
        collection_completed_at=bundle.collection.completed_at,
        kafka_source_timestamp=source_timestamp,
        source_bundle_sha256=digest,
    )
    return _Capture(
        bundle=bundle,
        source_reference=reference,
        observation=observation,
        single_evaluation=single_evaluation,
    )


def _core_sequence_result(
    captures: list[_Capture],
    policy: SequenceEvaluationPolicy,
    base: ConditionResult,
) -> ConditionResult:
    observations = [capture.observation for capture in captures]
    problems: list[str] = []
    if len(observations) < policy.activation_consecutive_capture_count:
        problems.append("SEQUENCE_CAPTURE_COUNT_INSUFFICIENT")
    if any(not item.required_evidence_usable for item in observations):
        problems.append("SEQUENCE_REQUIRED_EVIDENCE_UNUSABLE")
    if any(
        current.source_reference.collection_started_at
        <= previous.source_reference.collection_started_at
        or current.source_reference.collection_completed_at
        <= previous.source_reference.collection_completed_at
        for previous, current in zip(captures, captures[1:])
    ):
        problems.append("SEQUENCE_CAPTURE_TIMESTAMPS_NOT_STRICTLY_INCREASING")
    kafka_timestamps = [item.kafka_source_timestamp for item in observations]
    if any(timestamp is None for timestamp in kafka_timestamps) or any(
        current <= previous
        for previous, current in zip(kafka_timestamps, kafka_timestamps[1:])
        if previous is not None and current is not None
    ):
        problems.append("SEQUENCE_KAFKA_SOURCE_TIMESTAMPS_NOT_STRICTLY_INCREASING")
    identities = {item.source_identity_sha256 for item in observations}
    if None in identities or len(identities) != 1:
        problems.append("SEQUENCE_SOURCE_IDENTITY_MISMATCH")
    partition_sets = {tuple(item.partition_set) for item in observations}
    if len(partition_sets) != 1:
        problems.append("SEQUENCE_PARTITION_SET_MISMATCH")
    if any(item.rate_arithmetic_consistent is not True for item in observations):
        problems.append("SEQUENCE_RATE_LAG_ARITHMETIC_MISMATCH")

    matched_windows: list[list[int]] = []
    required_count = policy.activation_consecutive_capture_count
    if not problems:
        for start in range(len(observations) - required_count + 1):
            window = observations[start : start + required_count]
            qualifies = all(
                item.meets_lag_floor is True
                and item.meets_slope_floor is True
                for item in window
            )
            increasing = all(
                current.total_lag_records > previous.total_lag_records
                for previous, current in zip(window, window[1:])
                if previous.total_lag_records is not None
                and current.total_lag_records is not None
            )
            if qualifies and increasing:
                matched_windows.append(list(range(start, start + required_count)))

    facts = {
        "activation_policy": {
            "consecutive_capture_count": policy.activation_consecutive_capture_count,
            "total_lag_floor_records": policy.activation_total_lag_floor_records,
            "lag_slope_floor_records_per_second": (
                policy.activation_lag_slope_floor_records_per_second
            ),
            "require_increase_across_both_transitions": True,
            "produce_minus_committed_role": "arithmetic_consistency_only",
            "keda_worker_stage_role": "optional_context_only",
            "recovery_or_clearing_evaluated": False,
        },
        "ordered_capture_bundle_ids": [item.bundle_id for item in observations],
        "ordered_capture_digests": [
            item.source_bundle_sha256 for item in observations
        ],
        "capture_measurements": [
            {
                "sequence_index": item.sequence_index,
                "total_lag_records": item.total_lag_records,
                "lag_slope_60s_records_per_second": (
                    item.lag_slope_60s_records_per_second
                ),
                "produce_rate_60s_records_per_second": (
                    item.produce_rate_60s_records_per_second
                ),
                "committed_offset_rate_60s_records_per_second": (
                    item.committed_offset_rate_60s_records_per_second
                ),
                "rate_arithmetic_consistent": item.rate_arithmetic_consistent,
                "meets_lag_floor": item.meets_lag_floor,
                "meets_slope_floor": item.meets_slope_floor,
            }
            for item in observations
        ],
        "matched_activation_windows": matched_windows,
        "latest_single_bundle_facts": base.facts,
    }
    if problems:
        state = ConditionState.UNKNOWN
        reasons = sorted(set(problems))
    elif matched_windows:
        state = ConditionState.PRESENT
        reasons = [
            "CALIBRATED_THREE_CAPTURE_BACKLOG_ACTIVATION_OBSERVED",
            "LAG_RATE_ARITHMETIC_CONSISTENCY_CONFIRMED",
        ]
    elif base.state == ConditionState.ABSENT:
        state = ConditionState.ABSENT
        reasons = ["LATEST_FULL_WINDOW_BACKLOG_ZERO_WITHOUT_SEQUENCE_ACTIVATION"]
    else:
        state = ConditionState.UNKNOWN
        reasons = ["CALIBRATED_BACKLOG_ACTIVATION_SEQUENCE_NOT_OBSERVED"]

    evidence_ids = sorted(
        {
            evidence_id
            for capture in captures
            if capture.single_evaluation is not None
            for evidence_id in capture.single_evaluation.conditions[
                ConditionName.CORE_BACKLOG_PRESSURE
            ].evidence_ids
        }
    )
    return ConditionResult(
        condition=ConditionName.CORE_BACKLOG_PRESSURE,
        state=state,
        reason_codes=reasons,
        evidence_ids=evidence_ids,
        required_evidence=base.required_evidence,
        optional_evidence=base.optional_evidence,
        missing_required_dependencies=base.missing_required_dependencies,
        stale_required_evidence_ids=base.stale_required_evidence_ids,
        unknown_freshness_required_evidence_ids=(
            base.unknown_freshness_required_evidence_ids
        ),
        facts=facts,
    )


def evaluate_bundle_sequence(
    bundles: Sequence[EvidenceBundle | Mapping[str, Any] | str | bytes],
    *,
    policy: SequenceEvaluationPolicy | Mapping[str, Any] | None = None,
) -> SequenceConditionEvaluation:
    """Evaluate ordered immutable bundles without source access or wall-clock aging."""

    resolved_policy = _validate_policy(policy)
    sources = _validate_bundles(bundles)
    captures = [
        _capture(bundle, index, resolved_policy)
        for index, bundle in enumerate(sources)
    ]
    compatible = [
        capture for capture in captures if capture.single_evaluation is not None
    ]
    if not compatible:
        raise ValueError("sequence contains no bundle compatible with local-ha v2")
    latest = compatible[-1].single_evaluation
    assert latest is not None
    latest_core = latest.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
    core = _core_sequence_result(captures, resolved_policy, latest_core)
    conditions = dict(latest.conditions)
    conditions[ConditionName.CORE_BACKLOG_PRESSURE] = core
    assessments = {
        AssessmentName.NO_BACKLOG_PRESSURE_DETECTED: _no_backlog_assessment(
            conditions
        )
    }
    has_unknown = any(
        result.state == ConditionState.UNKNOWN
        for result in [*conditions.values(), *assessments.values()]
    )
    source_references = [capture.source_reference for capture in captures]
    observations = [capture.observation for capture in captures]
    evaluation_id = sequence_evaluation_id(
        evaluator_version=EVALUATOR_VERSION,
        ruleset_version=RULESET_VERSION,
        policy=resolved_policy,
        source_bundles=source_references,
        capture_observations=observations,
        conditions=conditions,
        assessments=assessments,
    )
    return SequenceConditionEvaluation(
        evaluator_version=EVALUATOR_VERSION,
        ruleset_version=RULESET_VERSION,
        evaluation_id=evaluation_id,
        evaluation_status=(
            EvaluationStatus.PARTIAL if has_unknown else EvaluationStatus.COMPLETE
        ),
        policy=resolved_policy,
        source_bundles=source_references,
        capture_observations=observations,
        conditions=conditions,
        assessments=assessments,
    )


__all__ = [
    "EVALUATOR_VERSION",
    "RULESET_VERSION",
    "SequenceEvaluationPolicy",
    "evaluate_bundle_sequence",
]
