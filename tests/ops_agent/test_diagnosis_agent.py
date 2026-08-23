from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ops_agent import cli
from ops_agent.diagnosis_agent import (
    DEFAULT_OPENAI_MODEL,
    DiagnosisOutputContractFailure,
    ModelResponse,
    load_openai_configuration,
    run_diagnosis,
)
from ops_agent.diagnosis_evals import aggregate_golden_scores, score_diagnosis_run
from ops_agent.diagnosis_models import (
    AgentDecision,
    DiagnosisPolicy,
    DiagnosisStopReason,
    HypothesisName,
    HypothesisResult,
    HypothesisSupportStatus,
)
from ops_agent.diagnosis_tools import DiagnosisToolRegistry
from ops_agent.diagnosis_validator import (
    DiagnosisOutputValidator,
    DiagnosisValidationError,
)
from ops_agent.models import EvidenceBundle
from ops_agent.evaluation_models import ConditionName
from ops_agent.sequence_evaluator import evaluate_bundle_sequence
from tests.ops_agent.synthetic_evidence import build_positive_sequence


ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "ops_agent" / "fixtures" / "diagnosis" / "golden_v1.json"
OUTPUT_REPAIR = (
    ROOT / "ops_agent" / "fixtures" / "diagnosis" / "output_repair_v1.json"
)


def _positive_inputs() -> tuple[list[EvidenceBundle], Any]:
    payloads = [
        json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode("utf-8")
        for bundle in build_positive_sequence()
    ]
    return (
        [EvidenceBundle.model_validate_json(payload) for payload in payloads],
        evaluate_bundle_sequence(payloads),
    )


class ScriptedClient:
    def __init__(
        self,
        *,
        tools: list[str],
        supported: list[str],
        stop_reason: str,
        include_rebalance: bool = False,
    ) -> None:
        self._tools = list(tools)
        self._supported = list(supported)
        self._stop_reason = stop_reason
        self._include_rebalance = include_rebalance
        self.calls = 0

    def create(self, payload: dict[str, Any]) -> ModelResponse:
        self.calls += 1
        if self._tools:
            tool_id = self._tools.pop(0)
            return ModelResponse(
                response_id=f"response-{self.calls}",
                output=[
                    {
                        "type": "function_call",
                        "call_id": f"call-{self.calls}",
                        "name": tool_id,
                        "arguments": "{}",
                    }
                ],
                output_text=None,
                input_tokens=10,
                output_tokens=2,
                total_tokens=12,
            )
        tool_evidence_ids = []
        initial_ids = []
        for item in payload["input"]:
            if item.get("type") == "function_call_output":
                tool_evidence_ids.append(json.loads(item["output"])["evidence_id"])
            if item.get("role") == "user":
                initial_ids = json.loads(item["content"])["initial_evidence_ids"]
        citations = tool_evidence_ids or initial_ids[:1]
        hypotheses = []
        for name in self._supported:
            hypotheses.append(
                {
                    "hypothesis": name,
                    "support_status": "SUPPORTED",
                    "reason_codes": ["GOLDEN_SUPPORTED"],
                    "supporting_evidence_ids": citations,
                    "conflicting_evidence_ids": [],
                    "evidence_gaps": [],
                }
            )
        if not self._supported:
            hypotheses.append(
                {
                    "hypothesis": "INSUFFICIENT_EVIDENCE",
                    "support_status": (
                        "INSUFFICIENT" if self._stop_reason == "tool_error" else "SUPPORTED"
                    ),
                    "reason_codes": ["GOLDEN_ABSTENTION"],
                    "supporting_evidence_ids": (
                        [] if self._stop_reason == "tool_error" else citations
                    ),
                    "conflicting_evidence_ids": [],
                    "evidence_gaps": ["CAUSAL_SIGNAL_NOT_AVAILABLE"],
                }
            )
        if self._include_rebalance:
            hypotheses.append(
                {
                    "hypothesis": "CONSUMER_REBALANCE_SUSPECTED",
                    "support_status": "INSUFFICIENT",
                    "reason_codes": ["REBAlANCE_UNAVAILABLE"],
                    "supporting_evidence_ids": [],
                    "conflicting_evidence_ids": [],
                    "evidence_gaps": ["CONSUMER_REBALANCE_TELEMETRY_UNAVAILABLE"],
                }
            )
        decision = {"hypotheses": hypotheses, "stop_reason": self._stop_reason}
        return ModelResponse(
            response_id=f"response-{self.calls}",
            output=[
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(decision)}],
                }
            ],
            output_text=json.dumps(decision),
            input_tokens=20,
            output_tokens=10,
            total_tokens=30,
        )


