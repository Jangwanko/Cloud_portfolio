"""Frozen runtime projection for v3.5; no source I/O and no fixture substitution."""

from copy import deepcopy
import json

from ops_agent.diagnosis_models import DiagnosisEvidenceStatus, DiagnosisFreshness
from ops_agent.diagnosis_tools import TOOL_BY_ID
from ops_agent.diagnosis_v2_models import DiagnosisEvidenceV2, FrozenProjectedProvenance
from ops_agent.evaluation_models import ConditionName, ConditionState, canonical_sha256
from ops_agent.evaluator import evaluate_bundle
from ops_agent.models import EvidenceBundle, FreshnessStatus
from ops_agent.sequence_evaluator import evaluate_bundle_sequence
from ops_agent.sequence_models import SequenceConditionEvaluation


ADAPTER_VERSION = "ops.diagnosis.runtime-projection.v1"
RUNTIME_TOOLS = ("get_worker_stage_latency", "get_postgres_health",
                 "get_worker_replica_status", "get_keda_status")


def verify_runtime_inputs(bundles, evaluation):
    # Deep parse so later mutations to a caller's models/dicts cannot alter tools.
    parsed = [EvidenceBundle.model_validate_json(
        item.model_dump_json() if isinstance(item, EvidenceBundle) else json.dumps(item))
        for item in bundles]
    recorded = SequenceConditionEvaluation.model_validate(
        evaluation.model_dump(mode="json") if isinstance(evaluation, SequenceConditionEvaluation) else evaluation)
    replay = evaluate_bundle_sequence([item.model_dump(mode="json") for item in parsed])
    if replay.model_dump(mode="json") != recorded.model_dump(mode="json"):
        raise ValueError("runtime activation does not match canonical source replay")
    return parsed, replay


