"""Offline evidence-role checks for the v3 candidate; no causal truth claim."""

import re

from ops_agent.diagnosis_v3_models import AgentDecisionV3
from ops_agent.diagnosis_validator import DiagnosisValidationError


SIGNAL_RULES = {
    "WORKER_STAGE_ELEVATED": ("get_worker_stage_latency", "ABOVE_SCENARIO_BASELINE"),
    "WORKER_REPLICA_SHORTFALL": ("get_worker_replica_status", "WORKER_CAPACITY_SHORTFALL"),
    "POSTGRES_GUARDRAIL_DEGRADED": ("get_postgres_health", "POSTGRES_PATH_DEGRADED"),
}
CAUSE_RULES = {
    "WORKER_CAPACITY_SHORTFALL_SUSPECTED": {
        "supporting": ("get_worker_replica_status", "WORKER_CAPACITY_SHORTFALL"),
        "conflicting": ("get_worker_replica_status", "WORKER_CAPACITY_AVAILABLE"),
    },
    "POSTGRES_GUARDRAIL_PRESSURE_SUSPECTED": {
        "supporting": ("get_postgres_health", "POSTGRES_PATH_DEGRADED"),
        "conflicting": ("get_postgres_health", "POSTGRES_PATH_HEALTHY"),
    },
}


def validate_decision_v3(decision: AgentDecisionV3, *, initial_evidence_ids, acquired_evidence):
    """Consume trusted normalized acquisition results, not unverified raw input.

    Frozen bundle integrity/provenance and tool budgets remain the caller's gates.
    This candidate has no production acquisition loop or completeness scorer.
    """
    def reject(code, message):
        raise DiagnosisValidationError(code, message)

    by_id = {item.evidence_id: item for item in acquired_evidence}
    text = " ".join([*decision.investigation_gaps,
                     *(gap for item in decision.hypotheses for gap in item.evidence_gaps)]).upper()
    if re.search(r"CORE_BACKLOG_PRESSURE_(?:ABSENT|UNKNOWN)|RECOVERED|RECOVERY_DECLAR|REMEDIAT|KUBECTL|PROMQL|SHELL|SCALE_WORKER|DELETE_POD|RESTART_POD|ARGO_SYNC|DLQ_REPLAY|DB_RESTORE|ROOT_CAUSE_CONFIRMED", text):
        reject("V3_FORBIDDEN_CLAIM", "gap fields cannot carry actions, condition changes or root-cause/recovery declarations")
    if len(by_id) != len(acquired_evidence) or len(set(initial_evidence_ids)) != len(initial_evidence_ids):
        reject("V3_DUPLICATE_INPUT_ID", "input evidence IDs must be unique")
    if set(initial_evidence_ids) & set(by_id):
        reject("V3_AMBIGUOUS_INPUT_ID", "initial and acquired evidence IDs must not collide")
    known = set(initial_evidence_ids) | set(by_id)
    citations = set(decision.context_evidence_ids)
    for item in decision.observations:
        citations.update(item.evidence_ids)
    for item in decision.hypotheses:
        citations.update(item.supporting_evidence_ids)
        citations.update(item.conflicting_evidence_ids)
    if not citations <= known:
        reject("V3_UNKNOWN_CITATION", "all context, observation and causal IDs must exist")

    def matches(evidence_id, rule):
        item = by_id.get(evidence_id)
        return (item is not None and item.status.value == "OK"
                and item.freshness.status.value == "FRESH"
                and item.tool_id == rule[0] and item.summary.get("semantic_flag") == rule[1])

    for observation in decision.observations:
        if not all(matches(value, SIGNAL_RULES[observation.signal.value]) for value in observation.evidence_ids):
            reject("V3_OBSERVATION_NOT_MEASURED", "signal citations must directly measure a fresh OK observation")
    for candidate in decision.hypotheses:
        name, status = candidate.hypothesis.value, candidate.support_status.value
        supporting, conflicting = candidate.supporting_evidence_ids, candidate.conflicting_evidence_ids
        if status == "SUPPORTED" and not supporting:
            reject("V3_SUPPORT_ANCHOR_REQUIRED", "supported candidate requires direct signed support")
        if status == "CONFLICTED" and (not supporting or not conflicting):
            reject("V3_CONFLICT_REQUIRES_BOTH_SIDES", "conflicted candidate requires both citation sides")
        if status == "INSUFFICIENT" and not candidate.evidence_gaps:
            reject("V3_GAP_REQUIRED", "insufficient candidate requires an explicit gap")
        if name not in CAUSE_RULES:
            if status != "INSUFFICIENT" or supporting or conflicting:
                reject("V3_CAUSE_TELEMETRY_UNAVAILABLE", "no signed causal rule exists for this candidate")
            if name == "CONSUMER_REBALANCE_SUSPECTED" and "CONSUMER_REBALANCE_TELEMETRY_UNAVAILABLE" not in candidate.evidence_gaps:
                reject("V3_REBALANCE_GAP_REQUIRED", "preserve the exact rebalance telemetry gap")
            continue
        for side, values in (("supporting", supporting), ("conflicting", conflicting)):
            if not all(matches(value, CAUSE_RULES[name][side]) for value in values):
                reject("V3_CONTEXT_IS_NOT_CAUSAL_SUPPORT", "lag, KEDA, stage context and unavailable data cannot substitute for direct signed evidence")
    supported = any(item.support_status.value == "SUPPORTED" for item in decision.hypotheses)
    if (decision.conclusion == "SUPPORTED_CANDIDATES") != supported:
        reject("V3_CONCLUSION_MISMATCH", "conclusion must agree with supported candidate presence")
    if not supported and not decision.investigation_gaps and not any(item.evidence_gaps for item in decision.hypotheses):
        reject("V3_ABSTENTION_GAP_REQUIRED", "abstention must preserve missing information")
    return {"contract_valid": True, "evidence_roles_valid": True,
            "causal_truth_verified": False, "investigation_completeness": "NOT_EVALUATED"}
