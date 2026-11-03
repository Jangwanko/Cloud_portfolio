"""Bounded acquisition, strict repair, anti-abstention and durable pilot regression tests."""

from copy import deepcopy
import json

import pytest

from ops_agent.diagnosis_agent import ModelResponse
from ops_agent.diagnosis_quality_v3 import fixed_decision_v3, fixed_run_v3, score_v3
from ops_agent.diagnosis_scenarios import load_scenario_catalog
from ops_agent.diagnosis_v2_models import DiagnosisEvidenceV2
from ops_agent.scenario_agent_v3 import (
    EXECUTION_GUIDANCE, FINAL_AUDIT, enforce_repair_scope, execution_contract_v3, run_scenario_v3,
)
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG
from scripts.run_diagnosis_v3_pilot import DEFAULT_RUBRIC_V3, export_compact_v3, run_pilot_v3


@pytest.fixture
def catalog():
    return load_scenario_catalog(DEFAULT_CATALOG)


@pytest.fixture
def rubric():
    return json.loads(DEFAULT_RUBRIC_V3.read_text(encoding="utf-8"))


TOOLS = ["get_worker_stage_latency", "get_postgres_health", "get_worker_replica_status", "get_keda_status"]


class OfflineClient:
    def __init__(self, tools=TOOLS, mutate=None, repair=False):
        self.tools, self.mutate, self.repair = tools, mutate, repair
        self.requests = []

    def create(self, payload):
        self.requests.append(payload)
        if len(self.requests) <= len(self.tools):
            tool = self.tools[len(self.requests) - 1]
            return ModelResponse("test", [{"type": "function_call", "name": tool,
                "call_id": f"call-{len(self.requests)}", "arguments": "{}"}], None, 1, 1, 2)
        activation = json.loads(payload["input"][1]["content"])["activation"]
        evidence = [DiagnosisEvidenceV2.model_validate_json(item["output"])
            for item in payload["input"] if item.get("type") == "function_call_output"]
        decision = fixed_decision_v3(activation=activation, evidence=evidence).model_dump(mode="json")
        if self.mutate and (not self.repair or len(self.requests) == len(self.tools) + 1):
            self.mutate(decision)
        return ModelResponse("test", [], json.dumps(decision), 1, 1, 2)


@pytest.mark.parametrize("index", range(4))
def test_isolated_loop_and_rule_baseline(catalog, rubric, index):
    fixture_id = catalog.scenarios[index].fixture_id
    client = OfflineClient()
    run = run_scenario_v3(catalog=catalog, fixture_id=fixture_id, client=client, model="offline")
    assert run["contract_pass"] and run["first_pass_contract"]
    assert len(client.requests) == 5
    assert "tools" not in client.requests[-1]
    assert score_v3(run, catalog=catalog, rubric=rubric)["joint_pass"]
    assert score_v3(fixed_run_v3(catalog=catalog, fixture_id=fixture_id), catalog=catalog, rubric=rubric)["joint_pass"]


def test_repair_once_without_rediagnosis(catalog):
    def bad_conclusion(value):
        value["conclusion"] = "SUPPORTED_CANDIDATES"
    client = OfflineClient(mutate=bad_conclusion, repair=True)
    run = run_scenario_v3(catalog=catalog, fixture_id="worker-db-path-pressure", client=client, model="offline")
    assert run["contract_pass"] and not run["first_pass_contract"] and run["repair_used"] == 1
    assert len(client.requests) == 6
    assert "tools" not in client.requests[-1]


def test_invalid_repair_stays_failed(catalog):
    client = OfflineClient(mutate=lambda value: value.update(conclusion="SUPPORTED_CANDIDATES"))
    run = run_scenario_v3(catalog=catalog, fixture_id="worker-db-path-pressure", client=client, model="offline")
    assert not run["contract_pass"] and len(run["decisions"]) == 2
    assert run["failure_code"] == "V3_CONCLUSION_MISMATCH"
    assert len(client.requests) == 6


def test_repair_cannot_introduce_candidate_or_evidence(catalog):
    original = fixed_run_v3(catalog=catalog, fixture_id="worker-db-path-pressure")["decision"]
    repaired = deepcopy(original)
    repaired["context_evidence_ids"].append("fabricated")
    with pytest.raises(ValueError, match="REDIAGNOSIS"):
        enforce_repair_scope(original, repaired)
    repaired = deepcopy(original)
    repaired["hypotheses"] = [{"hypothesis": "HOT_KEY_SUSPECTED", "support_status": "INSUFFICIENT"}]
    with pytest.raises(ValueError, match="REDIAGNOSIS"):
        enforce_repair_scope(original, repaired)


@pytest.mark.parametrize("tools", [[TOOLS[0], TOOLS[0]], TOOLS + ["get_pod_restart_summary"], ["shell"]])
def test_repeat_unknown_and_fifth_tools_rejected(catalog, tools):
    client = OfflineClient(tools=tools)
    run = run_scenario_v3(catalog=catalog, fixture_id="worker-db-path-pressure", client=client, model="offline")
    assert not run["contract_pass"]
    assert len(run["tool_calls"]) <= 4 and len(client.requests) <= 5


