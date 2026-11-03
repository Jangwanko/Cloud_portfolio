from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from ops_agent.diagnosis_agent import ModelResponse
from scripts.evaluate_investigation import baseline, cases, investigate, validate_step


def step(tool=None, cause="ABSTAIN", refs=None):
    return json.dumps({"hypotheses": ["startup or persistence failure"], "next_tool": tool,
        "reason": "Inspect direct evidence", "decision": None if tool else {
            "cause_code": cause, "evidence_ids": refs or ["E2"], "recommended_action": "Read missing evidence",
            "verification_steps": ["Check request persistence"], "uncertainty": "Not established"}})


def test_initial_and_followup_inputs_exclude_private_and_unqueried_evidence(tmp_path):
    case = cases()[0]
    case["expected"] = "SECRET_LABEL"
    case["evidence"]["db_activity"]["result"] = {"private_marker": "UNQUERIED_SECRET"}
    payloads = []
    def create(payload):
        payloads.append(deepcopy(payload))
        text = step("worker_logs") if len(payloads) == 1 else step(cause="STARTUP_FAILURE")
        return ModelResponse("id", [], text, 1, 1, 2)
    result = investigate(case, SimpleNamespace(create=create), "fake", tmp_path)
    assert result["tool_trace"] == ["worker_logs"]
    initial, followup = [json.dumps(p) for p in payloads]
    assert "No module named" not in initial
    assert "No module named" in followup
    assert all("UNQUERIED_SECRET" not in p and "SECRET_LABEL" not in p for p in (initial, followup))


def test_repeated_unknown_tools_and_unseen_citations_rejected():
    for text in (step("shell"), step("worker_logs"), step(refs=["E3"])):
        with pytest.raises(ValueError):
            validate_step(text, {"S0", "E2"}, ["worker_logs"])


def test_early_conclusion_and_empty_output_rejected():
    with pytest.raises(ValueError):
        validate_step(step(), {"S0"}, [])
    with pytest.raises(ValueError):
        validate_step("", {"S0", "E2"}, ["worker_logs"])


def test_missing_evidence_and_unrelated_db_wait_do_not_become_a_cause():
    lock = cases()[1]["evidence"]
    lock["db_activity"]["result"]["worker_pid"] = 999
    assert baseline(lock)["cause_code"] == "ABSTAIN"
    missing = cases()[2]["evidence"]
    assert baseline(missing)["cause_code"] == "ABSTAIN"
    assert len(baseline(missing)["tool_trace"]) == 4


def test_baseline_uses_correlated_pid_not_a_fixture_specific_number():
    lock = cases()[1]["evidence"]
    lock["db_activity"]["result"]["worker_pid"] = 777
    lock["worker_logs"]["result"]["lines"][1] = "Worker backend pid=777 INSERT awaiting completion"
    assert baseline(lock)["cause_code"] == "DB_LOCK_CONTENTION"


def test_model_failure_preserves_first_wire_response(tmp_path):
    client = SimpleNamespace(create=lambda _: ModelResponse("id", [], "", 2, 0, 2))
    with pytest.raises(ValueError):
        investigate(cases()[0], client, "fake", tmp_path)
    assert (tmp_path / "0-response.json").exists()
    assert not (tmp_path / "0-checkpoint.json").exists()
