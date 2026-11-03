"""Read-only observation gate; never invent an activation or invoke a model."""

from collections import Counter

from ops_agent.evaluation_models import ConditionName, ConditionState, canonical_sha256
from ops_agent.evaluator import evaluate_bundle
from ops_agent.models import EvidenceBundle
from ops_agent.sequence_evaluator import evaluate_bundle_sequence


OBSERVATION_VERSION = "ops.observation.v1"


def summarize_observation(bundles):
    """Replay frozen captures with the existing condition rules, without I/O.

    PRESENT indicates eligibility only. Wiring a v3.5 runtime evidence adapter is
    a separate step; no synthetic catalog or historical activation is substituted.
    Freshness remains relative to each recorded collection, not the replay time.
    """
    if len(bundles) != 3:
        raise ValueError("observation requires exactly three captures")
    bundles = [EvidenceBundle.model_validate(bundle) for bundle in bundles]
    sequence = evaluate_bundle_sequence([bundle.model_dump(mode="json") for bundle in bundles])
    sequence.verify_integrity()
    core = sequence.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
    gate = {
        ConditionState.PRESENT: "ELIGIBLE_FOR_DIAGNOSIS",
        ConditionState.ABSENT: "SKIPPED_NO_BACKLOG_PRESSURE",
        ConditionState.UNKNOWN: "SKIPPED_INSUFFICIENT_ACTIVATION_EVIDENCE",
    }[core.state]
    captures = []
    for bundle, capture in zip(bundles, sequence.capture_observations):
        result = evaluate_bundle(bundle)
        captures.append({
            "bundle_id": bundle.bundle_id,
            "bundle_sha256": canonical_sha256(bundle.model_dump(mode="json")),
            "collection": bundle.collection.model_dump(mode="json"),
            "context": bundle.context.model_dump(mode="json"),
            "conditions": {name.value: {"state": value.state.value,
                "reason_codes": value.reason_codes} for name, value in result.conditions.items()},
            "required_evidence_usable": capture.required_evidence_usable,
            "total_lag_records": capture.total_lag_records,
            "lag_slope_60s_records_per_second": capture.lag_slope_60s_records_per_second,
            "worker_context": capture.worker_context,
            "keda_context": capture.keda_context,
            "worker_stage_latency_context": capture.worker_stage_latency_context,
            "evidence_status_counts": dict(Counter(item.status.value for item in bundle.evidence)),
            "freshness_counts": dict(Counter(item.freshness.status.value for item in bundle.evidence)),
            "unusable_evidence": [{"evidence_id": item.evidence_id, "metric": item.metric.name,
                "status": item.status.value, "freshness": item.freshness.status.value,
                "semantic_flags": item.semantic.flags}
                for item in bundle.evidence
                if item.status.value != "OK" or item.freshness.status.value != "FRESH"],
        })
    summary = {
        "schema_version": OBSERVATION_VERSION,
        "classification": "READ_ONLY_RUNTIME_OBSERVATION",
        "scope": bundles[0].scope.model_dump(mode="json"),
        "sequence_evaluation_id": sequence.evaluation_id,
        "activation_state": core.state.value,
        "activation_reason_codes": core.reason_codes,
        "diagnosis_gate": gate,
        "model": {"status": "NOT_RUN", "api_request_count": 0},
        "fixed_diagnosis_runbook": {"status": "NOT_RUN"},
        "diagnosis_comparison": "NOT_EVALUATED",
        "runtime_v35_adapter": "PENDING",
        "captures": captures,
        "limitations": ["No model or causal diagnosis evaluated in this capture-only step.",
            "Collection-time freshness; sequential sources are not an atomic snapshot.",
            "No event writes, fault injection, remediation or recovery judgment."],
    }
    return summary, sequence.model_dump(mode="json")
