"""Deterministic Worker backlog recovery evaluation over frozen evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from ops_agent.calibration import summarize_bundle
from ops_agent.endpoint_provenance import endpoint_provenance
from ops_agent.evaluation_models import (
    ConditionName,
    ConditionState,
    EvaluationPolicy,
    EvaluationStatus,
    canonical_sha256,
)
from ops_agent.evaluator import evaluate_bundle
from ops_agent.models import EvidenceBundle, EvidenceItem, EvidenceStatus, FreshnessStatus
from ops_agent.recovery_models import (
    LowLagEvidencePolicy,
    NegativeLagPoint,
    RecoveryActivationReference,
    RecoveryCaptureObservation,
    RecoveryCompletion,
    RecoveryCompletionStatus,
    RecoveryEvaluation,
    RecoveryEvaluationPolicy,
    RecoveryQuality,
    RecoverySourceBundleReference,
    RecoveryState,
    RecoveryWindow,
    recovery_evaluation_id,
)
from ops_agent.recovery_policies import load_recovery_policy
from ops_agent.sequence_models import SequenceConditionEvaluation


EVALUATOR_VERSION = "ops.recovery.evaluator.v1"
RULESET_VERSION = "ops.recovery.rules.v1"
EVALUATOR_VERSION_V2 = "ops.recovery.evaluator.v2"
RULESET_VERSION_V2 = "ops.recovery.rules.v2"
_KAFKA_METRICS = (
    "kafka_topic_partition_current_offset",
    "kafka_consumergroup_current_offset",
    "kafka_consumergroup_lag",
)
_POSTGRES_METRICS = (
    "application_readiness_observation",
    "application_postgres_runtime_observation",
)
_GLOBAL_INTEGRITY_ISSUES = {
    "ACTIVATION_INCIDENT_IDENTITY_MISMATCH",
    "ACTIVATION_POLICY_MISMATCH",
    "ACTIVATION_SOURCE_IDENTITY_UNUSABLE",
    "ACTIVATION_STATE_NOT_PRESENT",
    "RECOVERY_BUNDLE_DIGEST_MISMATCH",
    "RECOVERY_CAPTURE_NOT_POST_ACTIVATION",
    "RECOVERY_COLLECTION_TIMESTAMPS_REORDERED",
    "RECOVERY_CONDITION_SOURCE_IDENTITY_MISMATCH",
    "RECOVERY_INCIDENT_IDENTITY_MISMATCH",
    "RECOVERY_OFFSET_DECREASE_OR_RESET",
    "RECOVERY_SCOPE_MISMATCH",
    "RECOVERY_SOURCE_IDENTITY_MISMATCH",
}


def _validate_bundles(
    bundles: Sequence[EvidenceBundle | Mapping[str, Any] | str | bytes],
) -> list[EvidenceBundle]:
    if not bundles:
        raise ValueError("recovery evaluation requires post-activation bundles")
    if len(bundles) > 256:
        raise ValueError("recovery evaluation accepts at most 256 bundles")
    result: list[EvidenceBundle] = []
    for value in bundles:
        if isinstance(value, EvidenceBundle):
            result.append(value)
        elif isinstance(value, (str, bytes)):
            result.append(EvidenceBundle.model_validate_json(value))
        else:
            result.append(EvidenceBundle.model_validate(value))
    return result


def _validate_activation(
    value: SequenceConditionEvaluation | Mapping[str, Any] | str | bytes,
) -> SequenceConditionEvaluation:
    if isinstance(value, SequenceConditionEvaluation):
        value.verify_integrity()
        return value
    if isinstance(value, (str, bytes)):
        result = SequenceConditionEvaluation.model_validate_json(value)
    else:
        result = SequenceConditionEvaluation.model_validate(value)
    result.verify_integrity()
    return result


def _logical_incident_matches(source_incident_id: str, incident_id: str) -> bool:
    return source_incident_id == incident_id or source_incident_id.startswith(
        f"{incident_id}-"
    )


def _items(bundle: EvidenceBundle, metric_name: str) -> list[EvidenceItem]:
    return [item for item in bundle.evidence if item.metric.name == metric_name]


def _endpoint_identity(
    bundle: EvidenceBundle,
    collector_source: str,
) -> tuple[str | None, list[str]]:
    candidates = [
        item
        for item in _items(bundle, "source_endpoint_identity")
        if item.labels.get("collector_source") == collector_source
    ]
    if len(candidates) != 1:
        return None, [f"{collector_source.upper()}_ENDPOINT_IDENTITY_MISSING"]
    item = candidates[0]
    value = item.metric.value
    if (
        item.status != EvidenceStatus.OK
        or item.freshness.status != FreshnessStatus.FRESH
        or item.source != "collector_configuration"
        or item.tool_id != "collector.endpoint.identity.v1"
        or item.semantic.type != "effective_source_endpoint_identity"
        or not isinstance(value, Mapping)
    ):
        return None, [f"{collector_source.upper()}_ENDPOINT_IDENTITY_UNUSABLE"]
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
    except (KeyError, TypeError, ValueError):
        return None, [f"{collector_source.upper()}_ENDPOINT_IDENTITY_INVALID"]
    if (
        value.get("identity_version") != expected["identity_version"]
        or value.get("identity_sha256") != expected["identity_sha256"]
    ):
        return None, [f"{collector_source.upper()}_ENDPOINT_IDENTITY_INVALID"]
    return str(expected["identity_sha256"]), []


def _source_identity(
    bundle: EvidenceBundle,
) -> tuple[str | None, str | None, tuple[str, ...], Any | None, list[str]]:
    issues: list[str] = []
    prometheus_endpoint, endpoint_issues = _endpoint_identity(bundle, "prometheus")
    issues.extend(endpoint_issues)
    application_endpoint, application_issues = _endpoint_identity(bundle, "application")
    issues.extend(application_issues)
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
    exporter_identity = (
        next(iter(exporter_identities)) if len(exporter_identities) == 1 else None
    )
    if exporter_identity is None:
        issues.append("KAFKA_EXPORTER_IDENTITY_UNUSABLE")
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
    source_timestamp = (
        next(iter(source_timestamps)) if len(source_timestamps) == 1 else None
    )
    if len(source_timestamps) != 1:
        issues.append("KAFKA_SOURCE_TIMESTAMP_UNUSABLE")
    if issues or prometheus_endpoint is None or exporter_identity is None:
        condition_identity = None
    else:
        condition_identity = canonical_sha256(
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
                "prometheus_endpoint_identity_sha256": prometheus_endpoint,
                "kafka_exporter_identity": exporter_identity,
                "partition_set": partition_set,
            }
        )
    if condition_identity is None or application_endpoint is None:
        recovery_identity = None
    else:
        recovery_identity = canonical_sha256(
            {
                "condition_source_identity_sha256": condition_identity,
                "application_endpoint_identity_sha256": application_endpoint,
            }
        )
    return (
        condition_identity,
        recovery_identity,
        partition_set,
        source_timestamp,
        sorted(set(issues)),
    )


def _scope_issues(
    bundle: EvidenceBundle,
    policy: RecoveryEvaluationPolicy,
) -> list[str]:
    values = (
        bundle.cluster_profile == policy.profile,
        bundle.scope.context == policy.context,
        bundle.scope.namespace == policy.namespace,
        bundle.scope.topic == policy.topic,
        bundle.scope.consumer_group == policy.consumer_group,
        bundle.context.policy_version == policy.source_evidence_policy_version,
    )
    return [] if all(values) else ["RECOVERY_SCOPE_MISMATCH"]


def _postgres_observation(
    bundle: EvidenceBundle,
    policy: RecoveryEvaluationPolicy,
) -> tuple[bool | None, dict[str, Any], list[str], list[str]]:
    readiness = _items(bundle, _POSTGRES_METRICS[0])
    postgres = _items(bundle, _POSTGRES_METRICS[1])
    evidence_ids = [item.evidence_id for item in [*readiness, *postgres]]
    if len(readiness) != 1 or len(postgres) != 1:
        return None, {}, ["POSTGRES_READINESS_EVIDENCE_MISSING"], evidence_ids
    readiness_item = readiness[0]
    postgres_item = postgres[0]
    issues: list[str] = []
    for item, semantic in (
        (readiness_item, "application_readiness"),
        (postgres_item, "application_postgres_runtime_observation"),
    ):
        if item.status != EvidenceStatus.OK:
            issues.append("POSTGRES_READINESS_STATUS_UNUSABLE")
        if item.freshness.status != FreshnessStatus.FRESH:
            issues.append("POSTGRES_READINESS_NOT_FRESH")
        if (
            item.source != "application"
            or item.tool_id != "application.readiness.get.v1"
            or item.semantic.type != semantic
            or item.labels.get("endpoint") != "/health/ready"
        ):
            issues.append("POSTGRES_READINESS_SELECTOR_MISMATCH")
    if (
        readiness_item.source_timestamp != postgres_item.source_timestamp
        or readiness_item.collected_at != postgres_item.collected_at
        or readiness_item.raw_sha256 != postgres_item.raw_sha256
        or readiness_item.raw_sha256 is None
    ):
        issues.append("POSTGRES_READINESS_PROVENANCE_MISMATCH")
    readiness_value = readiness_item.metric.value
    postgres_value = postgres_item.metric.value
    if not isinstance(readiness_value, Mapping) or not isinstance(
        postgres_value, Mapping
    ):
        return None, {}, sorted(set([*issues, "POSTGRES_READINESS_VALUE_INVALID"])), evidence_ids
    readiness_body = readiness_value.get("body")
    if not isinstance(readiness_body, Mapping):
        issues.append("POSTGRES_READINESS_BODY_INVALID")
        readiness_body = {}
    readiness_reasons = readiness_body.get("reason")
    nested_postgres = readiness_body.get("postgres")
    if not isinstance(readiness_reasons, list):
        issues.append("POSTGRES_READINESS_REASON_INVALID")
        readiness_reasons = []
    if not isinstance(nested_postgres, Mapping):
        issues.append("POSTGRES_READINESS_NESTED_COMPONENT_INVALID")
        nested_postgres = {}
    postgres_fields = (
        "ha_mode",
        "primary_reachable",
        "standby_count",
        "sync_standby_count",
        "max_replication_delay_bytes",
    )
    if any(nested_postgres.get(field) != postgres_value.get(field) for field in postgres_fields):
        issues.append("POSTGRES_READINESS_COMPONENT_MISMATCH")
    context = {
        "http_status": readiness_value.get("http_status"),
        "readiness_body_status": readiness_value.get("body_status"),
        "readiness_reasons": list(readiness_reasons),
        "ha_mode": postgres_value.get("ha_mode"),
        "primary_reachable": postgres_value.get("primary_reachable"),
        "standby_count": postgres_value.get("standby_count"),
        "sync_standby_count": postgres_value.get("sync_standby_count"),
        "max_replication_delay_bytes": postgres_value.get(
            "max_replication_delay_bytes"
        ),
    }
    ready = bool(
        not issues
        and context["http_status"] == 200
        and context["readiness_body_status"]
        == policy.postgres_required_body_status
        and readiness_body.get("status") == policy.postgres_required_body_status
        and not context["readiness_reasons"]
        and context["ha_mode"] is policy.postgres_require_ha_mode
        and context["primary_reachable"]
        is policy.postgres_require_primary_reachable
    )
    if not ready:
        issues.append("POSTGRES_REQUIRED_READINESS_NOT_ACCEPTABLE")
    return ready, context, sorted(set(issues)), evidence_ids


def _negative_lag_points(bundle: EvidenceBundle) -> list[NegativeLagPoint]:
    families: dict[str, dict[str, EvidenceItem]] = {}
    for metric_name in _KAFKA_METRICS:
        families[metric_name] = {
            item.labels["partition"]: item
            for item in _items(bundle, metric_name)
            if item.status == EvidenceStatus.OK and "partition" in item.labels
        }
    result: list[NegativeLagPoint] = []
    for partition, lag_item in families[_KAFKA_METRICS[2]].items():
        samples = lag_item.metric.value
        if not isinstance(samples, list):
            continue
        end_item = families[_KAFKA_METRICS[0]].get(partition)
        committed_item = families[_KAFKA_METRICS[1]].get(partition)
        end_samples = end_item.metric.value if end_item is not None else None
        committed_samples = (
            committed_item.metric.value if committed_item is not None else None
        )
        for sample_index, sample in enumerate(samples):
            if not isinstance(sample, Mapping):
                continue
            try:
                value = Decimal(str(sample["value"]))
                timestamp = float(sample["timestamp"])
            except (InvalidOperation, KeyError, TypeError, ValueError):
                continue
            if not value.is_finite() or value >= 0 or value != value.to_integral_value():
                continue

            def offset_value(source: Any) -> int | None:
                if not isinstance(source, list) or sample_index >= len(source):
                    return None
                candidate = source[sample_index]
                if not isinstance(candidate, Mapping):
                    return None
                try:
                    parsed = Decimal(str(candidate["value"]))
                except (InvalidOperation, KeyError, TypeError, ValueError):
                    return None
                if not parsed.is_finite() or parsed != parsed.to_integral_value():
                    return None
                return int(parsed)

            result.append(
                NegativeLagPoint(
                    evidence_id=lag_item.evidence_id,
                    partition=partition,
                    sample_index=sample_index,
                    range_evaluation_timestamp=timestamp,
                    exporter_lag_records=int(value),
                    end_offset_records=offset_value(end_samples),
                    committed_offset_records=offset_value(committed_samples),
                )
            )
    return sorted(result, key=lambda item: (int(item.partition), item.sample_index))


def _kafka_observation(
    bundle: EvidenceBundle,
) -> tuple[dict[str, Any], list[str], list[str], list[NegativeLagPoint]]:
    try:
        single = evaluate_bundle(bundle, policy=EvaluationPolicy())
    except ValueError:
        issue_codes = ["KAFKA_EVALUATION_SCOPE_INCOMPATIBLE"]
        evidence_ids = sorted(
            item.evidence_id
            for metric_name in _KAFKA_METRICS
            for item in _items(bundle, metric_name)
        )
    else:
        core = single.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
        issue_codes = sorted(
            {
                issue.code
                for trace in core.required_evidence
                for issue in trace.issues
            }
        )
        evidence_ids = sorted(
            {
                evidence_id
                for trace in core.required_evidence
                for evidence_id in trace.evidence_ids
            }
        )
    negative_points = _negative_lag_points(bundle)
    if negative_points:
        issue_codes.append("NEGATIVE_EXPORTER_LAG_INVALID")
    try:
        try:
            summary = summarize_bundle(bundle)
        except (KeyError, TypeError, ValueError):
            summary = {
                "worker": {},
                "keda": {},
                "worker_db_persist_stage_latency": {},
            }
        kafka = dict(summary["kafka"])
    except (KeyError, TypeError, ValueError):
        kafka = {}
        issue_codes.append("KAFKA_MEASUREMENT_INVALID")
    anomalies = kafka.get("anomalies")
    if isinstance(anomalies, list):
        issue_codes.extend(f"KAFKA_{str(value).upper()}" for value in anomalies)
    produce = kafka.get("produce_rate_records_per_second")
    committed = kafka.get("committed_offset_rate_records_per_second")
    slope = kafka.get("lag_slope_records_per_second")
    arithmetic_consistent = None
    if all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in (produce, committed, slope)
    ):
        arithmetic_consistent = abs((float(produce) - float(committed)) - float(slope)) <= 1e-9
        if not arithmetic_consistent:
            issue_codes.append("RECOVERY_RATE_LAG_ARITHMETIC_MISMATCH")
    else:
        issue_codes.append("RECOVERY_RATE_MEASUREMENT_UNAVAILABLE")
    result = {
        "total_lag_records": kafka.get("total_lag"),
        "lag_slope_60s_records_per_second": slope,
        "produce_rate_60s_records_per_second": produce,
        "committed_offset_rate_60s_records_per_second": committed,
        "rate_arithmetic_consistent": arithmetic_consistent,
        "partition_offsets": {
            str(partition): {
                "end_offset": int(values["end_offset"]),
                "committed_offset": int(values["committed_offset"]),
            }
            for partition, values in (
                kafka.get("per_partition", {}).items()
                if isinstance(kafka.get("per_partition"), Mapping)
                else []
            )
            if isinstance(values, Mapping)
            and isinstance(values.get("end_offset"), int)
            and isinstance(values.get("committed_offset"), int)
        },
    }
    return result, sorted(set(issue_codes)), evidence_ids, negative_points


def _activation_reference(
    activation: SequenceConditionEvaluation,
) -> RecoveryActivationReference:
    core = activation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
    source_identities = {
        item.source_identity_sha256
        for item in activation.capture_observations
        if item.source_identity_sha256 is not None
    }
    return RecoveryActivationReference(
        schema_version=activation.schema_version,
        evaluation_id=activation.evaluation_id,
        evaluator_version=activation.evaluator_version,
        ruleset_version=activation.ruleset_version,
        policy_version=activation.policy.policy_version,
        condition_state=core.state,
        source_bundle_digests=[
            item.source_bundle_sha256 for item in activation.source_bundles
        ],
        last_collection_completed_at=activation.source_bundles[-1].collection_completed_at,
        last_kafka_source_timestamp=activation.source_bundles[-1].kafka_source_timestamp,
        source_identity_sha256=(
            next(iter(source_identities)) if len(source_identities) == 1 else None
        ),
    )


def _activation_issues(
    activation: SequenceConditionEvaluation,
    incident_id: str,
    policy: RecoveryEvaluationPolicy,
) -> list[str]:
    issues: list[str] = []
    core = activation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
    if core.state != ConditionState.PRESENT:
        issues.append("ACTIVATION_STATE_NOT_PRESENT")
    if (
        activation.schema_version != policy.activation_schema_version
        or activation.evaluator_version != policy.activation_evaluator_version
        or activation.ruleset_version != policy.activation_ruleset_version
        or activation.policy.policy_version != policy.activation_policy_version
        or activation.policy.cluster_profile != policy.profile
        or activation.policy.context != policy.context
        or activation.policy.namespace != policy.namespace
        or activation.policy.topic != policy.topic
        or activation.policy.consumer_group != policy.consumer_group
    ):
        issues.append("ACTIVATION_POLICY_MISMATCH")
    if not all(
        _logical_incident_matches(item.incident_id, incident_id)
        for item in activation.source_bundles
    ):
        issues.append("ACTIVATION_INCIDENT_IDENTITY_MISMATCH")
    source_identities = {
        item.source_identity_sha256
        for item in activation.capture_observations
        if item.source_identity_sha256 is not None
    }
    if len(source_identities) != 1:
        issues.append("ACTIVATION_SOURCE_IDENTITY_UNUSABLE")
    return sorted(set(issues))


def _draining(observation: Mapping[str, Any]) -> bool:
    slope = observation.get("lag_slope_60s_records_per_second")
    produce = observation.get("produce_rate_60s_records_per_second")
    committed = observation.get("committed_offset_rate_60s_records_per_second")
    return bool(
        isinstance(slope, (int, float))
        and not isinstance(slope, bool)
        and slope < 0
        and isinstance(produce, (int, float))
        and not isinstance(produce, bool)
        and isinstance(committed, (int, float))
        and not isinstance(committed, bool)
        and committed >= produce
        and observation.get("rate_arithmetic_consistent") is True
    )


def _within_recovered_envelope(
    observation: RecoveryCaptureObservation,
    policy: RecoveryEvaluationPolicy,
) -> bool:
    if policy.recovered_policy_status.value != "PROMOTED":
        return False
    lag = observation.total_lag_records
    slope = observation.lag_slope_60s_records_per_second
    produce = observation.produce_rate_60s_records_per_second
    committed = observation.committed_offset_rate_60s_records_per_second
    return bool(
        observation.usable
        and observation.postgres_ready is True
        and observation.rate_arithmetic_consistent is True
        and lag is not None
        and policy.recovered_total_lag_maximum is not None
        and lag <= policy.recovered_total_lag_maximum
        and slope is not None
        and policy.recovered_lag_slope_maximum is not None
        and slope <= policy.recovered_lag_slope_maximum
        and produce is not None
        and policy.recovered_actual_produce_rate_minimum is not None
        and policy.recovered_actual_produce_rate_maximum is not None
        and policy.recovered_actual_produce_rate_minimum
        <= produce
        <= policy.recovered_actual_produce_rate_maximum
        and committed is not None
        and committed >= produce
    )


def evaluate_recovery(
    *,
    incident_id: str,
    activation_evaluation: SequenceConditionEvaluation | Mapping[str, Any] | str | bytes,
    bundles: Sequence[EvidenceBundle | Mapping[str, Any] | str | bytes],
    source_bundle_digests: Sequence[str],
    policy: RecoveryEvaluationPolicy | Mapping[str, Any] | None = None,
) -> RecoveryEvaluation:
    """Evaluate ACTIVE/RECOVERING/UNKNOWN without runtime source access."""

    if not incident_id or len(incident_id) > 256:
        raise ValueError("incident_id must contain 1 to 256 characters")
    activation = _validate_activation(activation_evaluation)
    sources = _validate_bundles(bundles)
    if len(source_bundle_digests) != len(sources):
        raise ValueError("one expected source bundle digest is required per bundle")
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in source_bundle_digests
    ):
        raise ValueError("source bundle digests must be lowercase SHA-256 values")
    resolved_policy = (
        load_recovery_policy()
        if policy is None
        else (
            policy
            if isinstance(policy, RecoveryEvaluationPolicy)
            else RecoveryEvaluationPolicy.model_validate(policy)
        )
    )
    activation_reference = _activation_reference(activation)
    activation_problems = _activation_issues(
        activation,
        incident_id,
        resolved_policy,
    )

    references: list[RecoverySourceBundleReference] = []
    raw_observations: list[dict[str, Any]] = []
    previous_collection = activation_reference.last_collection_completed_at
    previous_kafka_source = activation_reference.last_kafka_source_timestamp
    expected_recovery_identity: str | None = None
    stream_integrity_broken = bool(activation_problems)
    previous_partition_offsets: dict[str, dict[str, int]] | None = None

    for index, (bundle, expected_digest) in enumerate(
        zip(sources, source_bundle_digests)
    ):
        actual_digest = canonical_sha256(bundle.model_dump(mode="json"))
        condition_identity, recovery_identity, partition_set, kafka_source, identity_issues = _source_identity(bundle)
        measurement, kafka_issues, kafka_evidence_ids, negative_points = _kafka_observation(bundle)
        postgres_ready, postgres_context, postgres_issues, postgres_ids = _postgres_observation(
            bundle,
            resolved_policy,
        )
        issues = [*activation_problems, *_scope_issues(bundle, resolved_policy)]
        issues.extend(identity_issues)
        issues.extend(kafka_issues)
        issues.extend(postgres_issues)
        if actual_digest != expected_digest:
            issues.append("RECOVERY_BUNDLE_DIGEST_MISMATCH")
        if not _logical_incident_matches(bundle.incident_id, incident_id):
            issues.append("RECOVERY_INCIDENT_IDENTITY_MISMATCH")
        if bundle.collection.started_at <= activation_reference.last_collection_completed_at:
            issues.append("RECOVERY_CAPTURE_NOT_POST_ACTIVATION")
        if bundle.collection.completed_at <= previous_collection:
            issues.append("RECOVERY_COLLECTION_TIMESTAMPS_REORDERED")
        if condition_identity != activation_reference.source_identity_sha256:
            issues.append("RECOVERY_CONDITION_SOURCE_IDENTITY_MISMATCH")
        if expected_recovery_identity is None and recovery_identity is not None:
            expected_recovery_identity = recovery_identity
        elif recovery_identity != expected_recovery_identity:
            issues.append("RECOVERY_SOURCE_IDENTITY_MISMATCH")
        expected_partitions = tuple(str(value) for value in range(resolved_policy.expected_partition_count))
        if partition_set != expected_partitions:
            issues.append("RECOVERY_PARTITION_IDENTITY_MISMATCH")
        if kafka_source is None or previous_kafka_source is None:
            issues.append("RECOVERY_KAFKA_SOURCE_TIMESTAMP_UNUSABLE")
        else:
            interval = (kafka_source - previous_kafka_source).total_seconds()
            if interval <= 0:
                issues.append("RECOVERY_KAFKA_SOURCE_TIMESTAMPS_REORDERED")
            elif not (
                resolved_policy.capture_interval_min_seconds
                <= interval
                <= resolved_policy.capture_interval_max_seconds
            ):
                issues.append("RECOVERY_CAPTURE_INTERVAL_OUTSIDE_TOLERANCE")
        partition_offsets = measurement.get("partition_offsets")
        if isinstance(partition_offsets, dict) and previous_partition_offsets is not None:
            if any(
                partition not in previous_partition_offsets
                or values["end_offset"]
                < previous_partition_offsets[partition]["end_offset"]
                or values["committed_offset"]
                < previous_partition_offsets[partition]["committed_offset"]
                for partition, values in partition_offsets.items()
            ):
                issues.append("RECOVERY_OFFSET_DECREASE_OR_RESET")
        issues = sorted(set(issues))
        if any(issue in _GLOBAL_INTEGRITY_ISSUES for issue in issues):
            stream_integrity_broken = True
        if stream_integrity_broken:
            issues = sorted(set([*issues, "RECOVERY_SEQUENCE_INTEGRITY_BROKEN"]))
        usable = not issues
        references.append(
            RecoverySourceBundleReference(
                sequence_index=index,
                bundle_id=bundle.bundle_id,
                source_incident_id=bundle.incident_id,
                expected_source_bundle_sha256=expected_digest,
                actual_source_bundle_sha256=actual_digest,
                digest_matches=actual_digest == expected_digest,
                collection_started_at=bundle.collection.started_at,
                collection_completed_at=bundle.collection.completed_at,
                kafka_source_timestamp=kafka_source,
            )
        )
        summary = summarize_bundle(bundle)
        raw_observations.append(
            {
                "sequence_index": index,
                "bundle_id": bundle.bundle_id,
                "source_bundle_sha256": actual_digest,
                "usable": usable,
                "issue_codes": issues,
                "condition_source_identity_sha256": condition_identity,
                "recovery_source_identity_sha256": recovery_identity,
                "kafka_source_timestamp": kafka_source,
                "partition_set": list(partition_set),
                **measurement,
                "postgres_ready": postgres_ready,
                "postgres_context": postgres_context,
                "worker_context": summary["worker"],
                "keda_context": summary["keda"],
                "worker_stage_latency_context": summary[
                    "worker_db_persist_stage_latency"
                ],
                "required_evidence_ids": sorted(set([*kafka_evidence_ids, *postgres_ids])),
                "negative_exporter_lag": negative_points,
                "derived_lag_evidence_ids": [],
            }
        )
        previous_collection = bundle.collection.completed_at
        previous_kafka_source = kafka_source
        if isinstance(partition_offsets, dict) and partition_offsets:
            previous_partition_offsets = partition_offsets

    required_count = resolved_policy.recovering_consecutive_capture_count
    matched_windows: list[list[int]] = []
    observations: list[RecoveryCaptureObservation] = []
    recovering_seen = False
    for index, raw in enumerate(raw_observations):
        available_count = min(required_count, index + 1)
        candidate = raw_observations[index - available_count + 1 : index + 1]
        if any(not item["usable"] for item in candidate):
            state = RecoveryState.WORKER_BACKLOG_UNKNOWN
            reasons = sorted(
                {
                    issue
                    for item in candidate
                    for issue in item["issue_codes"]
                }
            ) or ["RECOVERY_REQUIRED_EVIDENCE_UNUSABLE"]
        elif available_count < required_count:
            state = RecoveryState.WORKER_BACKLOG_ACTIVE
            reasons = ["RECOVERY_WINDOW_INCOMPLETE_ACTIVE_REMAINS"]
        elif all(_draining(item) for item in candidate):
            window_indexes = [item["sequence_index"] for item in candidate]
            matched_windows.append(window_indexes)
            recovering_seen = True
            state = RecoveryState.WORKER_BACKLOG_RECOVERING
            produce_values = [
                float(item["produce_rate_60s_records_per_second"])
                for item in candidate
            ]
            reasons = [
                (
                    "BACKLOG_DRAINING_WITH_ZERO_INGRESS"
                    if all(value == 0 for value in produce_values)
                    else "BACKLOG_DRAINING_UNDER_ACTIVE_INGRESS"
                ),
                "THREE_CONSECUTIVE_FRESH_DRAINING_CAPTURES",
                "COMMITTED_RATE_GUARDS_LAG_SLOPE_DIRECTION",
                "POSTGRES_READINESS_ACCEPTABLE",
            ]
        else:
            state = RecoveryState.WORKER_BACKLOG_ACTIVE
            reasons = [
                (
                    "BACKLOG_REGROWTH_OR_DRAIN_STOPPED_ACTIVE_REMAINS"
                    if recovering_seen
                    else "RECOVERING_SEQUENCE_NOT_OBSERVED_ACTIVE_REMAINS"
                )
            ]
        observations.append(
            RecoveryCaptureObservation.model_validate(
                {
                    **raw,
                    "state_after_capture": state,
                    "state_reason_codes": reasons,
                }
            )
        )

    recovered_window: list[int] = []
    if resolved_policy.policy_version.endswith(".v2"):
        recovering_seen = False
        current_reentry: list[int] = []
        for observation in observations:
            if observation.state_after_capture == RecoveryState.WORKER_BACKLOG_RECOVERING:
                recovering_seen = True
            if recovering_seen and _within_recovered_envelope(
                observation,
                resolved_policy,
            ):
                current_reentry.append(observation.sequence_index)
            else:
                current_reentry = []
        recovered_count = resolved_policy.recovered_consecutive_capture_count
        if (
            recovered_count is not None
            and len(current_reentry) >= recovered_count
        ):
            recovered_window = current_reentry[-recovered_count:]
            final_payload = observations[-1].model_dump(mode="json")
            final_payload.update(
                {
                    "state_after_capture": RecoveryState.WORKER_BACKLOG_RECOVERED,
                    "state_reason_codes": [
                        "MEDIUM_ENVELOPE_REENTRY_STABLE_THREE_CAPTURES",
                        "REQUIRED_EVIDENCE_USABLE_AND_FRESH",
                        "POSTGRES_READINESS_ACCEPTABLE",
                        "GLOBAL_SYSTEM_HEALTH_NOT_INFERRED",
                    ],
                }
            )
            observations[-1] = RecoveryCaptureObservation.model_validate(
                final_payload
            )

    final = observations[-1]
    state = final.state_after_capture
    reasons = final.state_reason_codes
    evaluated_indexes = recovered_window or list(
        range(max(0, len(observations) - required_count), len(observations))
    )
    window = RecoveryWindow(
        required_capture_count=required_count,
        evaluated_sequence_indexes=evaluated_indexes,
        matched_recovering_windows=matched_windows,
        first_observed_at=references[0].collection_completed_at,
        last_observed_at=references[-1].collection_completed_at,
        capture_count=len(references),
    )
    evidence_ids = sorted(
        {
            evidence_id
            for item in observations[-required_count:]
            for evidence_id in item.required_evidence_ids
        }
    )
    quality = RecoveryQuality(
        required_evidence_names=[*_KAFKA_METRICS, *_POSTGRES_METRICS],
        expected_partition_count=resolved_policy.expected_partition_count,
        low_lag_evidence_policy=LowLagEvidencePolicy.INVALID_ONLY,
        exporter_negative_lag_preserved=True,
        timestamp_coherence_contract=(
            "All required Kafka series must share one fresh timestamp() source "
            "timestamp and aligned 60s/5s range evaluation grids. Query-range "
            "steps do not preserve raw scrape timestamps, so negative exporter "
            "lag is invalid and cannot be replaced by derived lag."
        ),
    )
    if resolved_policy.policy_version.endswith(".v1"):
        completion = RecoveryCompletion(
            status=RecoveryCompletionStatus.CALIBRATION_PENDING,
            reason_codes=["INSUFFICIENT_VALID_REENTRY_WINDOWS"],
        )
        evaluator_version = EVALUATOR_VERSION
        ruleset_version = RULESET_VERSION
    elif state == RecoveryState.WORKER_BACKLOG_RECOVERED:
        completion = RecoveryCompletion(
            status=RecoveryCompletionStatus.COMPLETE,
            reason_codes=[
                "RECOVERING_PREVIOUSLY_OBSERVED",
                "MEDIUM_ENVELOPE_REENTRY_STABLE_THREE_CAPTURES",
                "POST_RECOVERY_REGRESSION_HANDLING_IS_FUTURE_WORK",
            ],
        )
        evaluator_version = EVALUATOR_VERSION_V2
        ruleset_version = RULESET_VERSION_V2
    else:
        completion = RecoveryCompletion(
            status=RecoveryCompletionStatus.IN_PROGRESS,
            reason_codes=[
                "RECOVERED_STABLE_WINDOW_NOT_CURRENTLY_SATISFIED"
            ],
        )
        evaluator_version = EVALUATOR_VERSION_V2
        ruleset_version = RULESET_VERSION_V2
    evaluation_status = (
        EvaluationStatus.PARTIAL
        if state == RecoveryState.WORKER_BACKLOG_UNKNOWN
        else EvaluationStatus.COMPLETE
    )
    payload = {
        "schema_version": "ops.recovery.v1",
        "evaluator_version": evaluator_version,
        "ruleset_version": ruleset_version,
        "evaluation_status": evaluation_status.value,
        "incident_id": incident_id,
        "activation": activation_reference.model_dump(mode="json"),
        "policy": resolved_policy.model_dump(mode="json"),
        "state": state.value,
        "reason_codes": reasons,
        "source_bundles": [item.model_dump(mode="json") for item in references],
        "observations": [item.model_dump(mode="json") for item in observations],
        "window": window.model_dump(mode="json"),
        "evidence_ids": evidence_ids,
        "quality": quality.model_dump(mode="json"),
        "recovery_completion": completion.model_dump(mode="json"),
    }
    return RecoveryEvaluation(
        recovery_evaluation_id=recovery_evaluation_id(payload),
        **payload,
    )


__all__ = [
    "EVALUATOR_VERSION",
    "EVALUATOR_VERSION_V2",
    "RULESET_VERSION",
    "RULESET_VERSION_V2",
    "evaluate_recovery",
]
