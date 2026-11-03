"""Isolated runtime loop using v3.5 policies; historical Scenario Lab stays frozen."""

import json

from ops_agent.scenario_agent_v3 import (
    LOOP_INSTRUCTIONS, EXECUTION_GUIDANCE, FINAL_AUDIT, REPAIR_TASK,
    REPAIR_INSTRUCTIONS, request_v3, execution_contract_v3,
)
from ops_agent.citation_selection_v3 import (
    selectable_evidence_ids, citation_schema_v3, validate_citation_selection,
    citation_selection_policy_v3,
)
from ops_agent.diagnosis_repair_v3 import enforce_repair_scope, removable_observations
from ops_agent.diagnosis_v3_contract import decision_v3_contract
from ops_agent.diagnosis_v3_models import AgentDecisionV3
from ops_agent.diagnosis_v3_validator import validate_decision_v3
from ops_agent.evaluation_models import canonical_sha256
from ops_agent.investigation_gate_v3 import pending_investigation, investigation_policy_v3
from ops_agent.runtime_evidence_v3 import ADAPTER_VERSION


def run_runtime_v3(*, registry, client, model):
    """Four tools, one tool-free repair, six requests; no fixture catalog or rubric."""
    activation = registry.activation
    history = [{"role": "system", "content": LOOP_INSTRUCTIONS},
        {"role": "user", "content": json.dumps({"activation": activation,
            "decision_contract": decision_v3_contract()["instructions"],
            "execution_guidance": EXECUTION_GUIDANCE,
            "investigation_policy": investigation_policy_v3(),
            "citation_selection_policy": citation_selection_policy_v3()})}]
    run = {"schema_version": "ops.diagnosis.runtime-run.v1", "acquisition_mode": "FROZEN_PROJECTED",
        "adapter_version": ADAPTER_VERSION, "activation_sha256": canonical_sha256(activation),
        "execution_identity": execution_contract_v3()["identity"], "acquired_evidence": [],
        "tool_calls": [], "decisions": [], "first_pass_contract": False, "repair_used": 0,
        "repair_scope_audit": None, "investigation_audit": [], "citation_selection_audit": [],
        "contract_pass": False, "decision": None, "failure_code": None}
    evidence, original = [], None
    for turn in range(6):
        tools = registry.function_tools() if len(evidence) < 4 and not run["repair_used"] else []
        pending = pending_investigation(evidence)
        require_tool = bool(pending) and not run["repair_used"]
        if require_tool:
            tools = [tool for tool in tools if tool["name"] in pending]
            if len(pending) > 4 - len(evidence) or {tool["name"] for tool in tools} != set(pending):
                run["failure_code"] = "V3_MINIMUM_INVESTIGATION_BUDGET_EXHAUSTED"
                return run
        run["investigation_audit"].append({"turn": turn + 1, "pending_tools": pending,
            "offered_tools": [tool["name"] for tool in tools],
            "tool_choice": "required" if require_tool else ("auto" if tools else "none")})
        if len(evidence) == 4 and not run["repair_used"]:
            history.append({"role": "user", "content": FINAL_AUDIT})
        allowed = selectable_evidence_ids(initial_evidence_ids=activation["evidence_ids"],
            acquired_evidence=evidence, original=original if run["repair_used"] else None)
        run["citation_selection_audit"].append({"turn": turn + 1, "repair": bool(run["repair_used"]),
            "allowed_evidence_ids": allowed, "schema_sha256": canonical_sha256(citation_schema_v3(allowed))})
        try:
            response = client.create(request_v3(model=model, history=history, tools=tools,
                allowed_evidence_ids=allowed, require_tool=require_tool))
        except (RuntimeError, TimeoutError) as exc:
            exc.partial_run = run
            raise
        calls = [item for item in response.output if item.get("type") == "function_call"]
        if calls:
            if len(calls) != 1 or not tools:
                run["failure_code"] = "V3_TOOL_BUDGET_OR_PARALLEL_VIOLATION"
                return run
            call = calls[0]
            try:
                if call.get("name") not in {tool["name"] for tool in tools}:
                    raise ValueError("tool not offered")
                if json.loads(call.get("arguments", "")) != {}:
                    raise ValueError("zero argument tools only")
                item = registry.execute(call["name"])
            except (ValueError, KeyError):
                run["failure_code"] = "V3_INVALID_TOOL_CALL"
                return run
            evidence.append(item)
            run["tool_calls"].append(call["name"])
            run["acquired_evidence"].append(item.model_dump(mode="json"))
            history.extend(item for item in response.output if item.get("type") in {"reasoning", "function_call", "message"})
            history.append({"type": "function_call_output", "call_id": call["call_id"], "output": item.model_dump_json()})
            continue
        raw = None
        if pending:
            try:
                raw = json.loads(response.output_text or "")
            except ValueError:
                pass
            run["failure_code"] = "V3_MINIMUM_INVESTIGATION_INCOMPLETE"
            run["decisions"].append({"raw": raw, "contract_pass": False, "failure_code": run["failure_code"]})
            return run
        try:
            raw = json.loads(response.output_text or "")
            decision = AgentDecisionV3.model_validate(raw)
            validate_citation_selection(decision, allowed)
            if run["repair_used"]:
                run["repair_scope_audit"] = enforce_repair_scope(original, decision.model_dump(mode="json"), acquired_evidence=evidence)
            validate_decision_v3(decision, initial_evidence_ids=activation["evidence_ids"], acquired_evidence=evidence)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            run["decisions"].append({"raw": raw, "contract_pass": False, "failure_code": code})
            if run["repair_used"] or not isinstance(raw, dict) or turn >= 5:
                run["failure_code"] = code
                return run
            original, run["repair_used"] = raw, 1
            history.extend([{"role": "assistant", "content": response.output_text},
                {"role": "user", "content": json.dumps({"task": REPAIR_TASK,
                    "instructions": REPAIR_INSTRUCTIONS, "validation_error_code": code,
                    "removable_observations": removable_observations(raw, evidence), "existing_result": raw})}])
            continue
        run["decisions"].append({"raw": raw, "contract_pass": True, "failure_code": None})
        run.update(contract_pass=True, first_pass_contract=not bool(run["repair_used"]), decision=decision.model_dump(mode="json"))
        return run
    run["failure_code"] = "V3_REQUEST_BUDGET_EXHAUSTED"
    return run