class DecisionSequenceClient:
    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self._decisions = list(decisions)
        self.calls = 0
        self.payloads: list[dict[str, Any]] = []

    def create(self, payload: dict[str, Any]) -> ModelResponse:
        self.calls += 1
        self.payloads.append(payload)
        if not self._decisions:
            raise AssertionError("output repair exceeded the scripted response budget")
        decision = self._decisions.pop(0)
        output_text = json.dumps(decision, separators=(",", ":"))
        return ModelResponse(
            response_id=f"decision-response-{self.calls}",
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": output_text}],
                }
            ],
            output_text=output_text,
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        )


def _resolve_fixture_decision(
    decision: dict[str, Any], known_ids: list[str]
) -> dict[str, Any]:
    encoded = json.dumps(decision)
    encoded = encoded.replace("$KNOWN_1", known_ids[0])
    encoded = encoded.replace("$KNOWN_2", known_ids[1])
    return json.loads(encoded)


def test_all_allowlisted_tools_return_new_normalized_evidence() -> None:
    bundles, evaluation = _positive_inputs()
    registry = DiagnosisToolRegistry(bundles=bundles, condition_evaluation=evaluation)

    results = [registry.execute(tool_id) for tool_id in registry.tool_ids]

    assert len(results) == 9
    assert len({item.evidence_id for item in results}) == 9
    assert all(item.evidence_id.startswith("diagnosis.get_") for item in results)
    assert all(item.source_evidence_ids for item in results)
    assert all("raw_ref" not in item.summary for item in results)


def test_single_agent_run_is_bounded_and_grounded() -> None:
    bundles, evaluation = _positive_inputs()
    client = ScriptedClient(
        tools=["get_partition_lag", "get_worker_stage_latency"],
        supported=[],
        stop_reason="insufficient_evidence",
    )
    run = run_diagnosis(
        bundles=bundles,
        condition_evaluation=evaluation,
        client=client,
        policy=DiagnosisPolicy(model=DEFAULT_OPENAI_MODEL),
    )

    assert run.schema_version == "ops.diagnosis.v1"
    assert run.input_conditions == {"CORE_BACKLOG_PRESSURE": "PRESENT"}
    assert [step.tool_id for step in run.steps] == [
        "get_partition_lag",
        "get_worker_stage_latency",
    ]
    assert run.stop_reason == DiagnosisStopReason.INSUFFICIENT_EVIDENCE
    assert run.validation.citations_valid is True
    assert run.output_repairs_used == 0
    assert [item.result for item in run.validation_attempts] == ["VALID"]
    assert run.usage.api_requests == 3
    run.verify_integrity()


def test_unknown_evidence_citation_is_rejected() -> None:
    decision = AgentDecision(
        hypotheses=[
            HypothesisResult(
                hypothesis=HypothesisName.HOT_KEY_SUSPECTED,
                support_status=HypothesisSupportStatus.SUPPORTED,
                reason_codes=["BAD_CITATION"],
                supporting_evidence_ids=["fabricated-evidence"],
                conflicting_evidence_ids=[],
                evidence_gaps=[],
            )
        ],
        stop_reason=DiagnosisStopReason.SUFFICIENT_EVIDENCE,
    )
    with pytest.raises(DiagnosisValidationError, match="unknown evidence"):
        DiagnosisOutputValidator().validate(
            decision=decision,
            policy=DiagnosisPolicy(model=DEFAULT_OPENAI_MODEL),
            initial_evidence_ids=[],
            additional_evidence=[],
            steps=[],
        )


