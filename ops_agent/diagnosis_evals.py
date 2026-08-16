"""Offline golden-fixture scoring for diagnosis artifacts."""

from __future__ import annotations

from typing import Any

from ops_agent.diagnosis_models import (
    DiagnosisRun,
    DiagnosisStopReason,
    HypothesisName,
    HypothesisSupportStatus,
)
from ops_agent.diagnosis_tools import TOOL_BY_ID


def score_diagnosis_run(
    run: DiagnosisRun,
    *,
    expected_tools: list[str],
    allowed_supported_hypotheses: list[str],
    expect_abstention: bool,
    expected_stop_reason: str,
) -> dict[str, Any]:
    run.verify_integrity()
    called = [step.tool_id for step in run.steps]
    expected = set(expected_tools)
    selected = set(called)
    supported = {
        item.hypothesis.value
        for item in run.hypotheses
        if item.hypothesis != HypothesisName.INSUFFICIENT_EVIDENCE
        and item.support_status == HypothesisSupportStatus.SUPPORTED
    }
    unsupported = supported - set(allowed_supported_hypotheses)
    citations = {
        value
        for item in run.hypotheses
        for value in [
            *item.supporting_evidence_ids,
            *item.conflicting_evidence_ids,
        ]
    }
    known = set(run.initial_evidence_ids) | {
        item.evidence_id for item in run.additional_evidence
    }
    abstained = not supported
    return {
        "schema_compliance": 1.0,
        "citation_accuracy": 1.0 if citations <= known else 0.0,
        "fabricated_evidence_id_count": len(citations - known),
        "unsupported_claim_rate": (
            0.0 if not supported else len(unsupported) / len(supported)
        ),
        "tool_selection_precision": (
            1.0 if not called else len(selected & expected) / len(called)
        ),
        "unnecessary_tool_call_count": len(selected - expected),
        "required_abstention_accuracy": float(abstained == expect_abstention),
        "step_budget_compliance": float(
            len(run.steps) <= run.policy.max_steps
            and len(run.steps) <= run.policy.max_tool_calls
        ),
        "stop_reason_compliance": float(
            run.stop_reason.value == expected_stop_reason
        ),
        "forbidden_tool_call_count": len(
            [step for step in run.steps if step.tool_id not in TOOL_BY_ID]
        ),
    }


def aggregate_golden_scores(scores: list[dict[str, Any]]) -> dict[str, Any]:
    if not scores:
        raise ValueError("at least one golden score is required")
    rate_fields = (
        "schema_compliance",
        "citation_accuracy",
        "unsupported_claim_rate",
        "tool_selection_precision",
        "required_abstention_accuracy",
        "step_budget_compliance",
        "stop_reason_compliance",
    )
    totals = {
        "fabricated_evidence_id_count": sum(
            item["fabricated_evidence_id_count"] for item in scores
        ),
        "unnecessary_tool_call_count": sum(
            item["unnecessary_tool_call_count"] for item in scores
        ),
        "forbidden_tool_call_count": sum(
            item["forbidden_tool_call_count"] for item in scores
        ),
    }
    return {
        "fixture_count": len(scores),
        **{
            field: sum(float(item[field]) for item in scores) / len(scores)
            for field in rate_fields
        },
        **totals,
    }


__all__ = ["aggregate_golden_scores", "score_diagnosis_run"]
