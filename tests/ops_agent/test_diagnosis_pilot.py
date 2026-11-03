"""Pilot tests use injected scripted clients; no credentials or network required."""

from dataclasses import replace
import json
import socket

import pytest

from ops_agent.diagnosis_agent import ModelResponse
from ops_agent.scenario_agent import RecordedBranchModelClient
from scripts.run_diagnosis_pilot import TracedBlindClient, blind_payload, run_pilot


def test_blinding_covers_encoded_initial_and_tool_provenance():
    fixture = "worker-db-path-pressure"
    original = {"input": [{"content": json.dumps({"scenario_fixture_id": fixture})},
                          {"output": json.dumps({"provenance": {"fixture_id": fixture},
                                                "evidence_id": "opaque-evidence", "metric": 14.3})}]}
    visible = blind_payload(original, fixture_id=fixture, alias="case-opaque")
    assert fixture not in json.dumps(visible)
    assert fixture in json.dumps(original)
    assert "opaque-evidence" in json.dumps(visible)
    assert json.loads(visible["input"][1]["output"])["metric"] == 14.3


@pytest.mark.parametrize("payload", [
    {"expected_primary_hypothesis": "cause"},
    {"input": [{"content": json.dumps({"branch_expectations": ["answer"]})}]},
])
def test_blinding_rejects_answer_metadata(payload):
    with pytest.raises(ValueError, match="answer metadata"):
        blind_payload(payload, fixture_id="original", alias="case-opaque")


def test_trace_request_cap_and_failure_text_redaction(tmp_path):
    class Broken:
        def create(self, payload):
            raise RuntimeError("sensitive-exception-value")
    budget = {"used": 0}
    client = TracedBlindClient(delegate=Broken(), fixture_id="original",
                              trace_dir=tmp_path, batch_budget=budget)
    with pytest.raises(RuntimeError):
        client.create({"input": []})
    assert budget["used"] == 1
    assert "sensitive-exception-value" not in (tmp_path / "turn-01.json").read_text()
    assert client.usage()["input_tokens"] is None
    assert client.usage()["usage_coverage_complete"] is False
    budget["used"] = 48
    with pytest.raises(ValueError, match="budget"):
        client.create({"input": []})
    assert budget["used"] == 48


def test_pilot_schedule_paired_results_and_traces_network_free(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("tests must not call API")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    output = tmp_path / "pilot"
    assert run_pilot(output_dir=output, model="injected-scripted-client",
                     client_factory=RecordedBranchModelClient) == 0
    summary = json.loads((output / "summary.json").read_bytes())
    assert summary["scheduled_attempt_count"] == summary["executed_attempt_count"] == 8
    assert summary["api_request_count"] == 28  # Fake client: 20 tools + 8 final decisions.
    assert summary["live"]["quality_success_rate"] == 1
    assert summary["fixed_runbook"]["quality_success_rate"] == 1
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["status"] == "completed"
    assert manifest["prompt_identity"]["version"] == "ops.diagnosis.prompt.v2"
    prompt = json.loads((output / "prompt-contract.json").read_bytes())
    assert prompt["identity"] == manifest["prompt_identity"]
    assert len(manifest["schedule"]) == 8
    labels = [item["fixture_id"] for item in manifest["schedule"]]
    for path in output.glob("pilot-*/turn-*.json"):
        request = json.loads(path.read_bytes())["request"]
        assert all(label not in json.dumps(request) for label in labels)
    with pytest.raises(FileExistsError):
        run_pilot(output_dir=output, model="injected-scripted-client",
                  client_factory=RecordedBranchModelClient)


def test_transport_failure_aborts_and_separates_unrun_trials(tmp_path):
    class Broken:
        def create(self, payload):
            raise RuntimeError("simulated transport failure")
    output = tmp_path / "pilot"
    assert run_pilot(output_dir=output, model="injected-broken-client", client_factory=Broken) == 2
    summary = json.loads((output / "summary.json").read_bytes())
    assert summary["executed_attempt_count"] == 1
    assert summary["unrun_attempt_count"] == 7
    assert summary["live"]["attempt_count"] == 1
    assert summary["live"]["final_contract_failure_count"] == 1
    assert summary["usage_coverage_complete"] is False
    assert json.loads((output / "manifest.json").read_bytes())["status"] == "aborted"


def test_invalid_schema_remains_in_denominator_without_aborting(tmp_path):
    class Malformed:
        def create(self, payload):
            return ModelResponse(response_id="malformed", output=[], output_text="invalid JSON",
                                 input_tokens=11, output_tokens=2, total_tokens=13)
    output = tmp_path / "pilot"
    assert run_pilot(output_dir=output, model="injected-malformed-client", client_factory=Malformed) == 1
    summary = json.loads((output / "summary.json").read_bytes())
    assert summary["executed_attempt_count"] == 8
    assert summary["live"]["final_contract_failure_count"] == 8
    assert summary["live"]["first_pass_unknown_count"] == 0
    assert summary["live"]["telemetry"]["input_tokens"]["total_known"] == 88


def test_repair_turn_failure_preserves_initial_failure_and_tokens(tmp_path):
    class FailedRepair(RecordedBranchModelClient):
        def create(self, payload):
            if "repair_ops_diagnosis" in json.dumps(payload["input"]):
                return ModelResponse(response_id="failed-repair", output=[], output_text="invalid JSON",
                                     input_tokens=5, output_tokens=2, total_tokens=7)
            response = super().create(payload)
            if response.output_text:
                value = json.loads(response.output_text)
                value["hypotheses"][0]["support_status"] = "SUPPORTED"
                value["hypotheses"][0]["supporting_evidence_ids"] = ["fabricated"]
                value["stop_reason"] = "sufficient_evidence"
                return replace(response, output_text=json.dumps(value))
            return response
    output = tmp_path / "pilot"
    assert run_pilot(output_dir=output, model="injected-repair-client", client_factory=FailedRepair) == 1
    summary = json.loads((output / "summary.json").read_bytes())
    assert summary["live"]["final_contract_failure_count"] == 8
    assert summary["live"]["first_pass_unknown_count"] == 0
    state = json.loads((output / "pilot-1-1/state.json").read_bytes())
    assert state["live_attempt"]["repair_used"] == 1
    assert state["live_score"]["repair_used"] == 1
    assert state["live_score"]["first_pass_contract"] is False
    assert state["trace_usage"]["input_tokens"] == 5