def test_rebalance_cannot_be_confirmed_or_excluded() -> None:
    decision = AgentDecision(
        hypotheses=[
            HypothesisResult(
                hypothesis=HypothesisName.CONSUMER_REBALANCE_SUSPECTED,
                support_status=HypothesisSupportStatus.SUPPORTED,
                reason_codes=["UNSUPPORTED"],
                supporting_evidence_ids=["known"],
                conflicting_evidence_ids=[],
                evidence_gaps=[],
            )
        ],
        stop_reason=DiagnosisStopReason.SUFFICIENT_EVIDENCE,
    )
    with pytest.raises(DiagnosisValidationError, match="rebalance telemetry"):
        DiagnosisOutputValidator().validate(
            decision=decision,
            policy=DiagnosisPolicy(model=DEFAULT_OPENAI_MODEL),
            initial_evidence_ids=["known"],
            additional_evidence=[],
            steps=[],
        )


@pytest.mark.parametrize(
    ("supporting", "conflicting"),
    [(["known"], []), ([], ["known"])],
)
def test_unavailable_rebalance_telemetry_cannot_be_cited(
    supporting: list[str], conflicting: list[str]
) -> None:
    decision = AgentDecision(
        hypotheses=[
            HypothesisResult(
                hypothesis=HypothesisName.CONSUMER_REBALANCE_SUSPECTED,
                support_status=HypothesisSupportStatus.INSUFFICIENT,
                reason_codes=["REBALANCE_UNAVAILABLE"],
                supporting_evidence_ids=supporting,
                conflicting_evidence_ids=conflicting,
                evidence_gaps=["CONSUMER_REBALANCE_TELEMETRY_UNAVAILABLE"],
            )
        ],
        stop_reason=DiagnosisStopReason.INSUFFICIENT_EVIDENCE,
    )

    with pytest.raises(DiagnosisValidationError, match="cannot be cited"):
        DiagnosisOutputValidator().validate(
            decision=decision,
            policy=DiagnosisPolicy(model=DEFAULT_OPENAI_MODEL),
            initial_evidence_ids=["known"],
            additional_evidence=[],
            steps=[],
        )


def test_forbidden_recovery_or_action_claim_is_rejected() -> None:
    decision = AgentDecision(
        hypotheses=[
            HypothesisResult(
                hypothesis=HypothesisName.INSUFFICIENT_EVIDENCE,
                support_status=HypothesisSupportStatus.INSUFFICIENT,
                reason_codes=["RECOVERY_DECLARED"],
                supporting_evidence_ids=[],
                conflicting_evidence_ids=[],
                evidence_gaps=["SCALE_WORKER"],
            )
        ],
        stop_reason=DiagnosisStopReason.INSUFFICIENT_EVIDENCE,
    )
    with pytest.raises(DiagnosisValidationError, match="forbidden"):
        DiagnosisOutputValidator().validate(
            decision=decision,
            policy=DiagnosisPolicy(model=DEFAULT_OPENAI_MODEL),
            initial_evidence_ids=[],
            additional_evidence=[],
            steps=[],
        )


def test_step_budget_exhaustion_is_structured() -> None:
    bundles, evaluation = _positive_inputs()
    client = ScriptedClient(
        tools=[
            "get_partition_lag",
            "get_worker_stage_latency",
            "get_worker_replica_status",
            "get_keda_status",
            "get_postgres_health",
        ],
        supported=[],
        stop_reason="insufficient_evidence",
    )
    run = run_diagnosis(
        bundles=bundles,
        condition_evaluation=evaluation,
        client=client,
        policy=DiagnosisPolicy(model=DEFAULT_OPENAI_MODEL),
    )

    assert run.steps_used == 4
    assert run.stop_reason == DiagnosisStopReason.STEP_BUDGET_EXHAUSTED
    assert run.usage.api_requests == 5


