"""Offline, fixture-bound diagnosis scoring. No model or runtime acquisition."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from ops_agent.diagnosis_scenarios import ControlledScenarioRegistry, ScenarioCatalog
from ops_agent.diagnosis_v2_models import AgentDecisionV2, DiagnosisRunV2, HypothesisNameV2
from ops_agent.diagnosis_v2_validator import DiagnosisOutputValidatorV2
from ops_agent.evaluation_models import FrozenModel, canonical_sha256


class QualityCase(FrozenModel):
    expected_supported: list[HypothesisNameV2]
    required_tools: list[str] = Field(min_length=1)
    required_supporting_tools: list[str]


class QualityRubric(FrozenModel):
    schema_version: Literal["ops.diagnosis.quality-rubric.v1"]
    cases: dict[str, QualityCase]

    def verify_catalog(self, catalog: ScenarioCatalog) -> None:
        if set(self.cases) != {case.fixture_id for case in catalog.scenarios}:
            raise ValueError("rubric must cover exactly the scenario catalog")
        tools = set(ControlledScenarioRegistry(
            catalog=catalog, fixture_id=catalog.scenarios[0].fixture_id
        ).tool_ids)
        for fixture_id, case in self.cases.items():
            if len(set(case.required_tools)) != len(case.required_tools):
                raise ValueError("rubric required tools must be unique")
            if not set(case.required_tools) <= tools:
                raise ValueError("rubric contains unknown tools")
            if not set(case.required_supporting_tools) <= set(case.required_tools):
                raise ValueError("support anchors must be required tools")
            expected = catalog.scenario(fixture_id).expected_primary_hypothesis
            wanted = [] if expected == "INSUFFICIENT_EVIDENCE" else [expected]
            if [item.value for item in case.expected_supported] != wanted:
                raise ValueError("rubric expectation disagrees with scenario")


class QualityAttempt(FrozenModel):
    """One scheduled attempt, including attempts without a completed artifact.

    Failed-run telemetry remains unknown unless explicitly supplied by its runner.
    The offline importer never infers model latency from replay timestamps.
    """

    attempt_id: str = Field(min_length=1, max_length=128)
    fixture_id: str = Field(min_length=1, max_length=128)
    execution_kind: Literal["scripted", "live"]
    run: dict[str, Any] | None = None
    failure_code: str | None = Field(default=None, min_length=1, max_length=128)
    elapsed_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    first_pass_contract: bool | None = None
    repair_used: int | None = Field(default=None, ge=0, le=1)
    # Raw, sanitized responses can be supplied by a future runner on failure.
    responses: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_outcome(self) -> "QualityAttempt":
        if (self.run is None) == (self.failure_code is None):
            raise ValueError("attempt requires either a run or a failure code")
        return self


def _citation_relation(evidence: Any, hypothesis: str) -> str | None:
    """Limited signed relations; contextual support alone never proves a cause."""
    if evidence.status.value != "OK" or evidence.freshness.status.value != "FRESH":
        return "unusable"
    flag = evidence.summary.get("semantic_flag")
    relations = {
        "WORKER_PATH_PRESSURE_SUSPECTED": {
            "ABOVE_SCENARIO_BASELINE": "supporting",
            "WITHIN_SCENARIO_BASELINE": "conflicting",
            "POSTGRES_PATH_HEALTHY": "supporting",
            "WORKER_CAPACITY_AVAILABLE": "supporting",
        },
        "WORKER_CAPACITY_SHORTFALL_SUSPECTED": {
            "WORKER_CAPACITY_SHORTFALL": "supporting",
            "WORKER_CAPACITY_AVAILABLE": "conflicting",
            "WITHIN_SCENARIO_BASELINE": "supporting",
        },
        "POSTGRES_PATH_DEGRADED_SUSPECTED": {
            "POSTGRES_PATH_DEGRADED": "supporting",
            "POSTGRES_PATH_HEALTHY": "conflicting",
            "ABOVE_SCENARIO_BASELINE": "supporting",
        },
    }
    if hypothesis == "WORKER_CAPACITY_SHORTFALL_SUSPECTED":
        if evidence.tool_id == "get_keda_status" and evidence.summary.get("active") is True:
            return "supporting"
    return relations.get(hypothesis, {}).get(flag)


def score_attempt(
    attempt: QualityAttempt, *, catalog: ScenarioCatalog, rubric: QualityRubric
) -> dict[str, Any]:
    rubric.verify_catalog(catalog)
    case = rubric.cases[attempt.fixture_id]
    base: dict[str, Any] = {
        "attempt_id": attempt.attempt_id,
        "fixture_id": attempt.fixture_id,
        "execution_kind": attempt.execution_kind,
        "contract_pass": False,
        "quality_pass": False,
        "first_pass_contract": attempt.first_pass_contract,
        "repair_used": attempt.repair_used,
        "elapsed_seconds": attempt.elapsed_seconds,
        "input_tokens": attempt.input_tokens,
        "output_tokens": attempt.output_tokens,
        "failure_code": attempt.failure_code,
        "checks": None,
    }
    if attempt.run is None:
        return base
    try:
        run = DiagnosisRunV2.model_validate(attempt.run)
        if run.policy.model_mode != ("recorded" if attempt.execution_kind == "scripted" else "live"):
            raise ValueError("execution kind disagrees with run policy")
        activation = catalog.activation
        context = run.context
        if (
            run.acquisition_mode.value != "CONTROLLED_SCENARIO"
            or run.condition_evaluation_id != activation["condition_evaluation_id"]
            or run.incident_id != activation["incident_id"]
            or run.initial_evidence_ids != activation["evidence_ids"]
            or context.activation_source_bundle_digests != activation["source_bundle_digests"]
            or any(getattr(context, key) != activation[key] for key in (
                "profile", "cluster_context", "namespace", "topic", "consumer_group"
            ))
        ):
            raise ValueError("run does not match the frozen activation")
        registry = ControlledScenarioRegistry(catalog=catalog, fixture_id=attempt.fixture_id)
        expected_evidence = {tool: registry.execute(tool) for tool in registry.tool_ids}
        for item in run.additional_evidence:
            if item.model_dump(mode="json") != expected_evidence[item.tool_id].model_dump(mode="json"):
                raise ValueError("run evidence does not match the frozen fixture")
        # Re-run the contract validator instead of trusting a recorded VALID flag.
        DiagnosisOutputValidatorV2(allowlisted_tool_ids=set(registry.tool_ids)).validate(
            decision=AgentDecisionV2(hypotheses=run.hypotheses, stop_reason=run.stop_reason),
            policy=run.policy,
            initial_evidence_ids=run.initial_evidence_ids,
            additional_evidence=run.additional_evidence,
            steps=run.steps,
        )
    except (ValueError, KeyError) as exc:
        base["failure_code"] = "INVALID_COMPLETED_ARTIFACT"
        base["error_type"] = type(exc).__name__
        return base

    called = [step.tool_id for step in run.steps]
    by_id = {item.evidence_id: item for item in run.additional_evidence}
    supported = {
        item.hypothesis.value for item in run.hypotheses
        if item.hypothesis.value != "INSUFFICIENT_EVIDENCE"
        and item.support_status.value == "SUPPORTED"
    }
    expected = {item.value for item in case.expected_supported}
    edges = []
    anchors = set()
    for hypothesis in run.hypotheses:
        for side, ids in (
            ("supporting", hypothesis.supporting_evidence_ids),
            ("conflicting", hypothesis.conflicting_evidence_ids),
        ):
            for evidence_id in ids:
                evidence = by_id.get(evidence_id)
                relation = _citation_relation(evidence, hypothesis.hypothesis.value) if evidence else None
                matched = relation == side
                edges.append({
                    "hypothesis": hypothesis.hypothesis.value,
                    "evidence_id": evidence_id,
                    "claimed_relation": side,
                    "rubric_relation": relation,
                    "pass": matched,
                })
                if matched and side == "supporting" and hypothesis.hypothesis.value in expected:
                    anchors.add(evidence.tool_id)
    missing = sorted(set(case.required_tools) - set(called))
    missing_anchors = sorted(set(case.required_supporting_tools) - anchors)
    abstention = not supported
    correct_abstention = abstention == (not expected)
    gaps_present = any(item.evidence_gaps for item in run.hypotheses)
    strict_branches = []
    for branch in catalog.scenario(attempt.fixture_id).branch_expectations:
        if branch.after_tool_id not in called:
            strict_branches.append(False)
            continue
        index = called.index(branch.after_tool_id)
        next_tool = called[index + 1] if index + 1 < len(called) else None
        strict_branches.append(
            next_tool in branch.expected_next_tools
            if branch.expected_next_tools else next_tool is None
        )
    checks = {
        "supported_hypotheses_match": supported == expected,
        "abstention_correct": correct_abstention,
        "abstention_gap_present": not abstention or gaps_present,
        "required_evidence_acquired": not missing,
        "support_anchors_present": not missing_anchors,
        "citation_semantics_pass": all(edge["pass"] for edge in edges),
    }
    base.update({
        "contract_pass": True,
        "quality_pass": all(checks.values()),
        "first_pass_contract": run.validation_attempts[0].result == "VALID",
        "repair_used": run.output_repairs_used,
        "input_tokens": run.usage.input_tokens,
        "output_tokens": run.usage.output_tokens,
        "checks": checks,
        "supported_hypotheses": sorted(supported),
        "missing_required_tools": missing,
        "missing_support_anchors": missing_anchors,
        "extra_tool_call_count": sum(tool not in case.required_tools for tool in called),
        "tool_call_count": len(called),
        "citation_edges": edges,
        "unscored_citation_count": sum(edge["rubric_relation"] is None for edge in edges),
        "strict_branch_pass": all(strict_branches),
    })
    return base


def summarize_scores(scores: list[dict[str, Any]]) -> dict[str, Any]:
    if not scores or len({item["attempt_id"] for item in scores}) != len(scores):
        raise ValueError("scores must be nonempty with unique attempt IDs")
    kinds = {item["execution_kind"] for item in scores}
    if len(kinds) != 1:
        raise ValueError("scripted and live scores must be reported separately")
    count = len(scores)
    return {
        "attempt_count": count,
        "execution_kind": scores[0]["execution_kind"],
        "contract_success_rate": sum(item["contract_pass"] for item in scores) / count,
        "quality_success_rate": sum(item["quality_pass"] for item in scores) / count,
        "first_pass_contract_success_rate": sum(item["first_pass_contract"] is True for item in scores) / count,
        "first_pass_unknown_count": sum(item["first_pass_contract"] is None for item in scores),
        "repaired_contract_success_count": sum(item["contract_pass"] and item["repair_used"] == 1 for item in scores),
        "final_contract_failure_count": sum(not item["contract_pass"] for item in scores),
        "telemetry": {
            field: {
                "known_attempt_count": sum(item[field] is not None for item in scores),
                "total_known": sum(item[field] for item in scores if item[field] is not None),
            }
            for field in ("elapsed_seconds", "input_tokens", "output_tokens")
        },
    }


def evaluation_identity(catalog: ScenarioCatalog, rubric: QualityRubric) -> dict[str, str]:
    return {
        "catalog_sha256": canonical_sha256(catalog.model_dump(mode="json")),
        "rubric_sha256": canonical_sha256(rubric.model_dump(mode="json")),
        "scorer_version": "ops.diagnosis.quality.v1",
    }
