"""Pure, deterministic evaluation of frozen ``ops.evidence.v1`` bundles."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from ops_agent.evaluation_models import (
    AssessmentName,
    AssessmentResult,
    ConditionDependencyTrace,
    ConditionEvaluation,
    ConditionName,
    ConditionResult,
    ConditionState,
    DependencyRequirement,
    EvaluationPolicy,
    EvaluationStatus,
    EvidenceDependencyTrace,
    EvidenceIssue,
    SourceBundleReference,
    canonical_sha256,
    condition_evaluation_id,
)
from ops_agent.models import EvidenceBundle, EvidenceItem, EvidenceStatus, FreshnessStatus


EVALUATOR_VERSION = "ops.evaluator.v1"
RULESET_VERSION = "ops.conditions.rules.v1"


@dataclass(frozen=True)
class DependencySpec:
    name: str
    metric_names: tuple[str, ...]
    requirement: DependencyRequirement
    partitioned: bool = False
    source: str | None = None
    tool_id: str | None = None
    semantic_type: str | None = None
    unit: str | None = None
    window: str | None = None
    aggregation: str | None = None
    freshness_basis: str | None = None
    freshness_max_age_seconds: float | None = None
    required_labels: tuple[tuple[str, str], ...] = ()


def _required(
    name: str,
    *metric_names: str,
    partitioned: bool = False,
    source: str | None = None,
    tool_id: str | None = None,
    semantic_type: str | None = None,
    unit: str | None = None,
    window: str | None = None,
    aggregation: str | None = None,
    freshness_basis: str | None = None,
    freshness_max_age_seconds: float | None = None,
    required_labels: Mapping[str, str] | None = None,
) -> DependencySpec:
    return DependencySpec(
        name=name,
        metric_names=metric_names,
        requirement=DependencyRequirement.REQUIRED,
        partitioned=partitioned,
        source=source,
        tool_id=tool_id,
        semantic_type=semantic_type,
        unit=unit,
        window=window,
        aggregation=aggregation,
        freshness_basis=freshness_basis,
        freshness_max_age_seconds=freshness_max_age_seconds,
        required_labels=tuple(sorted((required_labels or {}).items())),
    )


def _optional(name: str, *metric_names: str) -> DependencySpec:
    return DependencySpec(
        name=name,
        metric_names=metric_names,
        requirement=DependencyRequirement.OPTIONAL,
    )


_END_OFFSET = _required(
    "topic_end_offsets",
    "kafka_topic_partition_current_offset",
    partitioned=True,
    source="prometheus",
    tool_id="prometheus.kafka_topic_partition_current_offset.range.v1",
    semantic_type="kafka_partition_end_offset_gauge",
    unit="records",
    window="60s",
    aggregation="raw_range_samples",
    freshness_basis="prometheus_timestamp_function",
    freshness_max_age_seconds=15.0,
    required_labels={"job": "kafka-exporter"},
)
_COMMITTED_OFFSET = _required(
    "consumer_committed_offsets",
    "kafka_consumergroup_current_offset",
    partitioned=True,
    source="prometheus",
    tool_id="prometheus.kafka_consumergroup_current_offset.range.v1",
    semantic_type="kafka_consumer_committed_offset_gauge",
    unit="records",
    window="60s",
    aggregation="raw_range_samples",
    freshness_basis="prometheus_timestamp_function",
    freshness_max_age_seconds=15.0,
    required_labels={"job": "kafka-exporter"},
)
_PARTITION_LAG = _required(
    "consumer_partition_lag",
    "kafka_consumergroup_lag",
    partitioned=True,
    source="prometheus",
    tool_id="prometheus.kafka_consumergroup_lag.range.v1",
    semantic_type="kafka_consumer_partition_lag_gauge",
    unit="records",
    window="60s",
    aggregation="raw_range_samples",
    freshness_basis="prometheus_timestamp_function",
    freshness_max_age_seconds=15.0,
    required_labels={"job": "kafka-exporter"},
)

_APPLICATION_READINESS = _required(
    "application_readiness",
    "application_readiness_observation",
    source="application",
    tool_id="application.readiness.get.v1",
    semantic_type="application_readiness",
    freshness_basis="collector_time_http_response",
    freshness_max_age_seconds=10.0,
    required_labels={"endpoint": "/health/ready"},
)

_POSTGRES_RUNTIME = _required(
    "postgres_runtime_readiness",
    "application_postgres_runtime_observation",
    source="application",
    tool_id="application.readiness.get.v1",
    semantic_type="application_postgres_runtime_observation",
    freshness_basis="collector_time_http_response",
    freshness_max_age_seconds=10.0,
    required_labels={"endpoint": "/health/ready"},
)

_WORKER_DEPLOYMENT = _required(
    "worker_deployment",
    "kubernetes_worker_deployment_observation",
    source="kubernetes",
    tool_id="k8s.worker_deployment.get.v1",
    semantic_type="worker_deployment_raw_observation",
    freshness_basis="collector_time_kubernetes_api_observation",
    freshness_max_age_seconds=30.0,
    required_labels={"namespace": "messaging-app", "deployment": "worker"},
)

CONDITION_DEPENDENCIES: dict[
    ConditionName,
    dict[DependencyRequirement, tuple[DependencySpec, ...]],
] = {
    ConditionName.CORE_BACKLOG_PRESSURE: {
        DependencyRequirement.REQUIRED: (
            _END_OFFSET,
            _COMMITTED_OFFSET,
            _PARTITION_LAG,
        ),
        DependencyRequirement.OPTIONAL: (
            _optional("worker_terminal_processing", "messaging_worker_processed_total"),
            _optional(
                "queue_wait_samples",
                "messaging_queue_wait_seconds_count",
                "messaging_queue_wait_seconds",
            ),
            _optional(
                "worker_db_persist_stage_samples",
                "messaging_worker_stage_latency_seconds_count",
                "messaging_worker_stage_latency_seconds",
            ),
            _optional(
                "worker_deployment",
                "kubernetes_worker_deployment_observation",
            ),
        ),
    },
    ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED: {
        DependencyRequirement.REQUIRED: (_PARTITION_LAG,),
        DependencyRequirement.OPTIONAL: (
            _optional(
                "partition_end_offset_slope",
                "kafka_topic_partition_current_offset",
            ),
            _optional("worker_terminal_processing", "messaging_worker_processed_total"),
        ),
    },
    ConditionName.DB_DEGRADED: {
        DependencyRequirement.REQUIRED: (
            _APPLICATION_READINESS,
            _POSTGRES_RUNTIME,
        ),
        DependencyRequirement.OPTIONAL: (
            _optional(
                "event_persist_lag_samples",
                "messaging_event_persist_lag_seconds_count",
                "messaging_event_persist_lag_seconds",
            ),
            _optional(
                "worker_db_persist_stage_samples",
                "messaging_worker_stage_latency_seconds_count",
                "messaging_worker_stage_latency_seconds",
            ),
        ),
    },
    ConditionName.WORKER_REPLICA_UNAVAILABLE: {
        DependencyRequirement.REQUIRED: (
            _WORKER_DEPLOYMENT,
        ),
        DependencyRequirement.OPTIONAL: (
            _optional(
                "application_worker_summary",
                "application_ops_summary_observation",
            ),
            _optional("worker_pods", "kubernetes_worker_pod_observations"),
            _optional(
                "worker_scaled_object",
                "kubernetes_worker_scaled_object_observation",
            ),
        ),
    },
}


_BLOCKING_SEMANTIC_FLAGS = {
    "counter_reset_or_decrease",
    "negative_value_preserved_not_zero",
    "non_finite_or_non_numeric_value",
    "offset_decrease",
    "partial_partition_coverage",
    "partition_set_mismatch",
    "source_timestamp_coverage_mismatch",
    "source_timestamp_missing",
    "source_timestamp_query_error",
}

_POSTGRES_REASON_PREDICATES = {
    "postgres_primary_unreachable": "primary_reachable",
    "postgres_ready_standbys_below_minimum": "standby_count",
    "postgres_sync_standbys_below_minimum": "sync_standby_count",
    "postgres_replication_delay_high": "max_replication_delay_bytes",
}


@dataclass
class _ObservedDependency:
    spec: DependencySpec
    items: list[EvidenceItem]
    issues: list[EvidenceIssue] = field(default_factory=list)
    _issue_keys: set[tuple[str, str | None, str | None]] = field(
        default_factory=set,
        init=False,
        repr=False,
    )

    def add_issue(
        self,
        code: str,
        *,
        evidence_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        issue = EvidenceIssue(code=code, evidence_id=evidence_id, detail=detail)
        key = (issue.code, issue.evidence_id, issue.detail)
        if key not in self._issue_keys:
            self._issue_keys.add(key)
            self.issues.append(issue)

    @property
    def blocking(self) -> bool:
        return self.spec.requirement == DependencyRequirement.REQUIRED and bool(
            self.issues
        )

    def trace(self) -> EvidenceDependencyTrace:
        items = sorted(self.items, key=lambda item: item.evidence_id)
        has_global_issue = any(
            issue.evidence_id is None for issue in self.issues
        )
        issue_ids = {
            issue.evidence_id for issue in self.issues if issue.evidence_id is not None
        }
        missing_ids = sorted(
            item.evidence_id
            for item in items
            if item.status == EvidenceStatus.MISSING
        )
        stale_ids = sorted(
            item.evidence_id
            for item in items
            if item.freshness.status == FreshnessStatus.STALE
        )
        unknown_freshness_ids = sorted(
            item.evidence_id
            for item in items
            if item.freshness.status == FreshnessStatus.UNKNOWN
        )
        coverage_ids = sorted(
            {
                issue.evidence_id
                for issue in self.issues
                if issue.evidence_id is not None
                and "COVERAGE" in issue.code
            }
        )
        anomaly_ids = sorted(
            {
                issue.evidence_id
                for issue in self.issues
                if issue.evidence_id is not None
                and issue.code == "REQUIRED_SEMANTIC_ANOMALY"
            }
        )
        return EvidenceDependencyTrace(
            dependency=self.spec.name,
            requirement=self.spec.requirement,
            accepted_metric_names=list(self.spec.metric_names),
            evidence_ids=[item.evidence_id for item in items],
            usable_evidence_ids=[
                item.evidence_id
                for item in items
                if item.status == EvidenceStatus.OK
                and item.freshness.status == FreshnessStatus.FRESH
                and not has_global_issue
                and item.evidence_id not in issue_ids
            ],
            evidence_statuses={item.evidence_id: item.status for item in items},
            freshness_statuses={
                item.evidence_id: item.freshness.status for item in items
            },
            missing=(not items or bool(missing_ids)),
            missing_evidence_ids=missing_ids,
            stale_evidence_ids=stale_ids,
            unknown_freshness_evidence_ids=unknown_freshness_ids,
            coverage_incomplete_evidence_ids=coverage_ids,
            semantic_anomaly_evidence_ids=anomaly_ids,
            issues=sorted(
                self.issues,
                key=lambda issue: (
                    issue.code,
                    issue.evidence_id or "",
                    issue.detail or "",
                ),
            ),
        )


def _evidence_index(bundle: EvidenceBundle) -> dict[str, list[EvidenceItem]]:
    index: dict[str, list[EvidenceItem]] = defaultdict(list)
    for item in bundle.evidence:
        index[item.metric.name].append(item)
    for items in index.values():
        items.sort(key=lambda item: item.evidence_id)
    return dict(index)


def _observe_dependency(
    spec: DependencySpec,
    index: Mapping[str, list[EvidenceItem]],
) -> _ObservedDependency:
    by_id: dict[str, EvidenceItem] = {}
    for metric_name in spec.metric_names:
        for item in index.get(metric_name, []):
            by_id[item.evidence_id] = item
    observation = _ObservedDependency(
        spec=spec,
        items=sorted(by_id.values(), key=lambda item: item.evidence_id),
    )
    prefix = spec.requirement.value
    if not observation.items:
        observation.add_issue(f"{prefix}_EVIDENCE_MISSING")
        return observation

    if spec.requirement == DependencyRequirement.OPTIONAL and not any(
        (
            spec.source,
            spec.tool_id,
            spec.semantic_type,
            spec.unit,
            spec.window,
            spec.aggregation,
            spec.required_labels,
        )
    ):
        observation.add_issue("OPTIONAL_SELECTOR_UNBOUND")

    for item in observation.items:
        if item.status != EvidenceStatus.OK:
            observation.add_issue(
                f"{prefix}_EVIDENCE_STATUS_{item.status.value}",
                evidence_id=item.evidence_id,
            )
        if item.freshness.status == FreshnessStatus.STALE:
            observation.add_issue(
                f"{prefix}_EVIDENCE_STALE",
                evidence_id=item.evidence_id,
            )
        elif item.freshness.status != FreshnessStatus.FRESH:
            observation.add_issue(
                f"{prefix}_EVIDENCE_FRESHNESS_{item.freshness.status.value}",
                evidence_id=item.evidence_id,
            )
        if (
            spec.freshness_basis is not None
            and item.freshness.basis != spec.freshness_basis
        ):
            observation.add_issue(
                f"{prefix}_FRESHNESS_BASIS_MISMATCH",
                evidence_id=item.evidence_id,
                detail=(
                    f"expected={spec.freshness_basis!r};"
                    f"actual={item.freshness.basis!r}"
                ),
            )
        if (
            spec.freshness_max_age_seconds is not None
            and item.freshness.max_age_seconds
            != spec.freshness_max_age_seconds
        ):
            observation.add_issue(
                f"{prefix}_FRESHNESS_BOUND_MISMATCH",
                evidence_id=item.evidence_id,
                detail=(
                    f"expected={spec.freshness_max_age_seconds};"
                    f"actual={item.freshness.max_age_seconds!r}"
                ),
            )
        if item.source_timestamp is None:
            observation.add_issue(
                f"{prefix}_SOURCE_TIMESTAMP_MISSING",
                evidence_id=item.evidence_id,
            )
        if (
            spec.requirement == DependencyRequirement.REQUIRED
            and (item.raw_ref is None or item.raw_sha256 is None)
        ):
            observation.add_issue(
                "REQUIRED_RAW_REFERENCE_MISSING",
                evidence_id=item.evidence_id,
            )
        selector_checks = (
            ("SOURCE", item.source, spec.source),
            ("TOOL", item.tool_id, spec.tool_id),
            ("SEMANTIC", item.semantic.type, spec.semantic_type),
            ("UNIT", item.metric.unit, spec.unit),
            ("WINDOW", item.metric.window, spec.window),
            ("AGGREGATION", item.metric.aggregation, spec.aggregation),
        )
        for selector_name, actual, expected in selector_checks:
            if expected is not None and actual != expected:
                observation.add_issue(
                    f"{prefix}_{selector_name}_SELECTOR_MISMATCH",
                    evidence_id=item.evidence_id,
                    detail=f"expected={expected!r};actual={actual!r}",
                )
        for label_name, expected in spec.required_labels:
            actual = item.labels.get(label_name)
            if actual != expected:
                observation.add_issue(
                    f"{prefix}_LABEL_SELECTOR_MISMATCH",
                    evidence_id=item.evidence_id,
                    detail=(
                        f"label={label_name};expected={expected!r};"
                        f"actual={actual!r}"
                    ),
                )
        if spec.partitioned and item.coverage.complete is not True:
            observation.add_issue(
                f"{prefix}_PARTITION_COVERAGE_INCOMPLETE",
                evidence_id=item.evidence_id,
            )
        blocking_flags = sorted(
            set(item.semantic.flags).intersection(_BLOCKING_SEMANTIC_FLAGS)
        )
        if blocking_flags:
            observation.add_issue(
                f"{prefix}_SEMANTIC_ANOMALY",
                evidence_id=item.evidence_id,
                detail=",".join(blocking_flags),
            )
    return observation


def _condition_observations(
    condition: ConditionName,
    index: Mapping[str, list[EvidenceItem]],
) -> tuple[list[_ObservedDependency], list[_ObservedDependency]]:
    registry = CONDITION_DEPENDENCIES[condition]
    required = [
        _observe_dependency(spec, index)
        for spec in registry[DependencyRequirement.REQUIRED]
    ]
    optional = [
        _observe_dependency(spec, index)
        for spec in registry[DependencyRequirement.OPTIONAL]
    ]
    return required, optional


@dataclass(frozen=True)
class _PartitionSeries:
    item: EvidenceItem
    timestamps: tuple[Decimal, ...]
    values: tuple[Decimal, ...]


def _parse_partition_series(
    bundle: EvidenceBundle,
    item: EvidenceItem,
    observation: _ObservedDependency,
    policy: EvaluationPolicy,
    *,
    committed_offsets: bool,
    require_monotonic: bool,
) -> _PartitionSeries | None:
    samples = item.metric.value
    expected_count = policy.kafka_expected_sample_count
    if (
        not isinstance(samples, list)
        or len(samples) != expected_count
        or item.metric.sample_count != expected_count
    ):
        observation.add_issue(
            "REQUIRED_SAMPLE_COUNT_MISMATCH",
            evidence_id=item.evidence_id,
            detail=(
                f"expected={expected_count};value_count="
                f"{len(samples) if isinstance(samples, list) else 'invalid'};"
                f"declared={item.metric.sample_count}"
            ),
        )
        return None

    timestamps: list[Decimal] = []
    values: list[Decimal] = []
    for sample_index, sample in enumerate(samples):
        if not isinstance(sample, Mapping) or not {"timestamp", "value"}.issubset(
            sample
        ):
            observation.add_issue(
                "REQUIRED_SAMPLE_INVALID",
                evidence_id=item.evidence_id,
                detail=f"sample_index={sample_index}",
            )
            return None
        try:
            timestamp = Decimal(str(sample["timestamp"]))
            value = Decimal(str(sample["value"]))
        except (InvalidOperation, ValueError):
            observation.add_issue(
                "REQUIRED_SAMPLE_NON_NUMERIC",
                evidence_id=item.evidence_id,
                detail=f"sample_index={sample_index}",
            )
            return None
        if not timestamp.is_finite() or not value.is_finite():
            observation.add_issue(
                "REQUIRED_SAMPLE_NON_FINITE",
                evidence_id=item.evidence_id,
                detail=f"sample_index={sample_index}",
            )
            return None
        if value != value.to_integral_value():
            observation.add_issue(
                "REQUIRED_RECORD_VALUE_NON_INTEGRAL",
                evidence_id=item.evidence_id,
                detail=f"sample_index={sample_index};value={value}",
            )
        if committed_offsets and value == -1:
            observation.add_issue(
                "COMMITTED_OFFSET_UNINITIALIZED",
                evidence_id=item.evidence_id,
                detail=f"sample_index={sample_index}",
            )
        elif value < 0:
            observation.add_issue(
                "REQUIRED_NEGATIVE_VALUE",
                evidence_id=item.evidence_id,
                detail=f"sample_index={sample_index};value={value}",
            )
        timestamps.append(timestamp)
        values.append(value)

    step = Decimal(policy.kafka_range_step_seconds)
    if any(
        current - previous != step
        for previous, current in zip(timestamps, timestamps[1:])
    ):
        observation.add_issue(
            "REQUIRED_SAMPLE_GRID_INVALID",
            evidence_id=item.evidence_id,
        )
    expected_span = Decimal(policy.kafka_range_window_seconds)
    if timestamps[-1] - timestamps[0] != expected_span:
        observation.add_issue(
            "REQUIRED_SAMPLE_WINDOW_INVALID",
            evidence_id=item.evidence_id,
        )
    collected_epoch = Decimal(str(item.collected_at.timestamp()))
    range_end_delta = abs(timestamps[-1] - collected_epoch)
    if range_end_delta > Decimal(
        str(policy.kafka_range_collection_skew_seconds)
    ):
        observation.add_issue(
            "REQUIRED_RANGE_END_COLLECTION_TIME_MISMATCH",
            evidence_id=item.evidence_id,
            detail=f"delta_seconds={range_end_delta}",
        )
    if item.source_timestamp is not None:
        source_epoch = Decimal(str(item.source_timestamp.timestamp()))
        source_to_range_end = timestamps[-1] - source_epoch
        if source_to_range_end < 0 or source_to_range_end > Decimal(
            str(policy.kafka_source_to_range_end_max_seconds)
        ):
            observation.add_issue(
                "REQUIRED_RANGE_END_SOURCE_TIME_MISMATCH",
                evidence_id=item.evidence_id,
                detail=f"delta_seconds={source_to_range_end}",
            )
        expected_age = (
            bundle.collection.completed_at - item.source_timestamp
        ).total_seconds()
        actual_age = item.freshness.age_seconds
        if (
            actual_age is None
            or abs(actual_age - expected_age)
            > policy.freshness_age_tolerance_seconds
        ):
            observation.add_issue(
                "REQUIRED_FRESHNESS_AGE_MISMATCH",
                evidence_id=item.evidence_id,
                detail=(
                    f"expected={expected_age};actual={actual_age!r}"
                ),
            )
    if (
        item.collected_at < bundle.collection.started_at
        or item.collected_at > bundle.collection.completed_at
    ):
        observation.add_issue(
            "REQUIRED_COLLECTION_INTERVAL_MISMATCH",
            evidence_id=item.evidence_id,
        )
    if require_monotonic and any(
        current < previous for previous, current in zip(values, values[1:])
    ):
        observation.add_issue(
            "REQUIRED_OFFSET_DECREASE_OBSERVED",
            evidence_id=item.evidence_id,
        )
    return _PartitionSeries(
        item=item,
        timestamps=tuple(timestamps),
        values=tuple(values),
    )


def _partition_values(
    bundle: EvidenceBundle,
    observation: _ObservedDependency,
    policy: EvaluationPolicy,
    *,
    require_consumer_group: bool,
    committed_offsets: bool = False,
    require_monotonic: bool = False,
) -> dict[str, _PartitionSeries]:
    series_by_partition: dict[str, _PartitionSeries] = {}
    partitions: list[str] = []
    expected_sets: set[tuple[str, ...]] = set()
    exporter_identities: set[tuple[str | None, str | None]] = set()
    source_timestamps: set[str] = set()
    collected_timestamps: set[str] = set()
    raw_identities: set[tuple[str | None, str | None]] = set()

    for item in observation.items:
        if item.status != EvidenceStatus.OK:
            continue
        partition = item.labels.get("partition")
        if partition is None:
            observation.add_issue(
                "REQUIRED_PARTITION_LABEL_MISSING",
                evidence_id=item.evidence_id,
            )
            continue
        partitions.append(partition)
        if item.labels.get("__name__") != item.metric.name:
            observation.add_issue(
                "REQUIRED_METRIC_LABEL_MISMATCH",
                evidence_id=item.evidence_id,
            )
        if item.labels.get("topic") != bundle.scope.topic:
            observation.add_issue(
                "REQUIRED_TOPIC_SCOPE_MISMATCH",
                evidence_id=item.evidence_id,
                detail=partition,
            )
        if require_consumer_group:
            observed_group = item.labels.get("consumergroup") or item.labels.get(
                "consumer_group"
            )
            if observed_group != bundle.scope.consumer_group:
                observation.add_issue(
                    "REQUIRED_CONSUMER_GROUP_SCOPE_MISMATCH",
                    evidence_id=item.evidence_id,
                    detail=partition,
                )
        exporter_identities.add(
            (item.labels.get("job"), item.labels.get("instance"))
        )
        if item.source_timestamp is not None:
            source_timestamps.add(item.source_timestamp.isoformat())
        collected_timestamps.add(item.collected_at.isoformat())
        raw_identities.add((item.raw_ref, item.raw_sha256))
        expected = tuple(sorted(item.coverage.expected_items))
        if expected:
            expected_sets.add(expected)
        else:
            observation.add_issue(
                "REQUIRED_PARTITION_EXPECTATION_MISSING",
                evidence_id=item.evidence_id,
            )
        expected_partitions = {
            str(partition_id)
            for partition_id in range(policy.expected_partition_count)
        }
        if (
            item.coverage.expected_count != policy.expected_partition_count
            or item.coverage.observed_count != policy.expected_partition_count
            or set(item.coverage.expected_items) != expected_partitions
            or set(item.coverage.observed_items) != expected_partitions
            or item.coverage.missing_items
            or item.coverage.extra_items
        ):
            observation.add_issue(
                "REQUIRED_PARTITION_COVERAGE_CONTRACT_MISMATCH",
                evidence_id=item.evidence_id,
            )
        series = _parse_partition_series(
            bundle,
            item,
            observation,
            policy,
            committed_offsets=committed_offsets,
            require_monotonic=require_monotonic,
        )
        if series is not None and partition not in series_by_partition:
            series_by_partition[partition] = series

    duplicates = sorted(
        partition for partition, count in Counter(partitions).items() if count > 1
    )
    if duplicates:
        observation.add_issue(
            "REQUIRED_DUPLICATE_PARTITION_SERIES",
            detail=",".join(duplicates),
        )
    if len(exporter_identities) != 1:
        observation.add_issue("REQUIRED_EXPORTER_IDENTITY_MIXED")
    if len(source_timestamps) != 1:
        observation.add_issue("REQUIRED_SOURCE_TIMESTAMP_MIXED")
    if len(collected_timestamps) != 1:
        observation.add_issue("REQUIRED_COLLECTION_TIMESTAMP_MIXED")
    if len(raw_identities) != 1 or next(iter(raw_identities), (None, None))[0] is None:
        observation.add_issue("REQUIRED_RAW_SOURCE_MIXED")
    if len(expected_sets) > 1:
        observation.add_issue("REQUIRED_PARTITION_EXPECTATION_INCONSISTENT")
    if len(expected_sets) == 1:
        expected = set(next(iter(expected_sets)))
        observed = set(partitions)
        if expected != observed:
            observation.add_issue(
                "REQUIRED_PARTITION_COVERAGE_MISMATCH",
                detail=(
                    f"missing={','.join(sorted(expected - observed))};"
                    f"extra={','.join(sorted(observed - expected))}"
                ),
            )
    grids = {series.timestamps for series in series_by_partition.values()}
    if len(grids) != 1:
        observation.add_issue("REQUIRED_SAMPLE_GRID_MISMATCH")
    return series_by_partition


def _all_issue_codes(observations: list[_ObservedDependency]) -> list[str]:
    return sorted({issue.code for item in observations for issue in item.issues})


def _json_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _condition_result(
    *,
    condition: ConditionName,
    state: ConditionState,
    reason_codes: list[str],
    required: list[_ObservedDependency],
    optional: list[_ObservedDependency],
    facts: Mapping[str, Any] | None = None,
) -> ConditionResult:
    required_traces = [item.trace() for item in required]
    optional_traces = [item.trace() for item in optional]
    return ConditionResult(
        condition=condition,
        state=state,
        reason_codes=list(dict.fromkeys(reason_codes)),
        evidence_ids=sorted(
            {
                evidence.evidence_id
                for dependency in required
                for evidence in dependency.items
            }
        ),
        required_evidence=required_traces,
        optional_evidence=optional_traces,
        missing_required_dependencies=sorted(
            trace.dependency for trace in required_traces if trace.missing
        ),
        stale_required_evidence_ids=sorted(
            {
                evidence_id
                for trace in required_traces
                for evidence_id in trace.stale_evidence_ids
            }
        ),
        unknown_freshness_required_evidence_ids=sorted(
            {
                evidence_id
                for trace in required_traces
                for evidence_id in trace.unknown_freshness_evidence_ids
            }
        ),
        facts=dict(facts or {}),
    )


def _unknown_condition(
    condition: ConditionName,
    required: list[_ObservedDependency],
    optional: list[_ObservedDependency],
    *,
    facts: Mapping[str, Any] | None = None,
) -> ConditionResult:
    return _condition_result(
        condition=condition,
        state=ConditionState.UNKNOWN,
        reason_codes=[
            f"{condition.value}_REQUIRED_EVIDENCE_UNUSABLE",
            *_all_issue_codes(required),
        ],
        required=required,
        optional=optional,
        facts=facts,
    )


def _evaluate_core_backlog(
    bundle: EvidenceBundle,
    index: Mapping[str, list[EvidenceItem]],
    policy: EvaluationPolicy,
) -> ConditionResult:
    condition = ConditionName.CORE_BACKLOG_PRESSURE
    required, optional = _condition_observations(condition, index)
    end_values = _partition_values(
        bundle,
        required[0],
        policy,
        require_consumer_group=False,
        require_monotonic=True,
    )
    committed_values = _partition_values(
        bundle,
        required[1],
        policy,
        require_consumer_group=True,
        committed_offsets=True,
        require_monotonic=True,
    )
    lag_values = _partition_values(
        bundle,
        required[2],
        policy,
        require_consumer_group=True,
    )
    partition_sets = [set(end_values), set(committed_values), set(lag_values)]
    if all(partition_sets) and not (
        partition_sets[0] == partition_sets[1] == partition_sets[2]
    ):
        required[0].add_issue("KAFKA_REQUIRED_PARTITION_SET_MISMATCH")
    if partition_sets[0] == partition_sets[1] == partition_sets[2]:
        for partition in sorted(partition_sets[0]):
            end_series = end_values[partition]
            committed_series = committed_values[partition]
            lag_series = lag_values[partition]
            if not (
                end_series.timestamps
                == committed_series.timestamps
                == lag_series.timestamps
            ):
                required[2].add_issue(
                    "KAFKA_REQUIRED_SAMPLE_GRID_MISMATCH",
                    evidence_id=lag_series.item.evidence_id,
                    detail=f"partition={partition}",
                )
                continue
            for sample_index, (end, committed, lag) in enumerate(
                zip(
                    end_series.values,
                    committed_series.values,
                    lag_series.values,
                )
            ):
                if committed == -1:
                    continue
                expected_lag = end - committed
                if expected_lag != lag:
                    required[2].add_issue(
                        "KAFKA_OFFSET_LAG_ARITHMETIC_MISMATCH",
                        evidence_id=lag_series.item.evidence_id,
                        detail=(
                            f"partition={partition};sample_index={sample_index};"
                            f"end={end};committed={committed};lag={lag};"
                            f"expected_lag={expected_lag}"
                        ),
                    )
    exporter_identities = {
        (
            series.item.labels.get("job"),
            series.item.labels.get("instance"),
        )
        for family in (end_values, committed_values, lag_values)
        for series in family.values()
    }
    if len(exporter_identities) != 1:
        required[0].add_issue("KAFKA_REQUIRED_EXPORTER_IDENTITY_MISMATCH")
    source_timestamps = {
        series.item.source_timestamp.isoformat()
        for family in (end_values, committed_values, lag_values)
        for series in family.values()
        if series.item.source_timestamp is not None
    }
    if len(source_timestamps) != 1:
        required[0].add_issue("KAFKA_REQUIRED_SOURCE_TIMESTAMP_MISMATCH")
    facts = {
        "end_offsets": {
            key: _json_number(series.values[-1])
            for key, series in sorted(end_values.items())
        },
        "committed_offsets": {
            key: _json_number(series.values[-1])
            for key, series in sorted(committed_values.items())
        },
        "partition_lag": {
            key: _json_number(series.values[-1])
            for key, series in sorted(lag_values.items())
        },
    }
    if any(item.blocking for item in required):
        return _unknown_condition(
            condition,
            required,
            optional,
            facts=facts,
        )

    total_lag_series = [
        sum(
            (series.values[sample_index] for series in lag_values.values()),
            Decimal(0),
        )
        for sample_index in range(policy.kafka_expected_sample_count)
    ]
    facts["latest_total_lag_records"] = _json_number(total_lag_series[-1])
    facts["maximum_total_lag_records"] = _json_number(max(total_lag_series))
    facts["window_all_zero"] = all(value == 0 for value in total_lag_series)
    if not facts["window_all_zero"]:
        return _condition_result(
            condition=condition,
            state=ConditionState.UNKNOWN,
            reason_codes=[
                "BACKLOG_OBSERVED_IN_WINDOW",
                "PRESSURE_POLICY_UNCALIBRATED",
            ],
            required=required,
            optional=optional,
            facts=facts,
        )
    return _condition_result(
        condition=condition,
        state=ConditionState.ABSENT,
        reason_codes=["BACKLOG_ZERO_FOR_FULL_WINDOW"],
        required=required,
        optional=optional,
        facts=facts,
    )


def _evaluate_partition_concentration(
    bundle: EvidenceBundle,
    index: Mapping[str, list[EvidenceItem]],
    policy: EvaluationPolicy,
) -> ConditionResult:
    condition = ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED
    required, optional = _condition_observations(condition, index)
    lag_values = _partition_values(
        bundle,
        required[0],
        policy,
        require_consumer_group=True,
    )
    facts: dict[str, Any] = {
        "partition_lag": {
            key: _json_number(series.values[-1])
            for key, series in sorted(lag_values.items())
        }
    }
    if any(item.blocking for item in required):
        return _unknown_condition(condition, required, optional, facts=facts)

    total_lag_series = [
        sum(
            (series.values[sample_index] for series in lag_values.values()),
            Decimal(0),
        )
        for sample_index in range(policy.kafka_expected_sample_count)
    ]
    maximum_total_lag = max(total_lag_series)
    facts["latest_total_lag_records"] = _json_number(total_lag_series[-1])
    facts["maximum_total_lag_records"] = _json_number(maximum_total_lag)
    if maximum_total_lag == 0:
        facts["maximum_partition_share"] = 0.0
        return _condition_result(
            condition=condition,
            state=ConditionState.ABSENT,
            reason_codes=["NO_LAG_TO_CONCENTRATE_FOR_FULL_WINDOW"],
            required=required,
            optional=optional,
            facts=facts,
        )

    latest_values = {
        partition: series.values[-1]
        for partition, series in lag_values.items()
    }
    latest_total = total_lag_series[-1]
    maximum_lag = max(latest_values.values())
    dominant = sorted(
        partition for partition, value in latest_values.items() if value == maximum_lag
    )
    share = maximum_lag / latest_total if latest_total > 0 else Decimal(0)
    facts.update(
        {
            "maximum_partition_lag_records": _json_number(maximum_lag),
            "maximum_partition_share": float(share),
            "dominant_partitions": dominant,
        }
    )
    return _condition_result(
        condition=condition,
        state=ConditionState.UNKNOWN,
        reason_codes=[
            "PARTITION_LAG_OBSERVED_IN_WINDOW",
            "CONCENTRATION_POLICY_UNCALIBRATED",
        ],
        required=required,
        optional=optional,
        facts=facts,
    )


def _single_mapping_value(
    observation: _ObservedDependency,
) -> Mapping[str, Any] | None:
    ok_items = [item for item in observation.items if item.status == EvidenceStatus.OK]
    if len(ok_items) != 1:
        if len(ok_items) > 1:
            observation.add_issue("REQUIRED_EVIDENCE_AMBIGUOUS")
        return None
    value = ok_items[0].metric.value
    if not isinstance(value, Mapping):
        observation.add_issue(
            "REQUIRED_VALUE_INVALID",
            evidence_id=ok_items[0].evidence_id,
        )
        return None
    return value


def _required_bool(
    value: Mapping[str, Any],
    field_name: str,
    observation: _ObservedDependency,
) -> bool | None:
    raw = value.get(field_name)
    if isinstance(raw, bool):
        return raw
    observation.add_issue(
        "REQUIRED_FIELD_MISSING" if field_name not in value else "REQUIRED_FIELD_INVALID",
        evidence_id=(
            observation.items[0].evidence_id
            if len(observation.items) == 1
            else None
        ),
        detail=field_name,
    )
    return None


def _required_nonnegative_int(
    value: Mapping[str, Any],
    field_name: str,
    observation: _ObservedDependency,
) -> int | None:
    raw = value.get(field_name)
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    observation.add_issue(
        "REQUIRED_FIELD_MISSING" if field_name not in value else "REQUIRED_FIELD_INVALID",
        evidence_id=(
            observation.items[0].evidence_id
            if len(observation.items) == 1
            else None
        ),
        detail=field_name,
    )
    return None


def _evaluate_db_degraded(
    index: Mapping[str, list[EvidenceItem]],
    policy: EvaluationPolicy,
) -> ConditionResult:
    condition = ConditionName.DB_DEGRADED
    required, optional = _condition_observations(condition, index)
    readiness_observation, postgres_observation = required
    readiness_value = _single_mapping_value(readiness_observation)
    postgres_value = _single_mapping_value(postgres_observation)
    facts: dict[str, Any] = {}
    if postgres_value is not None:
        ha_mode = _required_bool(postgres_value, "ha_mode", postgres_observation)
        primary = _required_bool(
            postgres_value,
            "primary_reachable",
            postgres_observation,
        )
        standby = _required_nonnegative_int(
            postgres_value,
            "standby_count",
            postgres_observation,
        )
        sync = _required_nonnegative_int(
            postgres_value,
            "sync_standby_count",
            postgres_observation,
        )
        delay = _required_nonnegative_int(
            postgres_value,
            "max_replication_delay_bytes",
            postgres_observation,
        )
        facts["observed"] = {
            "ha_mode": ha_mode,
            "primary_reachable": primary,
            "standby_count": standby,
            "sync_standby_count": sync,
            "max_replication_delay_bytes": delay,
        }
    else:
        ha_mode = primary = None
        standby = sync = delay = None

    body: Mapping[str, Any] | None = None
    body_status: str | None = None
    reasons: list[str] | None = None
    if readiness_value is not None:
        raw_body = readiness_value.get("body")
        if isinstance(raw_body, Mapping):
            body = raw_body
        else:
            readiness_observation.add_issue(
                "REQUIRED_READINESS_BODY_INVALID",
                evidence_id=readiness_observation.items[0].evidence_id,
            )
        raw_body_status = readiness_value.get("body_status")
        if isinstance(raw_body_status, str) and raw_body_status in {
            "ready",
            "degraded",
            "not_ready",
        }:
            body_status = raw_body_status
        else:
            readiness_observation.add_issue(
                "REQUIRED_READINESS_STATUS_INVALID",
                evidence_id=readiness_observation.items[0].evidence_id,
            )
        http_status = readiness_value.get("http_status")
        expected_http_status = 503 if body_status == "not_ready" else 200
        if (
            not isinstance(http_status, int)
            or isinstance(http_status, bool)
            or http_status != expected_http_status
        ):
            readiness_observation.add_issue(
                "READINESS_HTTP_STATUS_MISMATCH",
                evidence_id=readiness_observation.items[0].evidence_id,
                detail=(
                    f"expected={expected_http_status};actual={http_status!r}"
                ),
            )
    if body is not None:
        if body.get("status") != body_status:
            readiness_observation.add_issue(
                "READINESS_STATUS_BODY_MISMATCH",
                evidence_id=readiness_observation.items[0].evidence_id,
            )
        raw_reasons = body.get("reason")
        if isinstance(raw_reasons, list) and all(
            isinstance(reason, str) for reason in raw_reasons
        ):
            reasons = list(raw_reasons)
        else:
            readiness_observation.add_issue(
                "REQUIRED_READINESS_REASONS_INVALID",
                evidence_id=readiness_observation.items[0].evidence_id,
            )
        nested_postgres = body.get("postgres")
        if not isinstance(nested_postgres, Mapping) or (
            postgres_value is not None and dict(nested_postgres) != dict(postgres_value)
        ):
            readiness_observation.add_issue(
                "READINESS_POSTGRES_COMPONENT_MISMATCH",
                evidence_id=readiness_observation.items[0].evidence_id,
            )

    readiness_item = (
        readiness_observation.items[0]
        if len(readiness_observation.items) == 1
        else None
    )
    postgres_item = (
        postgres_observation.items[0]
        if len(postgres_observation.items) == 1
        else None
    )
    if readiness_item is not None and postgres_item is not None:
        if (
            readiness_item.raw_ref is None
            or (
                readiness_item.raw_ref,
                readiness_item.raw_sha256,
            )
            != (
                postgres_item.raw_ref,
                postgres_item.raw_sha256,
            )
        ):
            readiness_observation.add_issue(
                "READINESS_POSTGRES_RAW_SOURCE_MISMATCH"
            )
        if readiness_item.source_timestamp != postgres_item.source_timestamp:
            readiness_observation.add_issue(
                "READINESS_POSTGRES_TIMESTAMP_MISMATCH"
            )
        if readiness_item.collected_at != postgres_item.collected_at:
            readiness_observation.add_issue(
                "READINESS_POSTGRES_COLLECTION_TIMESTAMP_MISMATCH"
            )

    facts["readiness_status"] = body_status
    facts["readiness_reasons"] = reasons
    facts["local_ha_guardrails"] = {
        "ha_mode_required": True,
        "min_ready_standbys": policy.local_ha_min_ready_standbys,
        "min_sync_standbys": policy.local_ha_min_sync_standbys,
        "max_replication_delay_bytes": (
            policy.local_ha_max_replication_delay_bytes
        ),
    }

    if any(item.blocking for item in required):
        return _unknown_condition(condition, required, optional, facts=facts)

    assert reasons is not None
    postgres_reasons = sorted(
        reason for reason in reasons if reason.startswith("postgres_")
    )
    unknown_postgres_reasons = sorted(
        set(postgres_reasons) - set(_POSTGRES_REASON_PREDICATES)
    )
    if unknown_postgres_reasons:
        readiness_observation.add_issue(
            "READINESS_POSTGRES_REASON_UNRECOGNIZED",
            evidence_id=readiness_item.evidence_id if readiness_item else None,
            detail=",".join(unknown_postgres_reasons),
        )
        return _unknown_condition(condition, required, optional, facts=facts)
    db_reasons = set(postgres_reasons)
    if ha_mode is not True:
        postgres_observation.add_issue(
            "LOCAL_HA_MODE_CONTRACT_MISMATCH",
            evidence_id=postgres_item.evidence_id if postgres_item else None,
            detail=f"ha_mode={ha_mode!r}",
        )
        return _unknown_condition(condition, required, optional, facts=facts)

    degradation_observed = {
        "postgres_primary_unreachable": primary is False,
        "postgres_ready_standbys_below_minimum": (
            standby is not None
            and standby < policy.local_ha_min_ready_standbys
        ),
        "postgres_sync_standbys_below_minimum": (
            sync is not None and sync < policy.local_ha_min_sync_standbys
        ),
        "postgres_replication_delay_high": (
            delay is not None
            and delay > policy.local_ha_max_replication_delay_bytes
        ),
    }
    conflicts = sorted(
        reason
        for reason, observed in degradation_observed.items()
        if observed != (reason in db_reasons)
    )
    if conflicts:
        readiness_observation.add_issue(
            "READINESS_POSTGRES_REASON_COMPONENT_CONFLICT",
            evidence_id=readiness_item.evidence_id if readiness_item else None,
            detail=",".join(conflicts),
        )
        return _unknown_condition(condition, required, optional, facts=facts)
    if body_status == "ready" and reasons:
        readiness_observation.add_issue(
            "READINESS_READY_WITH_REASONS",
            evidence_id=readiness_item.evidence_id if readiness_item else None,
        )
        return _unknown_condition(condition, required, optional, facts=facts)
    if body_status in {"degraded", "not_ready"} and not reasons:
        readiness_observation.add_issue(
            "READINESS_NON_READY_WITHOUT_REASONS",
            evidence_id=readiness_item.evidence_id if readiness_item else None,
        )
        return _unknown_condition(condition, required, optional, facts=facts)
    return _condition_result(
        condition=condition,
        state=(ConditionState.PRESENT if db_reasons else ConditionState.ABSENT),
        reason_codes=(
            [
                f"APPLICATION_READINESS_{reason.upper()}"
                for reason in sorted(db_reasons)
            ]
            or ["NO_POSTGRES_DEGRADATION_REASON"]
        ),
        required=required,
        optional=optional,
        facts=facts,
    )


def _evaluate_worker_unavailable(
    index: Mapping[str, list[EvidenceItem]],
    policy: EvaluationPolicy,
) -> ConditionResult:
    condition = ConditionName.WORKER_REPLICA_UNAVAILABLE
    required, optional = _condition_observations(condition, index)
    observation = required[0]
    value = _single_mapping_value(observation)
    if value is not None:
        metadata = value.get("metadata")
        if isinstance(metadata, Mapping):
            generation = _required_nonnegative_int(
                metadata,
                "generation",
                observation,
            )
            if metadata.get("name") != policy.worker_deployment_name:
                observation.add_issue(
                    "DEPLOYMENT_NAME_SCOPE_MISMATCH",
                    evidence_id=observation.items[0].evidence_id,
                    detail=(
                        f"expected={policy.worker_deployment_name!r};"
                        f"actual={metadata.get('name')!r}"
                    ),
                )
            if metadata.get("namespace") != policy.namespace:
                observation.add_issue(
                    "DEPLOYMENT_NAMESPACE_SCOPE_MISMATCH",
                    evidence_id=observation.items[0].evidence_id,
                    detail=(
                        f"expected={policy.namespace!r};"
                        f"actual={metadata.get('namespace')!r}"
                    ),
                )
        else:
            observation.add_issue(
                "REQUIRED_DEPLOYMENT_METADATA_INVALID",
                evidence_id=observation.items[0].evidence_id,
            )
            generation = None
        observed_generation = _required_nonnegative_int(
            value,
            "observed_generation",
            observation,
        )
        desired = _required_nonnegative_int(value, "desired_replicas", observation)
        current = _required_nonnegative_int(value, "current_replicas", observation)
        available = _required_nonnegative_int(value, "available_replicas", observation)
    else:
        generation = observed_generation = None
        desired = current = available = None
    if (
        generation is not None
        and observed_generation is not None
        and generation != observed_generation
    ):
        observation.add_issue(
            "DEPLOYMENT_GENERATION_NOT_OBSERVED",
            evidence_id=observation.items[0].evidence_id,
            detail=f"generation={generation};observed={observed_generation}",
        )
    if current is not None and available is not None and available > current:
        observation.add_issue(
            "DEPLOYMENT_REPLICA_COUNTS_INCOHERENT",
            evidence_id=observation.items[0].evidence_id,
            detail=f"current={current};available={available}",
        )
    facts = {
        "generation": generation,
        "observed_generation": observed_generation,
        "desired_replicas": desired,
        "current_replicas": current,
        "available_replicas": available,
        "unavailable_grace_seconds": policy.worker_unavailable_grace_seconds,
    }
    if any(item.blocking for item in required):
        return _unknown_condition(condition, required, optional, facts=facts)

    assert desired is not None and current is not None and available is not None
    shortfall_reasons: list[str] = []
    if current < desired:
        shortfall_reasons.append("WORKER_CURRENT_REPLICA_SHORTFALL_OBSERVED")
    if available < desired:
        shortfall_reasons.append("WORKER_AVAILABLE_REPLICA_SHORTFALL_OBSERVED")
    if shortfall_reasons:
        return _condition_result(
            condition=condition,
            state=ConditionState.UNKNOWN,
            reason_codes=[
                *shortfall_reasons,
                "UNAVAILABLE_GRACE_NOT_PROVEN_BY_SINGLE_SNAPSHOT",
            ],
            required=required,
            optional=optional,
            facts=facts,
        )
    return _condition_result(
        condition=condition,
        state=ConditionState.ABSENT,
        reason_codes=["ALL_DESIRED_WORKER_REPLICAS_AVAILABLE"],
        required=required,
        optional=optional,
        facts=facts,
    )


def _no_backlog_assessment(
    conditions: Mapping[ConditionName, ConditionResult],
) -> AssessmentResult:
    dependencies = [
        conditions[ConditionName.CORE_BACKLOG_PRESSURE],
        conditions[ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED],
    ]
    states = {item.state for item in dependencies}
    if states == {ConditionState.ABSENT}:
        state = ConditionState.PRESENT
        reasons = ["BACKLOG_CONDITIONS_ABSENT"]
    elif ConditionState.PRESENT in states:
        state = ConditionState.ABSENT
        reasons = ["BACKLOG_PRESSURE_CONDITION_PRESENT"]
    else:
        state = ConditionState.UNKNOWN
        reasons = ["BACKLOG_CONDITION_UNKNOWN"]
    return AssessmentResult(
        assessment=AssessmentName.NO_BACKLOG_PRESSURE_DETECTED,
        state=state,
        reason_codes=reasons,
        condition_dependencies=[
            ConditionDependencyTrace(
                condition=item.condition,
                state=item.state,
                reason_codes=item.reason_codes,
            )
            for item in dependencies
        ],
    )


def _validate_bundle(
    bundle: EvidenceBundle | Mapping[str, Any] | str | bytes,
) -> EvidenceBundle:
    if isinstance(bundle, EvidenceBundle):
        return bundle
    if isinstance(bundle, (str, bytes)):
        return EvidenceBundle.model_validate_json(bundle)
    return EvidenceBundle.model_validate(bundle)


def _validate_policy(
    policy: EvaluationPolicy | Mapping[str, Any] | None,
) -> EvaluationPolicy:
    if policy is None:
        return EvaluationPolicy()
    if isinstance(policy, EvaluationPolicy):
        return policy
    return EvaluationPolicy.model_validate(policy)


def _evaluation_id(
    bundle: EvidenceBundle,
    policy: EvaluationPolicy,
    conditions: Mapping[ConditionName, ConditionResult],
    assessments: Mapping[AssessmentName, AssessmentResult],
) -> str:
    return condition_evaluation_id(
        evaluator_version=EVALUATOR_VERSION,
        ruleset_version=RULESET_VERSION,
        policy=policy,
        source_bundle=_source_bundle_reference(bundle),
        conditions=dict(conditions),
        assessments=dict(assessments),
    )


def _source_bundle_reference(bundle: EvidenceBundle) -> SourceBundleReference:
    return SourceBundleReference(
        schema_version=bundle.schema_version,
        bundle_id=bundle.bundle_id,
        incident_id=bundle.incident_id,
        cluster_profile=bundle.cluster_profile,
        collection_status=bundle.collection.status,
        source_bundle_sha256=canonical_sha256(bundle.model_dump(mode="json")),
    )


def evaluate_bundle(
    bundle: EvidenceBundle | Mapping[str, Any] | str | bytes,
    *,
    policy: EvaluationPolicy | Mapping[str, Any] | None = None,
) -> ConditionEvaluation:
    """Evaluate a frozen bundle without I/O, source access, or freshness aging."""

    source = _validate_bundle(bundle)
    resolved_policy = _validate_policy(policy)
    if source.cluster_profile != resolved_policy.cluster_profile:
        raise ValueError(
            "evaluation policy cluster profile mismatch: "
            f"bundle={source.cluster_profile!r}, "
            f"policy={resolved_policy.cluster_profile!r}"
        )
    scope_mismatches = {
        "context": (source.scope.context, resolved_policy.context),
        "namespace": (source.scope.namespace, resolved_policy.namespace),
        "topic": (source.scope.topic, resolved_policy.topic),
        "consumer_group": (
            source.scope.consumer_group,
            resolved_policy.consumer_group,
        ),
        "source_policy_version": (
            source.context.policy_version,
            resolved_policy.source_policy_version,
        ),
    }
    mismatched = {
        name: values
        for name, values in scope_mismatches.items()
        if values[0] != values[1]
    }
    if mismatched:
        raise ValueError(f"evaluation policy scope mismatch: {mismatched}")
    index = _evidence_index(source)
    condition_results = {
        ConditionName.CORE_BACKLOG_PRESSURE: _evaluate_core_backlog(
            source,
            index,
            resolved_policy,
        ),
        ConditionName.PARTITION_LAG_CONCENTRATION_OBSERVED: (
            _evaluate_partition_concentration(source, index, resolved_policy)
        ),
        ConditionName.DB_DEGRADED: _evaluate_db_degraded(index, resolved_policy),
        ConditionName.WORKER_REPLICA_UNAVAILABLE: _evaluate_worker_unavailable(
            index,
            resolved_policy,
        ),
    }
    no_backlog = _no_backlog_assessment(condition_results)
    assessments = {AssessmentName.NO_BACKLOG_PRESSURE_DETECTED: no_backlog}
    has_unknown = any(
        result.state == ConditionState.UNKNOWN
        for result in [*condition_results.values(), *assessments.values()]
    )
    source_reference = _source_bundle_reference(source)
    return ConditionEvaluation(
        evaluator_version=EVALUATOR_VERSION,
        ruleset_version=RULESET_VERSION,
        evaluation_id=_evaluation_id(
            source,
            resolved_policy,
            condition_results,
            assessments,
        ),
        evaluation_status=(
            EvaluationStatus.PARTIAL if has_unknown else EvaluationStatus.COMPLETE
        ),
        policy=resolved_policy,
        source_bundle=source_reference,
        conditions=condition_results,
        assessments=assessments,
    )


evaluate_conditions = evaluate_bundle


__all__ = [
    "CONDITION_DEPENDENCIES",
    "EVALUATOR_VERSION",
    "EvaluationPolicy",
    "RULESET_VERSION",
    "evaluate_bundle",
    "evaluate_conditions",
]