def test_non_present_sequence_is_rejected_before_model_call() -> None:
    payload = json.dumps(
        build_positive_sequence()[0], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    bundle = EvidenceBundle.model_validate_json(payload)
    evaluation = evaluate_bundle_sequence([payload])
    client = ScriptedClient(tools=[], supported=[], stop_reason="insufficient_evidence")

    with pytest.raises(ValueError, match="requires CORE_BACKLOG_PRESSURE=PRESENT"):
        run_diagnosis(
            bundles=[bundle],
            condition_evaluation=evaluation,
            client=client,
            policy=DiagnosisPolicy(model=DEFAULT_OPENAI_MODEL),
        )
    assert client.calls == 0


def test_cli_requires_explicit_live_opt_in() -> None:
    with pytest.raises(ValueError, match="explicit --live"):
        cli.main(
            [
                "diagnose",
                "--conditions",
                "missing.conditions.json",
                "--input",
                "missing.bundle.json",
                "--output",
                "unused.json",
            ]
        )


def test_openai_model_defaults_to_luna(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    _, model = load_openai_configuration(tmp_path)

    assert model == "gpt-5.6-luna"


@pytest.mark.parametrize(
    "fixture",
    json.loads(OUTPUT_REPAIR.read_text(encoding="utf-8"))["fixtures"],
    ids=lambda fixture: fixture["name"],
)
def test_output_contract_repair_fixtures(fixture: dict[str, Any]) -> None:
    bundles, evaluation = _positive_inputs()
    known_ids = list(
        evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE].evidence_ids
    )
    assert len(known_ids) >= 2
    decisions = [_resolve_fixture_decision(fixture["initial"], known_ids)]
    if fixture["repair"] is not None:
        decisions.append(_resolve_fixture_decision(fixture["repair"], known_ids))
    client = DecisionSequenceClient(decisions)
    policy = DiagnosisPolicy(
        model=DEFAULT_OPENAI_MODEL,
        max_retries=0,
        max_output_repairs=1,
    )

    if fixture["expected_success"]:
        run = run_diagnosis(
            bundles=bundles,
            condition_evaluation=evaluation,
            client=client,
            policy=policy,
        )
        assert run.hypotheses[0].support_status == fixture["expected_final_status"]
        assert run.output_repairs_used == fixture["expected_calls"] - 1
        assert run.usage.api_requests == fixture["expected_calls"]
        assert len(run.model_response_ids) == fixture["expected_calls"]
        assert run.validation_attempts[-1].result == "VALID"
        assert run.additional_evidence == []
        run.verify_integrity()
    else:
        with pytest.raises(DiagnosisOutputContractFailure) as captured:
            run_diagnosis(
                bundles=bundles,
                condition_evaluation=evaluation,
                client=client,
                policy=policy,
            )
        assert captured.value.classification == "validation_failure"
        assert captured.value.final_error is not None
        assert (
            captured.value.final_error["code"] == fixture["expected_final_error"]
        )

    assert client.calls == fixture["expected_calls"]
    if fixture["expected_calls"] == 2:
        repair_payload = client.payloads[1]
        assert "tools" not in repair_payload
        repair_request = json.loads(repair_payload["input"][-1]["content"])
        assert repair_request["task"] == "repair_ops_diagnosis_output_contract_only"
        assert repair_request["fixed_condition"]["CORE_BACKLOG_PRESSURE"] == "PRESENT"
        assert (
            repair_request["validation_error"]["code"]
            == "CONFLICTED_REQUIRES_BOTH_CITATION_SIDES"
        )
        assert set(repair_request["allowed_evidence_ids"]).issuperset(known_ids)
        assert "NO_TOOL_CALLS" in repair_request["constraints"]
        assert "NO_NEW_EVIDENCE" in repair_request["constraints"]
        assert "NO_FABRICATED_EVIDENCE_IDS" in repair_request["constraints"]


def test_cli_does_not_write_completed_artifact_after_failed_repair(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    bundles, evaluation = _positive_inputs()
    known_ids = list(
        evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE].evidence_ids
    )
    fixture = next(
        item
        for item in json.loads(OUTPUT_REPAIR.read_text(encoding="utf-8"))["fixtures"]
        if item["name"] == "repair_remains_invalid"
    )
    client = DecisionSequenceClient(
        [
            _resolve_fixture_decision(fixture["initial"], known_ids),
            _resolve_fixture_decision(fixture["repair"], known_ids),
        ]
    )
    conditions_path = tmp_path / "positive.conditions.json"
    conditions_path.write_text(evaluation.model_dump_json(), encoding="utf-8")
    input_paths = []
    for index, bundle in enumerate(build_positive_sequence()):
        input_path = tmp_path / f"positive-{index:03}.json"
        input_path.write_text(
            json.dumps(bundle, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        input_paths.append(input_path)
    output_path = tmp_path / "invalid-diagnosis.json"
    monkeypatch.setattr(
        cli,
        "load_openai_configuration",
        lambda _workspace: ("sk-test-not-real", DEFAULT_OPENAI_MODEL),
    )
    monkeypatch.setattr(cli, "OpenAIResponsesClient", lambda **_kwargs: client)

    result = cli.run_diagnose(
        SimpleNamespace(
            live=True,
            output=output_path,
            conditions=conditions_path,
            input=input_paths,
        )
    )

    assert result == 1
    assert client.calls == 2
    assert not output_path.exists()
    safe_error = json.loads(capsys.readouterr().err)
    assert safe_error == {
        "status": "FAILED",
        "classification": "validation_failure",
        "initial_validation_error": "CONFLICTED_REQUIRES_BOTH_CITATION_SIDES",
        "final_validation_error": "CONFLICTED_REQUIRES_BOTH_CITATION_SIDES",
    }


def test_golden_fixture_agent_evaluation() -> None:
    fixtures = json.loads(GOLDEN.read_text(encoding="utf-8"))["fixtures"]
    bundles, evaluation = _positive_inputs()
    scores = []
    for fixture in fixtures:
        if not fixture["entry_allowed"]:
            payload = json.dumps(
                build_positive_sequence()[0],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            with pytest.raises(ValueError):
                run_diagnosis(
                    bundles=[EvidenceBundle.model_validate_json(payload)],
                    condition_evaluation=evaluate_bundle_sequence([payload]),
                    client=ScriptedClient(
                        tools=[], supported=[], stop_reason="insufficient_evidence"
                    ),
                    policy=DiagnosisPolicy(model=DEFAULT_OPENAI_MODEL),
                )
            scores.append(
                {
                    "schema_compliance": 1.0,
                    "citation_accuracy": 1.0,
                    "fabricated_evidence_id_count": 0,
                    "unsupported_claim_rate": 0.0,
                    "tool_selection_precision": 1.0,
                    "unnecessary_tool_call_count": 0,
                    "required_abstention_accuracy": 1.0,
                    "step_budget_compliance": 1.0,
                    "stop_reason_compliance": 1.0,
                    "forbidden_tool_call_count": 0,
                }
            )
            continue
        client = ScriptedClient(
            tools=fixture["expected_tools"],
            supported=fixture["allowed_supported_hypotheses"],
            stop_reason=fixture["expected_stop_reason"],
            include_rebalance=fixture["name"] == "consumer_rebalance_unavailable",
        )
        run = run_diagnosis(
            bundles=bundles,
            condition_evaluation=evaluation,
            client=client,
            policy=DiagnosisPolicy(model=DEFAULT_OPENAI_MODEL),
        )
        scores.append(
            score_diagnosis_run(
                run,
                expected_tools=fixture["expected_tools"],
                allowed_supported_hypotheses=fixture[
                    "allowed_supported_hypotheses"
                ],
                expect_abstention=fixture["expect_abstention"],
                expected_stop_reason=fixture["expected_stop_reason"],
            )
        )
    summary = aggregate_golden_scores(scores)

    assert summary == {
        "fixture_count": 9,
        "schema_compliance": 1.0,
        "citation_accuracy": 1.0,
        "unsupported_claim_rate": 0.0,
        "tool_selection_precision": 1.0,
        "required_abstention_accuracy": 1.0,
        "step_budget_compliance": 1.0,
        "stop_reason_compliance": 1.0,
        "fabricated_evidence_id_count": 0,
        "unnecessary_tool_call_count": 0,
        "forbidden_tool_call_count": 0,
    }
