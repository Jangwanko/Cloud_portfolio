"""Scripted regression for prompt propagation, repair bounds and abstention scoring.

The test clients return prescribed responses; they do not measure LLM improvement.
"""

from copy import deepcopy
from dataclasses import replace
import json
import socket

import pytest

from ops_agent.diagnosis_agent import ModelResponse
from ops_agent.diagnosis_quality import QualityAttempt, QualityRubric, score_attempt
from ops_agent.diagnosis_scenarios import load_scenario_catalog
from ops_agent.diagnosis_v2_models import DiagnosisPolicyV2
from ops_agent.diagnosis_validator import DiagnosisValidationError
from ops_agent.scenario_agent import (
    RecordedBranchModelClient, run_scenario_diagnosis, scenario_prompt_identity,
)
from ops_agent.scenario_contract import OUTPUT_CONTRACT, SCENARIO_PROMPT_VERSION
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG, DEFAULT_RUBRIC


class ScriptedRepairClient(RecordedBranchModelClient):
    def __init__(self, fault, repair_fault=None):
        super().__init__()
        self.fault = fault
        self.repair_fault = repair_fault
        self.corrected = None
        self.repair_requests = []
        self.requests = []

    def create(self, payload):
        self.requests.append(deepcopy(payload))
        user = payload["input"][-1]
        task = json.loads(user["content"]) if user.get("role") == "user" else {}
        if task.get("task") == "repair_ops_diagnosis_v2_output_contract_only":
            self.repair_requests.append(task)
            assert "tools" not in payload
            assert task["output_contract"] == OUTPUT_CONTRACT
            assert "ALL output contract rules" in task["repair_instructions"]
            result = deepcopy(self.corrected)
            if self.repair_fault == "unknown-citation":
                result["hypotheses"][0]["supporting_evidence_ids"] = ["fabricated"]
            elif self.repair_fault == "invalid-gap":
                result["hypotheses"][-1]["evidence_gaps"] = ["wrong_gap_code"]
            elif self.repair_fault == "invent-tool":
                return ModelResponse(response_id="bad-repair", output=[{
                    "type": "function_call", "name": "get_runtime_image", "arguments": "{}",
                }], output_text=None, input_tokens=0, output_tokens=0, total_tokens=0)
            return ModelResponse(response_id="scripted-repair", output=[],
                output_text=json.dumps(result), input_tokens=0, output_tokens=0, total_tokens=0)
        response = super().create(payload)
        if response.output_text is None:
            return response
        result = json.loads(response.output_text)
        self.corrected = deepcopy(result)
        if self.fault == "missing-gap":
            result["hypotheses"][0]["evidence_gaps"] = []
        else:
            extra = {
                "hypothesis": "CONSUMER_REBALANCE_SUSPECTED", "support_status": "INSUFFICIENT",
                "reason_codes": ["UNAVAILABLE_OBSERVATION"],
                "supporting_evidence_ids": [], "conflicting_evidence_ids": [],
                "evidence_gaps": ["wrong_gap_code"],
            }
            good = deepcopy(extra)
            good["evidence_gaps"] = ["CONSUMER_REBALANCE_TELEMETRY_UNAVAILABLE"]
            self.corrected["hypotheses"].append(good)
            result["hypotheses"].append(extra)
            if self.fault == "multiple-errors":
                # First error masks the later rebalance gap; one repair must check both.
                result["hypotheses"][0]["support_status"] = "CONFLICTED"
                result["hypotheses"][0]["conflicting_evidence_ids"] = []
        return replace(response, output_text=json.dumps(result))


def execute(client, fixture="worker-db-path-pressure"):
    catalog = load_scenario_catalog(DEFAULT_CATALOG)
    return run_scenario_diagnosis(catalog=catalog, fixture_id=fixture, client=client,
        policy=DiagnosisPolicyV2(model="scripted-contract-test", max_retries=0,
                                  max_output_repairs=1)), catalog


