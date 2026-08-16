"""Deterministic validation for model-produced diagnosis decisions."""

from __future__ import annotations

import re
from typing import Any

from ops_agent.diagnosis_models import (
    AgentDecision,
    DiagnosisEvidence,
    DiagnosisPolicy,
    DiagnosisStep,
    DiagnosisStopReason,
    DiagnosisValidation,
    HypothesisName,
    HypothesisSupportStatus,
)
from ops_agent.diagnosis_tools import TOOL_BY_ID


class DiagnosisValidationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class DiagnosisOutputValidator:
    def validate(
        self,
        *,
        decision: AgentDecision,
        policy: DiagnosisPolicy,
        initial_evidence_ids: list[str],
        additional_evidence: list[DiagnosisEvidence],
        steps: list[DiagnosisStep],
    ) -> DiagnosisValidation:
        if len(steps) > policy.max_steps or len(steps) > policy.max_tool_calls:
            raise DiagnosisValidationError(
                "STEP_OR_TOOL_BUDGET_EXCEEDED",
                "step or tool-call budget exceeded",
            )
        if len({step.tool_id for step in steps}) != len(steps):
            raise DiagnosisValidationError(
                "REPEATED_TOOL_CALL",
                "repeated tool calls are forbidden",
            )
        if any(step.tool_id not in TOOL_BY_ID for step in steps):
            raise DiagnosisValidationError(
                "NON_ALLOWLISTED_TOOL_CALL",
                "non-allowlisted tool call",
            )
        returned = [item.evidence_id for item in additional_evidence]
        step_returned = [value for step in steps for value in step.returned_evidence_ids]
        if returned != step_returned:
            raise DiagnosisValidationError(
                "STEP_EVIDENCE_TRACE_MISMATCH",
                "step evidence trace does not match tool results",
            )
        if len(returned) != len(set(returned)):
            raise DiagnosisValidationError(
                "DUPLICATE_DIAGNOSIS_EVIDENCE_ID",
                "diagnosis evidence IDs must be unique",
            )
        for step, item in zip(steps, additional_evidence):
            if step.tool_id != item.tool_id:
                raise DiagnosisValidationError(
                    "TOOL_EVIDENCE_ID_MISMATCH",
                    "tool step and evidence tool ID mismatch",
                )

        known = set(initial_evidence_ids) | set(returned)
        for hypothesis_index, hypothesis in enumerate(decision.hypotheses):
            structured_text = " ".join(
                [*hypothesis.reason_codes, *hypothesis.evidence_gaps]
            ).upper()
            if re.search(
                r"(?:CORE_BACKLOG_PRESSURE_(?:ABSENT|UNKNOWN)|CONDITION_REEVALUAT|"
                r"RECOVERED|RECOVERY_DECLAR|REMEDIAT|KUBECTL|PROMQL|SHELL|"
                r"SCALE_WORKER|DELETE_POD|ARGO_SYNC|DLQ_REPLAY|DB_RESTORE)",
                structured_text,
            ):
                raise DiagnosisValidationError(
                    "FORBIDDEN_DIAGNOSIS_CLAIM",
                    "model output contains a forbidden condition, recovery, or action claim",
                    details={"hypothesis_index": hypothesis_index},
                )
            cited = set(hypothesis.supporting_evidence_ids) | set(
                hypothesis.conflicting_evidence_ids
            )
            unknown = cited - known
            if unknown:
                raise DiagnosisValidationError(
                    "UNKNOWN_EVIDENCE_CITATION",
                    f"hypothesis cites unknown evidence IDs: {sorted(unknown)}",
                    details={
                        "hypothesis_index": hypothesis_index,
                        "unknown_evidence_ids": sorted(unknown),
                    },
                )
            if (
                hypothesis.support_status == HypothesisSupportStatus.SUPPORTED
                and not hypothesis.supporting_evidence_ids
            ):
                raise DiagnosisValidationError(
                    "SUPPORTED_REQUIRES_SUPPORTING_CITATION",
                    "SUPPORTED hypothesis requires citations",
                    details={"hypothesis_index": hypothesis_index},
                )
            if (
                hypothesis.support_status == HypothesisSupportStatus.CONFLICTED
                and (
                    not hypothesis.supporting_evidence_ids
                    or not hypothesis.conflicting_evidence_ids
                )
            ):
                missing_fields = []
                if not hypothesis.supporting_evidence_ids:
                    missing_fields.append("supporting_evidence_ids")
                if not hypothesis.conflicting_evidence_ids:
                    missing_fields.append("conflicting_evidence_ids")
                raise DiagnosisValidationError(
                    "CONFLICTED_REQUIRES_BOTH_CITATION_SIDES",
                    "CONFLICTED hypothesis requires supporting and conflicting citations",
                    details={
                        "hypothesis_index": hypothesis_index,
                        "missing_fields": missing_fields,
                    },
                )
            if (
                hypothesis.support_status == HypothesisSupportStatus.INSUFFICIENT
                and not hypothesis.evidence_gaps
            ):
                raise DiagnosisValidationError(
                    "INSUFFICIENT_REQUIRES_EVIDENCE_GAP",
                    "INSUFFICIENT hypothesis requires an evidence gap",
                    details={"hypothesis_index": hypothesis_index},
                )
            if hypothesis.hypothesis == HypothesisName.CONSUMER_REBALANCE_SUSPECTED:
                if hypothesis.support_status != HypothesisSupportStatus.INSUFFICIENT:
                    raise DiagnosisValidationError(
                        "REBALANCE_TELEMETRY_UNAVAILABLE",
                        "rebalance telemetry cannot support or exclude the hypothesis",
                        details={"hypothesis_index": hypothesis_index},
                    )
                if "CONSUMER_REBALANCE_TELEMETRY_UNAVAILABLE" not in hypothesis.evidence_gaps:
                    raise DiagnosisValidationError(
                        "REBALANCE_GAP_REQUIRED",
                        "rebalance hypothesis must preserve unavailable telemetry gap",
                        details={"hypothesis_index": hypothesis_index},
                    )

        supported_causal = any(
            item.hypothesis != HypothesisName.INSUFFICIENT_EVIDENCE
            and item.support_status == HypothesisSupportStatus.SUPPORTED
            for item in decision.hypotheses
        )
        if (
            decision.stop_reason == DiagnosisStopReason.SUFFICIENT_EVIDENCE
            and not supported_causal
        ):
            raise DiagnosisValidationError(
                "SUFFICIENT_STOP_REQUIRES_SUPPORTED_CAUSE",
                "sufficient_evidence requires a supported causal hypothesis",
            )
        if (
            decision.stop_reason == DiagnosisStopReason.INSUFFICIENT_EVIDENCE
            and supported_causal
        ):
            raise DiagnosisValidationError(
                "INSUFFICIENT_STOP_CONFLICTS_WITH_SUPPORTED_CAUSE",
                "insufficient_evidence conflicts with a supported causal hypothesis",
            )
        if (
            decision.stop_reason == DiagnosisStopReason.STEP_BUDGET_EXHAUSTED
            and len(steps) != policy.max_steps
        ):
            raise DiagnosisValidationError(
                "STEP_BUDGET_STOP_REQUIRES_FULL_BUDGET",
                "step_budget_exhausted requires the complete step budget",
            )
        return DiagnosisValidation(
            schema_valid=True,
            citations_valid=True,
            tool_calls_valid=True,
            step_budget_valid=True,
            stop_valid=True,
            forbidden_claims_absent=True,
        )


__all__ = ["DiagnosisOutputValidator", "DiagnosisValidationError"]
