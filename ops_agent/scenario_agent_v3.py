"""Isolated v3 controlled investigation; no runtime writes or v2 score conversion."""

import json

from ops_agent.diagnosis_scenarios import ControlledScenarioRegistry
from ops_agent.diagnosis_repair_v3 import (
    REPAIR_INSTRUCTIONS, enforce_repair_scope, removable_observations, repair_policy_v3,
)
from ops_agent.diagnosis_v3_contract import decision_v3_contract
from ops_agent.diagnosis_v3_models import AgentDecisionV3
from ops_agent.diagnosis_v3_validator import validate_decision_v3
from ops_agent.evaluation_models import canonical_sha256
from ops_agent.investigation_gate_v3 import investigation_policy_v3, pending_investigation
from ops_agent.citation_selection_v3 import (
    citation_schema_v3, citation_selection_policy_v3, selectable_evidence_ids, validate_citation_selection,
)

LOOP_INSTRUCTIONS = """Investigate the frozen CORE_BACKLOG_PRESSURE=PRESENT incident.
Select at most one zero-argument allowlisted read-only tool per turn; never repeat a
tool. You have four tool calls, followed by a final decision. Stop earlier only when
the mandatory investigation gate is satisfied.
Use measured results to choose useful discriminating investigations. Tool failure is
missing information. Observe the supplied decision contract on every final turn.
Never request shell, kubectl, PromQL, URLs, writes, remediation or recovery judgments.
"""
REPAIR_TASK = "repair_ops_diagnosis_v3_output_contract_only"

# Common evidence semantics, not fixture answers or automatically generated output.
EXECUTION_GUIDANCE = """Use this common investigation/reporting procedure.
Investigate worker stage latency, PostgreSQL health AND worker replica status before
stopping in EVERY incident, even when one cause already has direct support. If fresh
OK replica status reports WORKER_CAPACITY_SHORTFALL, also inspect KEDA. Failed queries
count as attempted coverage and remain missing information. While required tools
remain, select one of the offered required tools. Other tools become optional only
after the gate is satisfied and if budget remains. No fixture label or expected
answer is provided.

Build observations independently from causes. Include EVERY acquired fresh OK
positive signal from this table exactly once, even if already cited by a hypothesis:
get_worker_stage_latency / ABOVE_SCENARIO_BASELINE -> WORKER_STAGE_ELEVATED
get_worker_replica_status / WORKER_CAPACITY_SHORTFALL -> WORKER_REPLICA_SHORTFALL
get_postgres_health / POSTGRES_PATH_DEGRADED -> POSTGRES_GUARDRAIL_DEGRADED
Do not include normal, unavailable or stale measurements as positive observations.

Signed causal citations must follow this exact common evidence-role table:
WORKER_CAPACITY_SHORTFALL_SUSPECTED:
 support = get_worker_replica_status / WORKER_CAPACITY_SHORTFALL
 conflict = get_worker_replica_status / WORKER_CAPACITY_AVAILABLE
POSTGRES_GUARDRAIL_PRESSURE_SUSPECTED:
 support = get_postgres_health / POSTGRES_PATH_DEGRADED
 conflict = get_postgres_health / POSTGRES_PATH_HEALTHY
The cited record must be fresh and OK. Stage elevation, initial lag and KEDA state
are NEVER signed support or conflict for either cause. A symptom plus a healthy
replica result does not form CONFLICTED evidence for replica shortfall.

Keep the report focused. Include supported candidates only when there is direct
support from this table. Optional unsupported hypotheses may be omitted; use
investigation_gaps to preserve uncertainty instead of enumerating every possibility.
Use CONFLICTED only with actual direct evidence on both sides for the SAME cause.
If no candidate has direct support, hypotheses may be [], conclusion must be ABSTAIN,
and investigation_gaps must describe the missing causal discriminator. Preserve all
measured observations in that case. Background IDs may go in context_evidence_ids;
never put a signed causal ID in that list. No provenance IDs may be invented.
"""
FINAL_AUDIT = """Before returning the final JSON, audit it against the common contract:
1. Every acquired fresh OK positive signal appears once in observations, including
   replica shortfall even when the same evidence already supports a hypothesis.
2. Every signed cause citation matches its exact tool/flag in the evidence-role table.
3. No stage/lag/KEDA record is signed cause support or conflict.
4. Supported candidates exist iff conclusion is SUPPORTED_CANDIDATES. Otherwise
   ABSTAIN, preserving observations and an explicit gap. Do not invent a conflict.
5. Cite only supplied IDs and keep context separate from signed causal citations.
The remaining tool budget is zero. Return only the decision JSON.
"""


