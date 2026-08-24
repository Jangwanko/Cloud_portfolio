"""Single bounded Evidence-grounded Diagnosis Agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Protocol
from urllib import error, request

from pydantic import ValidationError

from ops_agent.diagnosis_models import (
    AgentDecision,
    DiagnosisContext,
    DiagnosisEvidence,
    DiagnosisPolicy,
    DiagnosisRun,
    DiagnosisStep,
    DiagnosisStopReason,
    DiagnosisUsage,
    DiagnosisValidationAttempt,
    HypothesisName,
    HypothesisResult,
    HypothesisSupportStatus,
    diagnosis_id,
)
from ops_agent.diagnosis_tools import DiagnosisToolRegistry, TOOL_BY_ID
from ops_agent.diagnosis_validator import (
    DiagnosisOutputValidator,
    DiagnosisValidationError,
)
from ops_agent.evaluation_models import ConditionName, ConditionState
from ops_agent.models import EvidenceBundle
from ops_agent.sequence_evaluator import evaluate_bundle_sequence
from ops_agent.sequence_models import SequenceConditionEvaluation


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


SYSTEM_INSTRUCTIONS = """You are the single Evidence-grounded Diagnosis Agent.
The deterministic evaluator has already established CORE_BACKLOG_PRESSURE=PRESENT.
Never re-evaluate that condition, declare recovery, recommend remediation, or request
shell, kubectl, PromQL, URLs, filesystem access, writes, scaling, deletion, replay, sync,
or restore. Select at most one allowlisted read-only tool per turn. Do not repeat tools.
Use only evidence IDs present in the input or returned by tools. A hypothesis is
SUPPORTED only when cited normalized evidence directly supports the suspicion.
Preserve conflicting citations and explicit gaps. Consumer rebalance telemetry is
UNAVAILABLE, so CONSUMER_REBALANCE_SUSPECTED must remain INSUFFICIENT with gap
CONSUMER_REBALANCE_TELEMETRY_UNAVAILABLE. Output only the required JSON schema.
"""


REPAIR_INSTRUCTIONS = """Repair only the supplied structured diagnosis result so it
passes the existing semantic output contract. Do not re-diagnose the incident. Do not
request tools, add evidence, change the deterministic condition, declare recovery,
recommend remediation, or introduce evidence IDs outside allowed_evidence_ids. A
CONFLICTED hypothesis requires at least one real supporting citation and at least one
real conflicting citation. If either side is not present in the allowed evidence, do
not fabricate it: use SUPPORTED only when existing supporting evidence is sufficient
and no conflict is claimed; otherwise use INSUFFICIENT and record the missing evidence
in evidence_gaps. Return only the required JSON schema.
"""


_REPAIRABLE_VALIDATION_CODES = {
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


def _agent_decision_schema() -> dict[str, Any]:
    hypothesis_names = [item.value for item in HypothesisName]
    support_statuses = [item.value for item in HypothesisSupportStatus]
    stop_reasons = [
        DiagnosisStopReason.SUFFICIENT_EVIDENCE.value,
        DiagnosisStopReason.INSUFFICIENT_EVIDENCE.value,
        DiagnosisStopReason.NO_USEFUL_TOOL_REMAINING.value,
        DiagnosisStopReason.STEP_BUDGET_EXHAUSTED.value,
    ]
    return {
        "type": "object",
        "properties": {
            "hypotheses": {
                "type": "array",
                "minItems": 1,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "hypothesis": {"type": "string", "enum": hypothesis_names},
                        "support_status": {"type": "string", "enum": support_statuses},
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
            "stop_reason": {"type": "string", "enum": stop_reasons},
        },
        "required": ["hypotheses", "stop_reason"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class ModelResponse:
    response_id: str
    output: list[dict[str, Any]]
    output_text: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int


class DiagnosisModelClient(Protocol):
    def create(self, payload: dict[str, Any]) -> ModelResponse: ...


class DiagnosisOutputContractFailure(RuntimeError):
    classification = "validation_failure"

    def __init__(
        self,
        *,
        initial_error: DiagnosisValidationError,
        final_error: DiagnosisValidationError | None = None,
    ) -> None:
        super().__init__(self.classification)
        self.initial_error = initial_error.as_dict()
        self.final_error = final_error.as_dict() if final_error is not None else None


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class OpenAIResponsesClient:
    def __init__(self, *, api_key: str, timeout_seconds: float, max_retries: int) -> None:
        if not api_key.startswith("sk-"):
            raise ValueError("OPENAI_API_KEY is unavailable or malformed")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())

    def create(self, payload: dict[str, Any]) -> ModelResponse:
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
        req = request.Request(
            OPENAI_RESPONSES_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                with self._opener.open(req, timeout=self._timeout_seconds) as response:
                    raw = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise ValueError("OpenAI response exceeded the 4 MiB limit")
                return _parse_model_response(json.loads(raw.decode("utf-8")))
            except error.HTTPError as exc:
                status = int(exc.code)
                code = None
                error_type = None
                try:
                    error_body = exc.read(64 * 1024 + 1)
                    if len(error_body) <= 64 * 1024:
                        error_payload = json.loads(error_body.decode("utf-8"))
                        detail = error_payload.get("error", {})
                        if isinstance(detail, dict):
                            raw_code = detail.get("code")
                            raw_type = detail.get("type")
                            if isinstance(raw_code, str) and len(raw_code) <= 128:
                                code = raw_code
                            if isinstance(raw_type, str) and len(raw_type) <= 128:
                                error_type = raw_type
                except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
                    pass
                suffix = "/".join(value for value in (code, error_type) if value)
                last_error = RuntimeError(
                    f"OpenAI Responses API HTTP {status}"
                    + (f" ({suffix})" if suffix else "")
                )
                if code == "insufficient_quota":
                    break
                if status not in {408, 409, 429, 500, 502, 503, 504}:
                    break
            except (error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                last_error = RuntimeError(f"OpenAI Responses API failure: {type(exc).__name__}")
            if attempt < self._max_retries:
                time.sleep(min(1.0 * (attempt + 1), 2.0))
        assert last_error is not None
        raise last_error


def _parse_model_response(value: dict[str, Any]) -> ModelResponse:
    output = value.get("output")
    if not isinstance(output, list):
        raise ValueError("OpenAI response lacks output items")
    output_text: str | None = None
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                output_text = content.get("text")
    usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    return ModelResponse(
        response_id=str(value.get("id", "")),
        output=[item for item in output if isinstance(item, dict)],
        output_text=output_text,
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        total_tokens=int(usage.get("total_tokens", 0) or 0),
    )


def _read_env_value(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("env file must be a regular non-symlink file")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def load_openai_configuration(workspace: Path) -> tuple[str, str]:
    workspace = workspace.resolve()
    env_path = (workspace / ".env.local").resolve()
    if env_path.parent != workspace:
        raise ValueError(".env.local resolved outside the workspace")
    api_key = os.getenv("OPENAI_API_KEY") or _read_env_value(env_path, "OPENAI_API_KEY")
    model = (
        os.getenv("OPENAI_MODEL")
        or _read_env_value(env_path, "OPENAI_MODEL")
        or DEFAULT_OPENAI_MODEL
    )
    if not api_key:
        raise ValueError("OPENAI_API_KEY is required for an explicit live diagnosis")
    if len(model) > 128 or not model.startswith("gpt-"):
        raise ValueError("OPENAI_MODEL is not an allowed OpenAI model identifier")
    return api_key, model


def _initial_evidence(
    evaluation: SequenceConditionEvaluation,
    bundles: list[EvidenceBundle],
) -> tuple[list[str], list[dict[str, Any]]]:
    core = evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
    ids = list(dict.fromkeys(core.evidence_ids))
    unavailable = []
    windows = core.facts.get("matched_activation_windows")
    indexes = (
        windows[0]
        if isinstance(windows, list) and windows and isinstance(windows[0], list)
        else list(range(len(bundles)))
    )
    for index in indexes:
        if not isinstance(index, int) or not 0 <= index < len(bundles):
            continue
        bundle = bundles[index]
        for item in bundle.evidence:
            if item.metric.name != "consumer_rebalance_event":
                continue
            ids.append(item.evidence_id)
            unavailable.append(
                {
                    "evidence_id": item.evidence_id,
                    "status": item.status.value,
                    "semantic_type": item.semantic.type,
                    "notes": item.semantic.notes,
                }
            )
    return list(dict.fromkeys(ids)), unavailable


def _activation_facts(core_facts: dict[str, Any]) -> dict[str, Any]:
    windows = core_facts.get("matched_activation_windows")
    measurements = core_facts.get("capture_measurements")
    selected: list[Any] = []
    if (
        isinstance(windows, list)
        and windows
        and isinstance(windows[0], list)
        and isinstance(measurements, list)
    ):
        selected = [
            measurements[index]
            for index in windows[0]
            if isinstance(index, int) and 0 <= index < len(measurements)
        ]
    return {
        "activation_policy": core_facts.get("activation_policy"),
        "matched_activation_windows": windows,
        "matched_capture_measurements": selected,
        "recovery_or_clearing_evaluated": False,
    }


def _request_payload(
    *,
    policy: DiagnosisPolicy,
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
                "name": "ops_diagnosis_agent_decision_v1",
                "strict": True,
                "schema": _agent_decision_schema(),
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


def _repair_history(
    *,
    history: list[dict[str, Any]],
    decision: AgentDecision,
    validation_error: DiagnosisValidationError,
    condition_evaluation_id: str,
    allowed_evidence_ids: list[str],
) -> list[dict[str, Any]]:
    repair_request = {
        "task": "repair_ops_diagnosis_output_contract_only",
        "fixed_condition": {
            "condition_evaluation_id": condition_evaluation_id,
            "CORE_BACKLOG_PRESSURE": "PRESENT",
        },
        "validation_error": validation_error.as_dict(),
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
        "instructions": REPAIR_INSTRUCTIONS,
    }
    return [
        *history,
        {
            "role": "assistant",
            "content": decision.model_dump_json(),
        },
        {
            "role": "user",
            "content": json.dumps(
                repair_request,
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        },
    ]


def _safe_output_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = {"reasoning", "function_call", "message"}
    return [item for item in items if item.get("type") in allowed]


def _fallback_decision(reason: DiagnosisStopReason, gap: str) -> AgentDecision:
    return AgentDecision(
        hypotheses=[
            HypothesisResult(
                hypothesis=HypothesisName.INSUFFICIENT_EVIDENCE,
                support_status=HypothesisSupportStatus.INSUFFICIENT,
                reason_codes=[reason.value.upper()],
                supporting_evidence_ids=[],
                conflicting_evidence_ids=[],
                evidence_gaps=[gap],
            )
        ],
        stop_reason=reason,
    )


def run_diagnosis(
    *,
    bundles: list[EvidenceBundle],
    condition_evaluation: SequenceConditionEvaluation,
    client: DiagnosisModelClient,
    policy: DiagnosisPolicy,
) -> DiagnosisRun:
    started_at = datetime.now(timezone.utc)
    recomputed = evaluate_bundle_sequence(
        [bundle.model_dump(mode="json") for bundle in bundles]
    )
    if recomputed.evaluation_id != condition_evaluation.evaluation_id:
        raise ValueError("condition evaluation does not match the ordered bundles")
    core = condition_evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
    if core.state != ConditionState.PRESENT:
        raise ValueError("Diagnosis Agent entry requires CORE_BACKLOG_PRESSURE=PRESENT")
    registry = DiagnosisToolRegistry(
        bundles=bundles,
        condition_evaluation=condition_evaluation,
    )
    initial_ids, unavailable = _initial_evidence(condition_evaluation, bundles)
    history: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_INSTRUCTIONS},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "condition_evaluation_id": condition_evaluation.evaluation_id,
                    "input_conditions": {"CORE_BACKLOG_PRESSURE": "PRESENT"},
                    "activation_facts": _activation_facts(core.facts),
                    "initial_evidence_ids": initial_ids,
                    "unavailable_telemetry": unavailable,
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
    evidence: list[DiagnosisEvidence] = []
    response_ids: list[str] = []
    usage = DiagnosisUsage()
    decision: AgentDecision | None = None
    api_requests = 0

    while decision is None:
        tools = registry.function_tools() if len(steps) < policy.max_tool_calls else []
        response = client.create(
            _request_payload(policy=policy, history=history, tools=tools)
        )
        api_requests += 1
        response_ids.append(response.response_id)
        usage = DiagnosisUsage(
            input_tokens=usage.input_tokens + response.input_tokens,
            output_tokens=usage.output_tokens + response.output_tokens,
            total_tokens=usage.total_tokens + response.total_tokens,
            api_requests=api_requests,
        )
        calls = [item for item in response.output if item.get("type") == "function_call"]
        if calls:
            if len(calls) != 1:
                raise ValueError("Diagnosis Agent must request exactly one tool per step")
            if len(steps) >= policy.max_steps or not tools:
                decision = _fallback_decision(
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
            requested_at = datetime.now(timezone.utc)
            try:
                result = registry.execute(tool_id)
            except Exception:
                decision = _fallback_decision(
                    DiagnosisStopReason.TOOL_ERROR,
                    f"TOOL_ERROR_{tool_id.upper()}",
                )
                break
            evidence.append(result)
            spec = TOOL_BY_ID[tool_id]
            steps.append(
                DiagnosisStep(
                    step=len(steps) + 1,
                    tool_id=tool_id,
                    reason_code=spec.reason_code,
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
            raise ValueError("Diagnosis Agent returned neither a tool call nor structured output")
        try:
            decision = AgentDecision.model_validate_json(response.output_text)
        except ValidationError as exc:
            raise ValueError("Diagnosis Agent structured output failed schema validation") from exc

    validator = DiagnosisOutputValidator()
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
        if (
            policy.max_output_repairs < 1
            or initial_error.code not in _REPAIRABLE_VALIDATION_CODES
        ):
            raise DiagnosisOutputContractFailure(
                initial_error=initial_error,
            ) from None

        repair_response = client.create(
            _request_payload(
                policy=policy,
                history=_repair_history(
                    history=history,
                    decision=decision,
                    validation_error=initial_error,
                    condition_evaluation_id=condition_evaluation.evaluation_id,
                    allowed_evidence_ids=[
                        *initial_ids,
                        *(item.evidence_id for item in evidence),
                    ],
                ),
                tools=[],
            )
        )
        output_repairs_used = 1
        api_requests += 1
        response_ids.append(repair_response.response_id)
        usage = DiagnosisUsage(
            input_tokens=usage.input_tokens + repair_response.input_tokens,
            output_tokens=usage.output_tokens + repair_response.output_tokens,
            total_tokens=usage.total_tokens + repair_response.total_tokens,
            api_requests=api_requests,
        )
        repair_calls = [
            item
            for item in repair_response.output
            if item.get("type") == "function_call"
        ]
        if repair_calls:
            final_error = DiagnosisValidationError(
                "REPAIR_TOOL_CALL_FORBIDDEN",
                "output repair returned a forbidden tool call",
            )
            raise DiagnosisOutputContractFailure(
                initial_error=initial_error,
                final_error=final_error,
            ) from None
        if not repair_response.output_text:
            final_error = DiagnosisValidationError(
                "REPAIR_STRUCTURED_OUTPUT_MISSING",
                "output repair returned no structured result",
            )
            raise DiagnosisOutputContractFailure(
                initial_error=initial_error,
                final_error=final_error,
            ) from None
        try:
            repaired_decision = AgentDecision.model_validate_json(
                repair_response.output_text
            )
        except ValidationError:
            final_error = DiagnosisValidationError(
                "REPAIR_SCHEMA_INVALID",
                "output repair failed structured schema validation",
            )
            raise DiagnosisOutputContractFailure(
                initial_error=initial_error,
                final_error=final_error,
            ) from None
        try:
            validation = validator.validate(
                decision=repaired_decision,
                policy=policy,
                initial_evidence_ids=initial_ids,
                additional_evidence=evidence,
                steps=steps,
            )
        except DiagnosisValidationError as final_error:
            raise DiagnosisOutputContractFailure(
                initial_error=initial_error,
                final_error=final_error,
            ) from None
        decision = repaired_decision
        validation_attempts.append(
            DiagnosisValidationAttempt(
                attempt=2,
                phase="repair",
                result="VALID",
                response_id=repair_response.response_id,
            )
        )
    completed_at = datetime.now(timezone.utc)
    context = DiagnosisContext(
        profile=bundles[0].cluster_profile,
        cluster_context=bundles[0].scope.context or "unknown",
        namespace=bundles[0].scope.namespace,
        topic=bundles[0].scope.topic,
        consumer_group=bundles[0].scope.consumer_group,
        source_bundle_digests=[
            item.source_bundle_sha256 for item in condition_evaluation.source_bundles
        ],
    )
    identity_payload = {
        "condition_evaluation_id": condition_evaluation.evaluation_id,
        "source_bundle_digests": context.source_bundle_digests,
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
    }
    return DiagnosisRun(
        diagnosis_id=diagnosis_id(identity_payload),
        incident_id=bundles[0].incident_id,
        condition_evaluation_id=condition_evaluation.evaluation_id,
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
        usage=usage,
        validation=validation,
        output_repairs_used=output_repairs_used,
        validation_attempts=validation_attempts,
    )


__all__ = [
    "DEFAULT_OPENAI_MODEL",
    "DiagnosisModelClient",
    "DiagnosisOutputContractFailure",
    "ModelResponse",
    "OpenAIResponsesClient",
    "load_openai_configuration",
    "run_diagnosis",
]
