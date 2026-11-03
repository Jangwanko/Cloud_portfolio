"""Evidence-bound repair: only directly contradicted positive observations may go."""

from ops_agent.diagnosis_v3_models import ObservedSignalV3
from ops_agent.diagnosis_v3_validator import SIGNAL_RULES
from ops_agent.diagnosis_validator import DiagnosisValidationError

NEGATIVE_FLAGS = {
    "WORKER_STAGE_ELEVATED": "WITHIN_SCENARIO_BASELINE",
    "WORKER_REPLICA_SHORTFALL": "WORKER_CAPACITY_AVAILABLE",
    "POSTGRES_GUARDRAIL_DEGRADED": "POSTGRES_PATH_HEALTHY",
}
REPAIR_INSTRUCTIONS = """Repair the output contract only, with no tools or new evidence.
Preserve hypothesis names and support statuses. Do not add observations or citation
IDs absent from the first result. You may REMOVE an observation only if it is listed
in removable_observations: its original citations directly measure a fresh OK normal
state for that same signal. Preserve every other observation, including supported
positive observations and observations with unavailable, stale or unknown evidence.
Correct structure, gaps and citation roles within the existing evidence. No new
investigation or causal rediagnosis. Return only the required decision JSON.
All original contract rules still apply. The caller independently enforces this scope.
"""


def repair_policy_v3():
    return {"version": "ops.diagnosis.repair.v3.1",
            "removal_requires": "Every original citation is acquired, fresh, OK and directly normal for the same signal/tool",
            "negative_flags": NEGATIVE_FLAGS.copy(),
            "forbidden": ["ADD_OBSERVATION", "CHANGE_HYPOTHESIS_NAME_OR_STATUS", "ADD_CITATION_ID",
                          "REMOVE_SUPPORTED_OR_UNVERIFIED_OBSERVATION", "NEW_TOOL_OR_EVIDENCE"]}


def removable_observations(original, acquired_evidence):
    """Read trusted canonical acquisition; never use expected fixture answers."""
    by_id = {item.evidence_id: item for item in acquired_evidence}
    if len(by_id) != len(acquired_evidence):
        return []
    observations = original.get("observations", [])
    result = []
    for raw in observations:
        try:
            observation = ObservedSignalV3.model_validate(raw)
        except ValueError:
            continue
        name = observation.signal.value
        if sum(isinstance(item, dict) and item.get("signal") == name for item in observations) != 1:
            continue
        def contradicts(value):
            item = by_id.get(value)
            return (item is not None and item.status.value == "OK"
                    and item.freshness.status.value == "FRESH"
                    and item.tool_id == SIGNAL_RULES[name][0]
                    and item.summary.get("semantic_flag") == NEGATIVE_FLAGS[name])
        if all(contradicts(value) for value in observation.evidence_ids):
            result.append({"signal": name, "contradicting_evidence_ids": observation.evidence_ids})
    return result


def _citations(raw):
    values = set(raw.get("context_evidence_ids", []))
    for item in raw.get("observations", []):
        values.update(item.get("evidence_ids", []))
    for item in raw.get("hypotheses", []):
        values.update(item.get("supporting_evidence_ids", []))
        values.update(item.get("conflicting_evidence_ids", []))
    return values


def enforce_repair_scope(original, repaired, *, acquired_evidence=()):
    before = {item.get("signal") for item in original.get("observations", [])}
    after = {item["signal"] for item in repaired["observations"]}
    proofs = removable_observations(original, acquired_evidence)
    allowed = {item["signal"] for item in proofs}
    removed = before - after
    if (not after <= before or not removed <= allowed
            or {(item.get("hypothesis"), item.get("support_status")) for item in original.get("hypotheses", [])}
            != {(item["hypothesis"], item["support_status"]) for item in repaired["hypotheses"]}
            or not _citations(repaired) <= _citations(original)):
        raise DiagnosisValidationError("V3_REPAIR_REDIAGNOSIS_FORBIDDEN", "V3_REPAIR_REDIAGNOSIS_FORBIDDEN: repair exceeded evidence-bound removal/format scope")
    return {"policy_version": repair_policy_v3()["version"],
            "removed_observations": [item for item in proofs if item["signal"] in removed]}