@pytest.mark.parametrize("fault,fixture,error", [
    ("missing-gap", "telemetry-unavailable", "INSUFFICIENT_REQUIRES_EVIDENCE_GAP"),
    ("rebalance-gap", "worker-db-path-pressure", "REBALANCE_GAP_REQUIRED"),
    ("multiple-errors", "worker-db-path-pressure", "CONFLICTED_REQUIRES_BOTH_CITATION_SIDES"),
])
def test_repair_payload_carries_complete_contract_and_one_scripted_repair_can_pass(fault, fixture, error):
    client = ScriptedRepairClient(fault)
    run, catalog = execute(client, fixture)
    assert len(client.repair_requests) == 1
    assert client.repair_requests[0]["validation_error"]["code"] == error
    assert run.output_repairs_used == 1
    assert [item.result for item in run.validation_attempts] == ["INVALID", "VALID"]
    assert run.validation_attempts[0].error_code == error
    rubric = QualityRubric.model_validate_json(DEFAULT_RUBRIC.read_bytes())
    assert score_attempt(QualityAttempt(attempt_id="repair", fixture_id=fixture,
        execution_kind="scripted", run=run.model_dump(mode="json")),
        catalog=catalog, rubric=rubric)["quality_pass"]


@pytest.mark.parametrize("fault,code", [
    ("unknown-citation", "UNKNOWN_EVIDENCE_CITATION"),
    ("invalid-gap", "REBALANCE_GAP_REQUIRED"),
    ("invent-tool", "REPAIR_TOOL_CALL_FORBIDDEN"),
])
def test_second_invalid_result_is_rejected_without_further_repair(fault, code):
    client = ScriptedRepairClient("rebalance-gap", repair_fault=fault)
    with pytest.raises(DiagnosisValidationError) as error:
        execute(client)
    assert error.value.code == code
    assert len(client.repair_requests) == 1


def test_same_contract_for_every_scenario_without_answer_labels(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("offline contract tests must not use network")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    catalog = load_scenario_catalog(DEFAULT_CATALOG)
    systems = []
    for case in catalog.scenarios:
        client = ScriptedRepairClient("missing-gap" if case.fixture_id == "telemetry-unavailable" else "rebalance-gap")
        execute(client, case.fixture_id)
        initial = json.loads(client.requests[0]["input"][1]["content"])
        assert initial["output_contract"] == OUTPUT_CONTRACT
        systems.append(client.requests[0]["input"][0]["content"])
    assert len(set(systems)) == 1
    contract = json.dumps(OUTPUT_CONTRACT)
    assert all(case.fixture_id not in contract and case.title not in contract for case in catalog.scenarios)
    assert "expected_primary_hypothesis" not in contract
    assert "expected_next_tools" not in contract
    assert scenario_prompt_identity()["version"] == SCENARIO_PROMPT_VERSION
    assert len(scenario_prompt_identity()["sha256"]) == 64


def test_semantically_wrong_but_contract_valid_abstention_failure_still_fails_quality():
    class Overconfident(RecordedBranchModelClient):
        def create(self, payload):
            response = super().create(payload)
            if response.output_text is None:
                return response
            evidence = self._evidence_from_history(payload["input"])
            result = json.loads(response.output_text)
            result["hypotheses"][0].update(
                hypothesis="WORKER_PATH_PRESSURE_SUSPECTED", support_status="SUPPORTED",
                supporting_evidence_ids=[evidence["get_worker_stage_latency"]["evidence_id"]],
            )
            result["stop_reason"] = "sufficient_evidence"
            return replace(response, output_text=json.dumps(result))
    run, catalog = execute(Overconfident(), "telemetry-unavailable")
    assert run.output_repairs_used == 0
    assert run.hypotheses[0].support_status.value == "SUPPORTED"
    rubric = QualityRubric.model_validate_json(DEFAULT_RUBRIC.read_bytes())
    result = score_attempt(QualityAttempt(attempt_id="overconfident", fixture_id="telemetry-unavailable",
        execution_kind="scripted", run=run.model_dump(mode="json")), catalog=catalog, rubric=rubric)
    assert result["contract_pass"]
    assert not result["checks"]["abstention_correct"]
    assert not result["quality_pass"]
