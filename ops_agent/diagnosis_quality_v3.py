"""Predeclared v3 dimensions; canonical acquisition revalidation before scoring."""

from ops_agent.diagnosis_scenarios import ControlledScenarioRegistry
from ops_agent.diagnosis_v3_models import AgentDecisionV3
from ops_agent.diagnosis_v3_validator import CAUSE_RULES, SIGNAL_RULES, validate_decision_v3
from ops_agent.evaluation_models import canonical_sha256
from ops_agent.fixed_runbook import TOOL_ORDER


def fixed_decision_v3(*, activation, evidence):
    """Generic evidence rules only; no fixture IDs, answer labels or rubric access."""
    def matches(item, rule):
        return (item.status.value == "OK" and item.freshness.status.value == "FRESH"
                and item.tool_id == rule[0] and item.summary.get("semantic_flag") == rule[1])
    observations = [{"signal": signal, "evidence_ids": [item.evidence_id]}
                    for signal, rule in SIGNAL_RULES.items() for item in evidence if matches(item, rule)]
    hypotheses = [{"hypothesis": cause, "support_status": "SUPPORTED",
                   "supporting_evidence_ids": [item.evidence_id], "conflicting_evidence_ids": [], "evidence_gaps": []}
                  for cause, rules in CAUSE_RULES.items() for item in evidence if matches(item, rules["supporting"])]
    signed = {value for item in hypotheses for value in item["supporting_evidence_ids"]}
    return AgentDecisionV3.model_validate({"schema_version": "ops.diagnosis.decision.v3",
        "input_condition": "CORE_BACKLOG_PRESSURE=PRESENT", "observations": observations,
        "hypotheses": hypotheses, "context_evidence_ids": [*activation["evidence_ids"],
            *(item.evidence_id for item in evidence if item.evidence_id not in signed)],
        "investigation_gaps": [] if hypotheses else ["CAUSAL_DISCRIMINATOR_UNAVAILABLE"],
        "conclusion": "SUPPORTED_CANDIDATES" if hypotheses else "ABSTAIN"})


def fixed_run_v3(*, catalog, fixture_id):
    registry = ControlledScenarioRegistry(catalog=catalog, fixture_id=fixture_id)
    evidence = [registry.execute(tool) for tool in TOOL_ORDER]
    decision = fixed_decision_v3(activation=catalog.activation, evidence=evidence)
    validate_decision_v3(decision, initial_evidence_ids=catalog.activation["evidence_ids"], acquired_evidence=evidence)
    return {"schema_version": "ops.diagnosis.run.v3", "fixture_id": fixture_id,
            "activation_sha256": canonical_sha256(catalog.activation),
            "tool_calls": list(TOOL_ORDER), "acquired_evidence": [item.model_dump(mode="json") for item in evidence],
            "decision": decision.model_dump(mode="json"), "contract_pass": True,
            "first_pass_contract": True, "repair_used": 0, "failure_code": None}


def score_v3(run, *, catalog, rubric):
    score = {key: False for key in ("contract_pass", "observations_pass", "causes_pass", "completeness_pass", "joint_pass")}
    score.update(first_pass_contract=bool(run.get("first_pass_contract")), repair_used=run.get("repair_used", 0),
                 tool_call_count=len(run.get("tool_calls", [])), failure_code=run.get("failure_code"))
    try:
        if run["schema_version"] != "ops.diagnosis.run.v3" or run["activation_sha256"] != canonical_sha256(catalog.activation):
            raise ValueError("v3 envelope or activation mismatch")
        case = rubric["cases"][run["fixture_id"]]
        calls = run["tool_calls"]
        if len(calls) > 4 or len(calls) != len(run["acquired_evidence"]):
            raise ValueError("v3 acquisition budget mismatch")
        registry = ControlledScenarioRegistry(catalog=catalog, fixture_id=run["fixture_id"])
        evidence = [registry.execute(tool) for tool in calls]
        if [item.model_dump(mode="json") for item in evidence] != run["acquired_evidence"]:
            raise ValueError("v3 canonical evidence mismatch")
        score["completeness_pass"] = set(case["required_tools"]) <= set(calls)
        if not run["contract_pass"]:
            return score
        decision = AgentDecisionV3.model_validate(run["decision"])
        validate_decision_v3(decision, initial_evidence_ids=catalog.activation["evidence_ids"], acquired_evidence=evidence)
        supported = {item.hypothesis.value for item in decision.hypotheses if item.support_status.value == "SUPPORTED"}
        score.update(contract_pass=True,
            observations_pass={item.signal.value for item in decision.observations} == set(case["observations"]),
            causes_pass=supported == set(case["supported_causes"]),
            completeness_pass=set(case["required_tools"]) <= set(calls))
        score["joint_pass"] = all(score[key] for key in ("contract_pass", "observations_pass", "causes_pass", "completeness_pass"))
    except (ValueError, KeyError, TypeError) as exc:
        score.update(failure_code=getattr(exc, "code", "V3_SCORE_INPUT_INVALID"), first_pass_contract=False)
    return score


def summarize_v3(scores):
    return {"attempt_count": len(scores), **{key: sum(bool(item[key]) for item in scores)
        for key in ("first_pass_contract", "contract_pass", "observations_pass", "causes_pass", "completeness_pass", "joint_pass", "repair_used")},
        "tool_call_count": sum(item["tool_call_count"] for item in scores)}