@pytest.mark.parametrize("fixture_id", ["worker-replica-shortfall", "postgres-path-degradation"])
def test_blanket_abstention_fails_positive_cases(catalog, rubric, fixture_id):
    run = fixed_run_v3(catalog=catalog, fixture_id=fixture_id)
    run["decision"].update(hypotheses=[], conclusion="ABSTAIN", context_evidence_ids=[],
                            investigation_gaps=["CAUSAL_DISCRIMINATOR_UNAVAILABLE"])
    score = score_v3(run, catalog=catalog, rubric=rubric)
    assert score["contract_pass"] and score["observations_pass"]
    assert not score["causes_pass"] and not score["joint_pass"]


def test_keda_completeness_separate_from_causal_quality(catalog, rubric):
    # Imported pre-gate records still use the unchanged scorer's separate dimensions.
    run = fixed_run_v3(catalog=catalog, fixture_id="worker-replica-shortfall")
    omitted = run["acquired_evidence"].pop()
    run["tool_calls"].pop()
    run["decision"]["context_evidence_ids"].remove(omitted["evidence_id"])
    score = score_v3(run, catalog=catalog, rubric=rubric)
    assert score["contract_pass"] and score["causes_pass"] and score["observations_pass"]
    assert not score["completeness_pass"] and not score["joint_pass"]


def test_forged_acquisition_fails_scoring(catalog, rubric):
    run = fixed_run_v3(catalog=catalog, fixture_id="worker-db-path-pressure")
    run["acquired_evidence"][0]["summary"]["semantic_flag"] = "FORGED"
    assert not score_v3(run, catalog=catalog, rubric=rubric)["contract_pass"]


def test_guidance_preserves_model_errors_for_scoring(catalog, rubric):
    def omit_observations(value):
        value["observations"] = []
    client = OfflineClient(mutate=omit_observations)
    run = run_scenario_v3(catalog=catalog, fixture_id="worker-replica-shortfall", client=client, model="offline")
    assert run["contract_pass"] and not run["decision"]["observations"]
    assert not score_v3(run, catalog=catalog, rubric=rubric)["joint_pass"]
    assert client.requests[-1]["input"][-1]["content"] == FINAL_AUDIT


def test_common_guidance_has_no_fixture_answers(catalog):
    from ops_agent.diagnosis_v3_validator import CAUSE_RULES, SIGNAL_RULES
    assert execution_contract_v3()["identity"]["version"] == "ops.diagnosis.execution.v3.5"
    assert execution_contract_v3()["execution_guidance"] == EXECUTION_GUIDANCE
    for case in catalog.scenarios:
        assert case.fixture_id not in EXECUTION_GUIDANCE
    assert "expected_primary_hypothesis" not in EXECUTION_GUIDANCE
    for signal, (tool, flag) in SIGNAL_RULES.items():
        assert f"{tool} / {flag} -> {signal}" in EXECUTION_GUIDANCE
    for cause, rules in CAUSE_RULES.items():
        assert cause in EXECUTION_GUIDANCE
        for tool, flag in rules.values():
            assert f"{tool} / {flag}" in EXECUTION_GUIDANCE


def test_pilot_preregisters_before_requests_and_blinds_labels(tmp_path):
    output = tmp_path / "pilot"
    def factory():
        assert (output / "manifest.json").exists() and (output / "rubric.json").exists()
        return OfflineClient()
    assert run_pilot_v3(output_dir=output, model="offline", client_factory=factory) == 0
    summary = json.loads((output / "summary.json").read_bytes())
    assert summary["live"]["joint_pass"] == summary["fixed_runbook"]["joint_pass"] == 8
    assert summary["api_request_count"] == 40
    wire = json.loads((output / "pilot-1-1/turn-02.json").read_bytes())["request"]
    assert "worker-db-path-pressure" not in json.dumps(wire)
    assert "expected_primary_hypothesis" not in json.dumps(wire)
    compact_path = tmp_path / "compact.json"
    export_compact_v3(source_dir=output, output_path=compact_path)
    assert json.loads(compact_path.read_bytes())["summary"] == summary
    with pytest.raises(FileExistsError):
        export_compact_v3(source_dir=output, output_path=compact_path)
    summary["live"]["joint_pass"] = 7
    (output / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match="summary mismatch"):
        export_compact_v3(source_dir=output, output_path=tmp_path / "bad-compact.json")
    with pytest.raises(FileExistsError):
        run_pilot_v3(output_dir=output, model="offline", client_factory=factory)


def test_transport_failure_is_not_success_or_silently_unrun(tmp_path):
    class Broken:
        def create(self, payload):
            raise RuntimeError("secret must not be persisted")
    output = tmp_path / "pilot"
    assert run_pilot_v3(output_dir=output, model="offline", client_factory=Broken) == 2
    summary = json.loads((output / "summary.json").read_bytes())
    assert summary["executed_attempt_count"] == 1 and summary["unrun_attempt_count"] == 7
    assert summary["live"]["joint_pass"] == 0 and not summary["usage_coverage_complete"]
    assert "secret must not be persisted" not in (output / "pilot-1-1/turn-01.json").read_text()