def execution_contract_v3():
    contract = {"decision_contract": decision_v3_contract(),
                "loop_instructions": LOOP_INSTRUCTIONS,
                "execution_guidance": EXECUTION_GUIDANCE, "final_audit": FINAL_AUDIT,
                "repair_task": REPAIR_TASK, "repair_instructions": REPAIR_INSTRUCTIONS,
                "repair_policy": repair_policy_v3(),
                "investigation_policy": investigation_policy_v3(),
                "citation_selection_policy": citation_selection_policy_v3(),
                "max_tools": 4, "max_repairs": 1, "max_requests": 6}
    return {"identity": {"version": "ops.diagnosis.execution.v3.5",
                         "sha256": canonical_sha256(contract)}, **contract}


def request_v3(*, model, history, tools, allowed_evidence_ids, require_tool=False):
    payload = {"model": model, "store": False, "input": history,
               "reasoning": {"effort": "low"}, "max_output_tokens": 1600,
               "text": {"format": {"type": "json_schema", "name": "ops_diagnosis_decision_v3",
                                    "strict": True, "schema": citation_schema_v3(allowed_evidence_ids)}}}
    if tools:
        payload.update(tools=tools, tool_choice="required" if require_tool else "auto", parallel_tool_calls=False)
    return payload


def run_scenario_v3(*, catalog, fixture_id, client, model):
    """Always return an auditable executed attempt, including validation failures.

    Transport errors propagate to the pilot, which persists partial wire traces.
    """
    registry = ControlledScenarioRegistry(catalog=catalog, fixture_id=fixture_id)
    history = [{"role": "system", "content": LOOP_INSTRUCTIONS},
               {"role": "user", "content": json.dumps({"activation": catalog.activation,
                    "decision_contract": decision_v3_contract()["instructions"],
                    "execution_guidance": EXECUTION_GUIDANCE,
                    "investigation_policy": investigation_policy_v3(),
                    "citation_selection_policy": citation_selection_policy_v3()})}]
    run = {"schema_version": "ops.diagnosis.run.v3", "fixture_id": fixture_id,
           "activation_sha256": canonical_sha256(catalog.activation),
           "execution_identity": execution_contract_v3()["identity"],
           "acquired_evidence": [], "tool_calls": [], "decisions": [],
           "first_pass_contract": False, "repair_used": 0,
           "repair_scope_audit": None,
           "investigation_audit": [],
           "citation_selection_audit": [],
           "contract_pass": False, "decision": None, "failure_code": None}
    evidence = []
    original = None
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
        allowed_ids = selectable_evidence_ids(initial_evidence_ids=catalog.activation["evidence_ids"],
            acquired_evidence=evidence, original=original if run["repair_used"] else None)
        run["citation_selection_audit"].append({"turn": turn + 1, "repair": bool(run["repair_used"]),
            "allowed_evidence_ids": allowed_ids, "schema_sha256": canonical_sha256(citation_schema_v3(allowed_ids))})
        try:
            response = client.create(request_v3(model=model, history=history, tools=tools,
                allowed_evidence_ids=allowed_ids, require_tool=require_tool))
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
                    raise ValueError("tool was not offered this turn")
                if json.loads(call.get("arguments", "")) != {}:
                    raise ValueError("zero argument tools only")
                item = registry.execute(call["name"])
            except (ValueError, KeyError) as exc:
                run["failure_code"] = "V3_INVALID_TOOL_CALL"
                return run
            evidence.append(item)
            run["tool_calls"].append(call["name"])
            run["acquired_evidence"].append(item.model_dump(mode="json"))
            history.extend(item for item in response.output if item.get("type") in {"reasoning", "function_call", "message"})
            history.append({"type": "function_call_output", "call_id": call["call_id"],
                            "output": item.model_dump_json()})
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
            validate_citation_selection(decision, allowed_ids)
            if run["repair_used"]:
                run["repair_scope_audit"] = enforce_repair_scope(
                    original, decision.model_dump(mode="json"), acquired_evidence=evidence)
            validate_decision_v3(decision, initial_evidence_ids=catalog.activation["evidence_ids"],
                                 acquired_evidence=evidence)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            run["decisions"].append({"raw": raw, "contract_pass": False, "failure_code": code})
            if run["repair_used"] or not isinstance(raw, dict) or turn >= 5:
                run["failure_code"] = code
                return run
            original = raw
            run["repair_used"] = 1
            history.extend([{"role": "assistant", "content": response.output_text},
                            {"role": "user", "content": json.dumps({"task": REPAIR_TASK,
                                "instructions": REPAIR_INSTRUCTIONS, "validation_error_code": code,
                                "removable_observations": removable_observations(raw, evidence),
                                "existing_result": raw})}])
            continue
        run["decisions"].append({"raw": raw, "contract_pass": True, "failure_code": None})
        run.update(contract_pass=True, first_pass_contract=not bool(run["repair_used"]),
                   decision=decision.model_dump(mode="json"))
        return run
    run["failure_code"] = "V3_REQUEST_BUDGET_EXHAUSTED"
    return run
