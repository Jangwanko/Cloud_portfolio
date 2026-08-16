"""Allowlisted read-only diagnosis tools over normalized Evidence Bundles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from ops_agent.diagnosis_models import (
    DiagnosisEvidence,
    DiagnosisFreshness,
    evidence_status_summary,
)
from ops_agent.evaluation_models import ConditionName, ConditionState, canonical_sha256
from ops_agent.models import EvidenceBundle, EvidenceItem, FreshnessStatus
from ops_agent.sequence_models import SequenceConditionEvaluation


TOOL_REGISTRY_VERSION = "ops.diagnosis.tools.v1"


@dataclass(frozen=True)
class ToolSpec:
    tool_id: str
    description: str
    reason_code: str


TOOL_SPECS = (
    ToolSpec(
        "get_partition_lag",
        "Return per-partition Kafka lag across the deterministic activation window.",
        "CHECK_PARTITION_LAG_DISTRIBUTION",
    ),
    ToolSpec(
        "get_worker_stage_latency",
        "Return normalized Worker db_persist stage latency context; commit is excluded.",
        "CHECK_WORKER_PERSIST_STAGE",
    ),
    ToolSpec(
        "get_worker_replica_status",
        "Return Worker desired/current/ready/available replica observations.",
        "CHECK_WORKER_REPLICA_PATH",
    ),
    ToolSpec(
        "get_keda_status",
        "Return the Worker KEDA ScaledObject observations and conditions.",
        "CHECK_KEDA_ACTIVITY",
    ),
    ToolSpec(
        "get_postgres_health",
        "Return normalized PostgreSQL HA readiness component observations.",
        "CHECK_POSTGRES_HEALTH",
    ),
    ToolSpec(
        "get_application_readiness",
        "Return normalized application readiness observations.",
        "CHECK_APPLICATION_READINESS",
    ),
    ToolSpec(
        "get_runtime_image",
        "Return desired Worker image and observed pod image identifiers.",
        "CHECK_RUNTIME_IMAGE_CONSISTENCY",
    ),
    ToolSpec(
        "get_pod_restart_status",
        "Return Worker pod phase, readiness, restart, and last termination observations.",
        "CHECK_WORKER_RESTARTS",
    ),
    ToolSpec(
        "get_argocd_status",
        "Return normalized Argo CD sync, health, revision, and reconciliation observations.",
        "CHECK_GITOPS_STATUS",
    ),
)

TOOL_BY_ID = {item.tool_id: item for item in TOOL_SPECS}


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _series_summary(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    samples = [item for item in value if isinstance(item, dict) and "value" in item]
    if not samples:
        return value[:64]
    first = _decimal(samples[0].get("value"))
    latest = _decimal(samples[-1].get("value"))
    return {
        "sample_count": len(samples),
        "first_timestamp": samples[0].get("timestamp"),
        "latest_timestamp": samples[-1].get("timestamp"),
        "first_value": None if first is None else str(first),
        "latest_value": None if latest is None else str(latest),
        "delta": None if first is None or latest is None else str(latest - first),
    }


def _item_summary(item: EvidenceItem) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "status": item.status.value,
        "freshness": item.freshness.status.value,
        "source_timestamp": (
            item.source_timestamp.isoformat() if item.source_timestamp else None
        ),
        "metric_name": item.metric.name,
        "labels": dict(item.labels),
        "value": _series_summary(item.metric.value),
        "semantic_type": item.semantic.type,
        "semantic_flags": list(item.semantic.flags),
    }


class DiagnosisToolRegistry:
    """Fixed tools that only reduce already-normalized evidence."""

    def __init__(
        self,
        *,
        bundles: list[EvidenceBundle],
        condition_evaluation: SequenceConditionEvaluation,
    ) -> None:
        condition_evaluation.verify_integrity()
        core = condition_evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
        if core.state != ConditionState.PRESENT:
            raise ValueError("diagnosis requires CORE_BACKLOG_PRESSURE=PRESENT")
        if len(bundles) != len(condition_evaluation.source_bundles):
            raise ValueError("condition evaluation bundle count mismatch")
        for bundle, reference in zip(bundles, condition_evaluation.source_bundles):
            if bundle.bundle_id != reference.bundle_id:
                raise ValueError("condition evaluation bundle order mismatch")
            if canonical_sha256(bundle.model_dump(mode="json")) != reference.source_bundle_sha256:
                raise ValueError("condition evaluation source bundle digest mismatch")
        windows = core.facts.get("matched_activation_windows")
        if not isinstance(windows, list) or not windows or not isinstance(windows[0], list):
            raise ValueError("PRESENT condition lacks a matched activation window")
        self._indexes = [int(value) for value in windows[0]]
        self._bundles = bundles
        self._evaluation = condition_evaluation
        self._called: set[str] = set()
        self._handlers: dict[str, Callable[[], tuple[list[EvidenceItem], dict[str, Any]]]] = {
            "get_partition_lag": self._partition_lag,
            "get_worker_stage_latency": self._worker_stage_latency,
            "get_worker_replica_status": self._worker_replicas,
            "get_keda_status": self._keda,
            "get_postgres_health": lambda: self._metric_items(
                {"application_postgres_runtime_observation"}
            ),
            "get_application_readiness": lambda: self._metric_items(
                {"application_readiness_observation"}
            ),
            "get_runtime_image": self._runtime_image,
            "get_pod_restart_status": self._pod_restarts,
            "get_argocd_status": lambda: self._metric_items(
                {"argocd_application_observation"}
            ),
        }

    @property
    def tool_ids(self) -> tuple[str, ...]:
        return tuple(item.tool_id for item in TOOL_SPECS)

    @property
    def called_tool_ids(self) -> frozenset[str]:
        return frozenset(self._called)

    def function_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": item.tool_id,
                "description": item.description,
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            }
            for item in TOOL_SPECS
            if item.tool_id not in self._called
        ]

    def execute(self, tool_id: str) -> DiagnosisEvidence:
        if tool_id not in self._handlers:
            raise ValueError(f"tool is not allowlisted: {tool_id}")
        if tool_id in self._called:
            raise ValueError(f"tool may not be repeated: {tool_id}")
        self._called.add(tool_id)
        source_items, summary = self._handlers[tool_id]()
        selected_bundles = [self._bundles[index] for index in self._indexes]
        digests = [
            self._evaluation.source_bundles[index].source_bundle_sha256
            for index in self._indexes
        ]
        source_timestamps = [
            item.source_timestamp for item in source_items if item.source_timestamp
        ]
        freshness_states = {item.freshness.status for item in source_items}
        if FreshnessStatus.STALE in freshness_states:
            freshness_status = FreshnessStatus.STALE
        elif freshness_states and freshness_states == {FreshnessStatus.FRESH}:
            freshness_status = FreshnessStatus.FRESH
        else:
            freshness_status = FreshnessStatus.UNKNOWN
        max_ages = [
            item.freshness.max_age_seconds
            for item in source_items
            if item.freshness.max_age_seconds is not None
        ]
        status = evidence_status_summary([item.status for item in source_items])
        identity = {
            "tool_registry_version": TOOL_REGISTRY_VERSION,
            "tool_id": tool_id,
            "condition_evaluation_id": self._evaluation.evaluation_id,
            "source_bundle_digests": digests,
            "source_evidence_ids": sorted({item.evidence_id for item in source_items}),
            "summary": summary,
        }
        evidence_id = f"diagnosis.{tool_id}.{canonical_sha256(identity)[:24]}"
        return DiagnosisEvidence(
            evidence_id=evidence_id,
            tool_id=tool_id,
            status=status,
            observed_at=max(bundle.collection.completed_at for bundle in selected_bundles),
            freshness=DiagnosisFreshness(
                status=freshness_status,
                oldest_source_timestamp=min(source_timestamps) if source_timestamps else None,
                newest_source_timestamp=max(source_timestamps) if source_timestamps else None,
                max_age_seconds=max(max_ages) if max_ages else None,
            ),
            source_evidence_ids=sorted({item.evidence_id for item in source_items}),
            source_bundle_digests=digests,
            semantic_type=f"normalized_{tool_id}_observation",
            summary=summary,
        )

    def _selected_items(self) -> list[EvidenceItem]:
        return [
            item
            for index in self._indexes
            for item in self._bundles[index].evidence
        ]

    def _metric_items(
        self,
        metric_names: set[str],
    ) -> tuple[list[EvidenceItem], dict[str, Any]]:
        items = [
            item for item in self._selected_items() if item.metric.name in metric_names
        ]
        return items, {"observations": [_item_summary(item) for item in items]}

    def _partition_lag(self) -> tuple[list[EvidenceItem], dict[str, Any]]:
        items = [
            item
            for item in self._selected_items()
            if item.metric.name == "kafka_consumergroup_lag"
        ]
        captures = []
        for index in self._indexes:
            observation = self._evaluation.capture_observations[index]
            partition_items = [
                item
                for item in self._bundles[index].evidence
                if item.metric.name == "kafka_consumergroup_lag"
            ]
            per_partition = {
                item.labels.get("partition", "unknown"): _series_summary(item.metric.value)
                for item in partition_items
            }
            latest_values = [
                _decimal(value.get("latest_value"))
                for value in per_partition.values()
                if isinstance(value, dict)
            ]
            numeric = [value for value in latest_values if value is not None]
            total = sum(numeric, Decimal(0))
            maximum = max(numeric, default=Decimal(0))
            captures.append(
                {
                    "sequence_index": index,
                    "total_lag_records": observation.total_lag_records,
                    "lag_slope_60s_records_per_second": (
                        observation.lag_slope_60s_records_per_second
                    ),
                    "maximum_partition_share": (
                        None if total == 0 else float(maximum / total)
                    ),
                    "per_partition": per_partition,
                }
            )
        return items, {"activation_window": self._indexes, "captures": captures}

    def _worker_stage_latency(self) -> tuple[list[EvidenceItem], dict[str, Any]]:
        items = [
            item
            for item in self._selected_items()
            if item.tool_id
            == "prometheus.messaging_worker_db_persist_stage_latency_seconds.range.v1"
        ]
        return items, {
            "commit_latency": "UNAVAILABLE",
            "stage_semantic": "_persist_message_with_cursor stage; commit excluded",
            "captures": [
                {
                    "sequence_index": index,
                    "context": self._evaluation.capture_observations[
                        index
                    ].worker_stage_latency_context,
                }
                for index in self._indexes
            ],
        }

    def _worker_replicas(self) -> tuple[list[EvidenceItem], dict[str, Any]]:
        items = [
            item
            for item in self._selected_items()
            if item.metric.name == "kubernetes_worker_deployment_observation"
        ]
        return items, {
            "captures": [
                {
                    "sequence_index": index,
                    "context": self._evaluation.capture_observations[index].worker_context,
                }
                for index in self._indexes
            ]
        }

    def _keda(self) -> tuple[list[EvidenceItem], dict[str, Any]]:
        items = [
            item
            for item in self._selected_items()
            if item.metric.name == "kubernetes_worker_scaled_object_observation"
        ]
        return items, {
            "captures": [
                {
                    "sequence_index": index,
                    "context": self._evaluation.capture_observations[index].keda_context,
                }
                for index in self._indexes
            ]
        }

    def _runtime_image(self) -> tuple[list[EvidenceItem], dict[str, Any]]:
        items = [
            item
            for item in self._selected_items()
            if item.metric.name == "kubernetes_worker_pod_observations"
        ]
        return items, {
            "captures": [
                {
                    "sequence_index": index,
                    "desired_image": self._bundles[index].context.desired_image,
                    "pod_image_ids": self._bundles[index].context.pod_image_ids,
                }
                for index in self._indexes
            ]
        }

    def _pod_restarts(self) -> tuple[list[EvidenceItem], dict[str, Any]]:
        items = [
            item
            for item in self._selected_items()
            if item.metric.name == "kubernetes_worker_pod_observations"
        ]
        return items, {"observations": [_item_summary(item) for item in items]}


__all__ = [
    "DiagnosisToolRegistry",
    "TOOL_BY_ID",
    "TOOL_REGISTRY_VERSION",
    "TOOL_SPECS",
    "ToolSpec",
]