class FrozenRuntimeRegistry:
    """Four zero-argument tools over the end of the first matched activation window.

    The window and complete input sequence are bound to the activation identity.
    Operational flags describe its final capture, not present wall-clock health.
    """

    def __init__(self, *, bundles, evaluation):
        parsed, replay = verify_runtime_inputs(bundles, evaluation)
        core = replay.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
        if core.state != ConditionState.PRESENT:
            raise ValueError("runtime diagnosis requires CORE_BACKLOG_PRESSURE=PRESENT")
        self._evaluation = replay
        self._index = int(core.facts["matched_activation_windows"][0][-1])
        self._bundle = parsed[self._index]
        self._single = evaluate_bundle(self._bundle)
        self._capture = replay.capture_observations[self._index]
        self._called = set()
        self._activation = {
            "condition_evaluation_id": replay.evaluation_id,
            "input_condition": "CORE_BACKLOG_PRESSURE=PRESENT",
            "profile": self._bundle.cluster_profile,
            "cluster_context": self._bundle.scope.context,
            "namespace": self._bundle.scope.namespace,
            "topic": self._bundle.scope.topic,
            "consumer_group": self._bundle.scope.consumer_group,
            "evidence_ids": sorted(set(core.evidence_ids)),
            "source_bundle_digests": [item.source_bundle_sha256 for item in replay.source_bundles],
            "projection_sequence_index": self._index,
            "projection_bundle_sha256": canonical_sha256(self._bundle.model_dump(mode="json")),
            "acquisition_mode": "FROZEN_PROJECTED",
        }

    @property
    def activation(self):
        return deepcopy(self._activation)

    @property
    def tool_ids(self):
        return RUNTIME_TOOLS

    @property
    def called_tool_ids(self):
        return frozenset(self._called)

    def function_tools(self):
        return [{"type": "function", "name": tool, "description": TOOL_BY_ID[tool].description,
            "strict": True, "parameters": {"type": "object", "properties": {},
            "required": [], "additionalProperties": False}}
            for tool in RUNTIME_TOOLS if tool not in self._called]

    def execute(self, tool):
        if tool not in RUNTIME_TOOLS or tool in self._called:
            raise ValueError("runtime tool must be allowlisted and unrepeated")
        self._called.add(tool)
        condition = None
        if tool == "get_postgres_health":
            condition = self._single.conditions[ConditionName.DB_DEGRADED]
        elif tool == "get_worker_replica_status":
            condition = self._single.conditions[ConditionName.WORKER_REPLICA_UNAVAILABLE]
        if condition is not None:
            ids = {value for trace in condition.required_evidence for value in trace.evidence_ids}
            items = [item for item in self._bundle.evidence if item.evidence_id in ids]
            valid = all(trace.evidence_ids and not trace.issues
                and set(trace.evidence_ids) == set(trace.usable_evidence_ids)
                for trace in condition.required_evidence)
            summary = {"facts": condition.facts, "condition_state": condition.state.value,
                "reason_codes": condition.reason_codes}
            if tool == "get_postgres_health":
                valid = valid and condition.state != ConditionState.UNKNOWN
                if valid:
                    summary["semantic_flag"] = ("POSTGRES_PATH_DEGRADED"
                        if condition.state == ConditionState.PRESENT else "POSTGRES_PATH_HEALTHY")
            elif valid:
                desired, available = (condition.facts[key] for key in ("desired_replicas", "available_replicas"))
                summary.update(semantic_flag="WORKER_CAPACITY_SHORTFALL" if available < desired
                    else "WORKER_CAPACITY_AVAILABLE", desired_replicas=desired, available_replicas=available,
                    grace_proven=False)
            error = None if valid else "RUNTIME_DEPENDENCY_NOT_USABLE"
        else:
            is_stage = tool == "get_worker_stage_latency"
            items = [item for item in self._bundle.evidence if (
                item.tool_id == "prometheus.messaging_worker_db_persist_stage_latency_seconds.range.v1"
                if is_stage else item.metric.name == "kubernetes_worker_scaled_object_observation")]
            valid = bool(items) and all(item.status.value == "OK" for item in items)
            summary = ({"context": self._capture.worker_stage_latency_context,
                "semantic_flag": "RUNTIME_STAGE_BASELINE_UNAVAILABLE", "baseline_status": "UNAVAILABLE",
                "commit_latency": "UNAVAILABLE", "evidence_gap": "RUNTIME_STAGE_BASELINE_UNAVAILABLE"}
                if is_stage else {"context": self._capture.keda_context})
            error = None if valid else "RUNTIME_METRIC_NOT_AVAILABLE"
        summary.update(projection_sequence_index=self._index,
            freshness_basis="FROZEN_COLLECTION_TIME", adapter_version=ADAPTER_VERSION)
        states = {item.freshness.status for item in items}
        freshness = (FreshnessStatus.STALE if FreshnessStatus.STALE in states else
            FreshnessStatus.FRESH if states == {FreshnessStatus.FRESH} else FreshnessStatus.UNKNOWN)
        # An invalid dependency can never become positive or healthy signed evidence.
        status = DiagnosisEvidenceStatus.OK if valid else DiagnosisEvidenceStatus.UNAVAILABLE
        timestamps = [item.source_timestamp for item in items if item.source_timestamp]
        source_ids = sorted(item.evidence_id for item in items)
        digest = self._activation["projection_bundle_sha256"]
        identity = {"adapter": ADAPTER_VERSION, "activation": self._evaluation.evaluation_id,
            "bundle": digest, "tool": tool, "status": status.value,
            "freshness": freshness.value, "source_ids": source_ids, "summary": summary}
        return DiagnosisEvidenceV2(evidence_id=f"diagnosis.{tool}.{canonical_sha256(identity)[:24]}",
            tool_id=tool, status=status, observed_at=self._bundle.collection.completed_at,
            freshness=DiagnosisFreshness(status=freshness,
                oldest_source_timestamp=min(timestamps) if timestamps else None,
                newest_source_timestamp=max(timestamps) if timestamps else None),
            semantic_type=f"normalized_{tool}_observation", summary=summary,
            provenance=FrozenProjectedProvenance(acquisition_mode="FROZEN_PROJECTED",
                source_bundle_digests=[digest], source_evidence_ids=source_ids), error_code=error)
