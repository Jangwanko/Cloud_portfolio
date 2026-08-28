"""Bounded diagnosis loop for controlled Scenario Lab acquisitions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from pydantic import ValidationError

from ops_agent.diagnosis_agent import DiagnosisModelClient, ModelResponse
from ops_agent.diagnosis_models import (
    DiagnosisStep,
    DiagnosisStopReason,
    DiagnosisUsage,
    DiagnosisValidationAttempt,
    HypothesisSupportStatus,
)
from ops_agent.diagnosis_scenarios import (
    ControlledScenarioRegistry,
    ScenarioCatalog,
    ScenarioDefinition,
)
from ops_agent.diagnosis_tools import TOOL_BY_ID
from ops_agent.diagnosis_v2_models import (
    AgentDecisionV2,
    BranchEvaluation,
    DiagnosisAcquisitionMode,
    DiagnosisContextV2,
    DiagnosisEvidenceV2,
    DiagnosisPolicyV2,
    DiagnosisRunV2,
    HypothesisNameV2,
    HypothesisResultV2,
)
from ops_agent.diagnosis_v2_validator import DiagnosisOutputValidatorV2
from ops_agent.diagnosis_validator import DiagnosisValidationError
from ops_agent.evaluation_models import ConditionState, canonical_sha256


SCENARIO_SYSTEM_INSTRUCTIONS = """You are the bounded Evidence-grounded Diagnosis Agent.
The deterministic evaluator already established CORE_BACKLOG_PRESSURE=PRESENT from an
immutable activation. Do not re-evaluate the condition. Select at most one zero-argument
allowlisted read-only tool per turn and do not repeat tools. Controlled scenario tools
return normalized evidence with explicit provenance. Use observations to choose the next
investigation. Never declare recovery, recommend remediation, or request shell, kubectl,
PromQL, URLs, writes, scaling, deletion, replay, sync, or restore. Cite only supplied
evidence IDs. Return only the required structured JSON when the investigation stops.
"""


_REPAIRABLE_CODES = {
    "FORBIDDEN_DIAGNOSIS_CLAIM",
    "UNKNOWN_EVIDENCE_CITATION",
    "SUPPORTED_REQUIRES_SUPPORTING_CITATION",
    "CONFLICTED_REQUIRES_BOTH_CITATION_SIDES",
    "INSUFFICIENT_REQUIRES_EVIDENCE_GAP",
    "REBALANCE_TELEMETRY_UNAVAILABLE",
    "REBALANCE_CITATIONS_FORBIDDEN",
    "REBALANCE_GAP_REQUIRED",
    "SUFFICIENT_STOP_REQUIRES_SUPPORTED_CAUSE",
    "INSUFFICIENT_STOP_CONFLICTS_WITH_SUPPORTED_CAUSE",
    "STEP_BUDGET_STOP_REQUIRES_FULL_BUDGET",
}


def _decision_schema_v2() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "hypotheses": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "hypothesis": {
                            "type": "string",
                            "enum": [item.value for item in HypothesisNameV2],
                        },
                        "support_status": {
                            "type": "string",
                            "enum": [item.value for item in HypothesisSupportStatus],
                        },
                        "reason_codes": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 16,
                            "items": {"type": "string", "maxLength": 128},
                        },
                        "supporting_evidence_ids": {
                            "type": "array",
                            "maxItems": 128,
                            "items": {"type": "string", "maxLength": 256},
                        },
                        "conflicting_evidence_ids": {
                            "type": "array",
                            "maxItems": 128,
                            "items": {"type": "string", "maxLength": 256},
                        },
                        "evidence_gaps": {
                            "type": "array",
                            "maxItems": 32,
                            "items": {"type": "string", "maxLength": 128},
                        },
                    },
                    "required": [
                        "hypothesis",
                        "support_status",
                        "reason_codes",
                        "supporting_evidence_ids",
                        "conflicting_evidence_ids",
                        "evidence_gaps",
                    ],
                    "additionalProperties": False,
                },
            },
            "stop_reason": {
                "type": "string",
                "enum": [
                    DiagnosisStopReason.SUFFICIENT_EVIDENCE.value,
                    DiagnosisStopReason.INSUFFICIENT_EVIDENCE.value,
                    DiagnosisStopReason.NO_USEFUL_TOOL_REMAINING.value,
                    DiagnosisStopReason.STEP_BUDGET_EXHAUSTED.value,
                ],
            },
        },
        "required": ["hypotheses", "stop_reason"],
        "additionalProperties": False,
    }


def _request_payload(
    *,
    policy: DiagnosisPolicyV2,
    history: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": policy.model,
        "store": False,
        "input": history,
        "reasoning": {"effort": policy.reasoning_effort},
        "max_output_tokens": policy.max_output_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "ops_diagnosis_agent_decision_v2",
                "strict": True,
                "schema": _decision_schema_v2(),
            }
        },
    }
    if tools:
        payload.update(
            {
                "tools": tools,
                "tool_choice": "auto",
                "parallel_tool_calls": False,
            }
        )
    return payload


def _safe_output_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item.get("type") in {"reasoning", "function_call", "message"}
    ]


def _fallback(reason: DiagnosisStopReason, gap: str) -> AgentDecisionV2:
    return AgentDecisionV2(
        hypotheses=[
            HypothesisResultV2(
                hypothesis=HypothesisNameV2.INSUFFICIENT_EVIDENCE,
                support_status=HypothesisSupportStatus.INSUFFICIENT,
                reason_codes=[reason.value.upper()],
                evidence_gaps=[gap],
            )
        ],
        stop_reason=reason,
    )


def _branch_evaluations(
    scenario: ScenarioDefinition,
    steps: list[DiagnosisStep],
) -> list[BranchEvaluation]:
    called = [item.tool_id for item in steps]
    results = []
    for expectation in scenario.branch_expectations:
        try:
            index = called.index(expectation.after_tool_id)
        except ValueError:
            selected = None
            result = "NOT_EVALUATED"
        else:
            selected = called[index + 1] if index + 1 < len(called) else None
            result = (
                "PASS"
                if (
                    selected in expectation.expected_next_tools
                    or (selected is None and not expectation.expected_next_tools)
                )
                else "FAIL"
            )
        results.append(
            BranchEvaluation(
                after_tool_id=expectation.after_tool_id,
                expected_next_tools=expectation.expected_next_tools,
                selected_next_tool=selected,
                result=result,
            )
        )
    return results


def _repair_history(
    *,
    history: list[dict[str, Any]],
    decision: AgentDecisionV2,
    error: DiagnosisValidationError,
    allowed_evidence_ids: list[str],
) -> list[dict[str, Any]]:
    return [
        *history,
        {"role": "assistant", "content": decision.model_dump_json()},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "repair_ops_diagnosis_v2_output_contract_only",
                    "validation_error": error.as_dict(),
                    "existing_structured_result": decision.model_dump(mode="json"),
                    "allowed_evidence_ids": sorted(set(allowed_evidence_ids)),
                    "constraints": [
                        "NO_TOOL_CALLS",
                        "NO_NEW_EVIDENCE",
                        "NO_CONDITION_REEVALUATION",
                        "NO_RECOVERY_JUDGMENT",
                        "NO_REMEDIATION",
                        "NO_FABRICATED_EVIDENCE_IDS",
                    ],
                },
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        },
    ]


def run_scenario_diagnosis(
    *,
    catalog: ScenarioCatalog,
    fixture_id: str,
    client: DiagnosisModelClient,
    policy: DiagnosisPolicyV2,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> DiagnosisRunV2:
    started_at = started_at or datetime.now(timezone.utc)
    if started_at.utcoffset() is None:
        raise ValueError("scenario diagnosis start must be timezone-aware")
    registry = ControlledScenarioRegistry(catalog=catalog, fixture_id=fixture_id)
    scenario = registry.scenario
    activation = catalog.activation
    initial_ids = list(activation.get("evidence_ids", []))
    if not initial_ids:
        raise ValueError("scenario activation requires grounded evidence IDs")
    history: list[dict[str, Any]] = [
        {"role": "system", "content": SCENARIO_SYSTEM_INSTRUCTIONS},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "condition_evaluation_id": activation["condition_evaluation_id"],
                    "input_conditions": {"CORE_BACKLOG_PRESSURE": "PRESENT"},
                    "activation_facts": activation["facts"],
                    "initial_evidence_ids": initial_ids,
                    "acquisition_mode": "CONTROLLED_SCENARIO",
                    "scenario_fixture_id": scenario.fixture_id,
                    "available_tools": list(registry.tool_ids),
                    "budgets": {
                        "max_steps": policy.max_steps,
                        "max_tool_calls": policy.max_tool_calls,
                    },
                },
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        },
    ]
    steps: list[DiagnosisStep] = []
    evidence: list[DiagnosisEvidenceV2] = []
    response_ids: list[str] = []
    input_tokens = output_tokens = total_tokens = 0
    decision: AgentDecisionV2 | None = None

    while decision is None:
        tools = registry.function_tools() if len(steps) < policy.max_tool_calls else []
        response = client.create(_request_payload(policy=policy, history=history, tools=tools))
        response_ids.append(response.response_id)
        input_tokens += response.input_tokens
        output_tokens += response.output_tokens
        total_tokens += response.total_tokens
        calls = [item for item in response.output if item.get("type") == "function_call"]
        if calls:
            if len(calls) != 1:
                raise ValueError("Diagnosis Agent must request exactly one tool per step")
            if len(steps) >= policy.max_steps or not tools:
                decision = _fallback(
                    DiagnosisStopReason.STEP_BUDGET_EXHAUSTED,
                    "STEP_BUDGET_EXHAUSTED_BEFORE_SUFFICIENT_EVIDENCE",
                )
                break
            call = calls[0]
            tool_id = str(call.get("name", ""))
            try:
                arguments = json.loads(str(call.get("arguments", "{}")))
            except json.JSONDecodeError as exc:
                raise ValueError("tool arguments were not valid JSON") from exc
            if arguments != {}:
                raise ValueError("diagnosis tools do not accept model-controlled arguments")
            requested_at = (
                started_at
                if policy.model_mode == "recorded"
                else datetime.now(timezone.utc)
            )
            result = registry.execute(tool_id)
            evidence.append(result)
            steps.append(
                DiagnosisStep(
                    step=len(steps) + 1,
                    tool_id=tool_id,
                    reason_code=TOOL_BY_ID[tool_id].reason_code,
                    requested_at=requested_at,
                    returned_evidence_ids=[result.evidence_id],
                )
            )
            history.extend(_safe_output_items(response.output))
            history.append(
                {
                    "type": "function_call_output",
                    "call_id": call.get("call_id"),
                    "output": json.dumps(
                        result.model_dump(mode="json"),
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ),
                }
            )
            continue
        if not response.output_text:
            raise ValueError("Diagnosis Agent returned neither a tool call nor output")
        try:
            decision = AgentDecisionV2.model_validate_json(response.output_text)
        except ValidationError as exc:
            raise ValueError("Diagnosis Agent v2 output failed schema validation") from exc

    validator = DiagnosisOutputValidatorV2(allowlisted_tool_ids=set(registry.tool_ids))
    validation_attempts: list[DiagnosisValidationAttempt] = []
    output_repairs_used = 0
    try:
        validation = validator.validate(
            decision=decision,
            policy=policy,
            initial_evidence_ids=initial_ids,
            additional_evidence=evidence,
            steps=steps,
        )
        validation_attempts.append(
            DiagnosisValidationAttempt(
                attempt=1,
                phase="initial",
                result="VALID",
                response_id=response_ids[-1],
            )
        )
    except DiagnosisValidationError as initial_error:
        if policy.max_output_repairs < 1 or initial_error.code not in _REPAIRABLE_CODES:
            raise
        validation_attempts.append(
            DiagnosisValidationAttempt(
                attempt=1,
                phase="initial",
                result="INVALID",
                response_id=response_ids[-1],
                error_code=initial_error.code,
                error_message=initial_error.message,
            )
        )
        repair = client.create(
            _request_payload(
                policy=policy,
                history=_repair_history(
                    history=history,
                    decision=decision,
                    error=initial_error,
                    allowed_evidence_ids=[
                        *initial_ids,
                        *(item.evidence_id for item in evidence),
                    ],
                ),
                tools=[],
            )
        )
        output_repairs_used = 1
        response_ids.append(repair.response_id)
        input_tokens += repair.input_tokens
        output_tokens += repair.output_tokens
        total_tokens += repair.total_tokens
        if any(item.get("type") == "function_call" for item in repair.output):
            raise DiagnosisValidationError(
                "REPAIR_TOOL_CALL_FORBIDDEN",
                "output repair returned a forbidden tool call",
            )
        if not repair.output_text:
            raise DiagnosisValidationError(
                "REPAIR_STRUCTURED_OUTPUT_MISSING",
                "output repair returned no structured result",
            )
        try:
            decision = AgentDecisionV2.model_validate_json(repair.output_text)
        except ValidationError as exc:
            raise DiagnosisValidationError(
                "REPAIR_SCHEMA_INVALID",
                "output repair failed structured schema validation",
            ) from exc
        validation = validator.validate(
            decision=decision,
            policy=policy,
            initial_evidence_ids=initial_ids,
            additional_evidence=evidence,
            steps=steps,
        )
        validation_attempts.append(
            DiagnosisValidationAttempt(
                attempt=2,
                phase="repair",
                result="VALID",
                response_id=repair.response_id,
            )
        )

    completed_at = completed_at or datetime.now(timezone.utc)
    activation_digests = list(activation["source_bundle_digests"])
    session_payload = {
        "condition_evaluation_id": activation["condition_evaluation_id"],
        "fixture_id": scenario.fixture_id,
        "fixture_digest": scenario.fixture_digest,
        "acquisition_mode": "CONTROLLED_SCENARIO",
        "model": policy.model,
        "model_mode": policy.model_mode,
        "started_at": started_at.isoformat(),
    }
    session_id = canonical_sha256(session_payload)
    context = DiagnosisContextV2(
        profile=activation["profile"],
        cluster_context=activation["cluster_context"],
        namespace=activation["namespace"],
        topic=activation["topic"],
        consumer_group=activation["consumer_group"],
        activation_source_bundle_digests=activation_digests,
    )
    branches = _branch_evaluations(scenario, steps)
    identity = {
        "condition_evaluation_id": activation["condition_evaluation_id"],
        "investigation_session_id": session_id,
        "acquisition_mode": "CONTROLLED_SCENARIO",
        "context": context.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
        "initial_evidence_ids": initial_ids,
        "additional_evidence": [item.model_dump(mode="json") for item in evidence],
        "steps": [
            {
                "step": item.step,
                "tool_id": item.tool_id,
                "reason_code": item.reason_code,
                "returned_evidence_ids": item.returned_evidence_ids,
            }
            for item in steps
        ],
        "hypotheses": [item.model_dump(mode="json") for item in decision.hypotheses],
        "stop_reason": decision.stop_reason.value,
        "branch_evaluations": [item.model_dump(mode="json") for item in branches],
    }
    return DiagnosisRunV2(
        diagnosis_id=canonical_sha256(identity),
        incident_id=activation["incident_id"],
        condition_evaluation_id=activation["condition_evaluation_id"],
        investigation_session_id=session_id,
        acquisition_mode=DiagnosisAcquisitionMode.CONTROLLED_SCENARIO,
        context=context,
        policy=policy,
        input_conditions={"CORE_BACKLOG_PRESSURE": ConditionState.PRESENT},
        initial_evidence_ids=initial_ids,
        additional_evidence=evidence,
        steps=steps,
        hypotheses=decision.hypotheses,
        stop_reason=decision.stop_reason,
        steps_used=len(steps),
        started_at=started_at,
        completed_at=completed_at,
        model_response_ids=response_ids,
        model_turns=len(response_ids),
        usage=DiagnosisUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            api_requests=len(response_ids) if policy.model_mode == "live" else 0,
        ),
        validation=validation,
        output_repairs_used=output_repairs_used,
        validation_attempts=validation_attempts,
        branch_evaluations=branches,
    )


class RecordedBranchModelClient:
    """Deterministic recorded path used by offline tests and the local replay UI."""

    def __init__(self) -> None:
        self.turns = 0

    def create(self, payload: dict[str, Any]) -> ModelResponse:
        self.turns += 1
        evidence = self._evidence_from_history(payload["input"])
        tool_id = self._next_tool(evidence)
        if tool_id is not None:
            return ModelResponse(
                response_id=f"recorded-branch-turn-{self.turns}",
                output=[
                    {
                        "type": "function_call",
                        "call_id": f"recorded-call-{self.turns}",
                        "name": tool_id,
                        "arguments": "{}",
                    }
                ],
                output_text=None,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
            )
        decision = self._decision(evidence)
        output_text = decision.model_dump_json()
        return ModelResponse(
            response_id=f"recorded-branch-turn-{self.turns}",
            output=[
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": output_text}],
                }
            ],
            output_text=output_text,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )

    @staticmethod
    def _evidence_from_history(history: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result = {}
        for item in history:
            if item.get("type") != "function_call_output":
                continue
            parsed = json.loads(item["output"])
            result[parsed["tool_id"]] = parsed
        return result

    @staticmethod
    def _next_tool(evidence: dict[str, dict[str, Any]]) -> str | None:
        if "get_worker_stage_latency" not in evidence:
            return "get_worker_stage_latency"
        stage = evidence["get_worker_stage_latency"]["summary"]
        if stage.get("semantic_flag") == "WITHIN_SCENARIO_BASELINE":
            if "get_worker_replica_status" not in evidence:
                return "get_worker_replica_status"
            replica = evidence["get_worker_replica_status"]["summary"]
            if (
                replica.get("semantic_flag") == "WORKER_CAPACITY_SHORTFALL"
                and "get_keda_status" not in evidence
            ):
                return "get_keda_status"
            return None
        if "get_postgres_health" not in evidence:
            return "get_postgres_health"
        postgres = evidence["get_postgres_health"]
        if postgres["status"] == "UNAVAILABLE":
            return None
        if postgres["summary"].get("semantic_flag") == "POSTGRES_PATH_DEGRADED":
            return None
        if "get_worker_replica_status" not in evidence:
            return "get_worker_replica_status"
        return None

    @staticmethod
    def _decision(evidence: dict[str, dict[str, Any]]) -> AgentDecisionV2:
        def evidence_id(tool_id: str) -> str:
            return evidence[tool_id]["evidence_id"]

        stage = evidence["get_worker_stage_latency"]
        if stage["summary"].get("semantic_flag") == "WITHIN_SCENARIO_BASELINE":
            replica = evidence["get_worker_replica_status"]
            if replica["summary"].get("semantic_flag") == "WORKER_CAPACITY_SHORTFALL":
                return AgentDecisionV2(
                    hypotheses=[
                        HypothesisResultV2(
                            hypothesis=HypothesisNameV2.WORKER_CAPACITY_SHORTFALL_SUSPECTED,
                            support_status=HypothesisSupportStatus.SUPPORTED,
                            reason_codes=["WORKER_AVAILABLE_BELOW_DESIRED"],
                            supporting_evidence_ids=[
                                evidence_id("get_worker_replica_status"),
                                evidence_id("get_keda_status"),
                            ],
                        )
                    ],
                    stop_reason=DiagnosisStopReason.SUFFICIENT_EVIDENCE,
                )
            return _fallback(
                DiagnosisStopReason.INSUFFICIENT_EVIDENCE,
                "NO_WORKER_PATH_OR_CAPACITY_PRESSURE_SIGNAL",
            )
        postgres = evidence.get("get_postgres_health")
        if postgres and postgres["status"] == "UNAVAILABLE":
            return _fallback(
                DiagnosisStopReason.INSUFFICIENT_EVIDENCE,
                "POSTGRES_HEALTH_TELEMETRY_UNAVAILABLE",
            )
        if postgres and postgres["summary"].get("semantic_flag") == "POSTGRES_PATH_DEGRADED":
            return AgentDecisionV2(
                hypotheses=[
                    HypothesisResultV2(
                        hypothesis=HypothesisNameV2.POSTGRES_PATH_DEGRADED_SUSPECTED,
                        support_status=HypothesisSupportStatus.SUPPORTED,
                        reason_codes=["POSTGRES_PATH_GUARDRAIL_BREACH"],
                        supporting_evidence_ids=[
                            evidence_id("get_worker_stage_latency"),
                            evidence_id("get_postgres_health"),
                        ],
                    )
                ],
                stop_reason=DiagnosisStopReason.SUFFICIENT_EVIDENCE,
            )
        return AgentDecisionV2(
            hypotheses=[
                HypothesisResultV2(
                    hypothesis=HypothesisNameV2.WORKER_PATH_PRESSURE_SUSPECTED,
                    support_status=HypothesisSupportStatus.SUPPORTED,
                    reason_codes=["WORKER_STAGE_ABOVE_SCENARIO_BASELINE"],
                    supporting_evidence_ids=[
                        evidence_id("get_worker_stage_latency"),
                    ],
                    evidence_gaps=["COMMIT_LATENCY_UNAVAILABLE"],
                )
            ],
            stop_reason=DiagnosisStopReason.SUFFICIENT_EVIDENCE,
        )


__all__ = ["RecordedBranchModelClient", "run_scenario_diagnosis"]
