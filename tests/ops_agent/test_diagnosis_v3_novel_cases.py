"""Independent expected combinations, stale traps and frozen-model transfer protocol."""

from copy import deepcopy
import hashlib
import json

import pytest

from ops_agent.diagnosis_quality_v3 import fixed_run_v3, score_v3
from ops_agent.diagnosis_scenarios import ControlledScenarioRegistry
from ops_agent.diagnosis_v3_models import AgentDecisionV3
from ops_agent.diagnosis_v3_validator import validate_decision_v3
from ops_agent.diagnosis_validator import DiagnosisValidationError
from ops_agent.evaluation_models import canonical_sha256
from ops_agent.scenario_agent_v3 import execution_contract_v3
from scripts.build_diagnosis_v3_novel_cases import ROOT, build_novel_cases, export_novel_cases
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG
from scripts.run_diagnosis_pilot import blind_payload
from scripts.run_diagnosis_v3_pilot import run_pilot_v3
from tests.ops_agent.test_scenario_agent_v3 import OfflineClient

FIXTURES = ROOT / "ops_agent/fixtures/diagnosis/novel_v1"


@pytest.mark.parametrize("index", range(4))
def test_novel_expected_cases_and_rule_baseline(index):
    catalog, rubric = build_novel_cases()
    fixture_id = catalog.scenarios[index].fixture_id
    run = fixed_run_v3(catalog=catalog, fixture_id=fixture_id)
    assert score_v3(run, catalog=catalog, rubric=rubric)["joint_pass"]
    if index == 0:
        assert len(run["decision"]["hypotheses"]) == 2
        assert {item["signal"] for item in run["decision"]["observations"]} == {
            "WORKER_REPLICA_SHORTFALL", "POSTGRES_GUARDRAIL_DEGRADED"}
    if index == 1:
        assert not run["decision"]["hypotheses"] and len(run["decision"]["observations"]) == 1
    if index == 2:
        assert [item["signal"] for item in run["decision"]["observations"]] == ["WORKER_REPLICA_SHORTFALL"]
    if index == 3:
        assert not run["decision"]["observations"] and run["decision"]["conclusion"] == "ABSTAIN"


def test_static_novel_fixtures_reproduce_and_differ_from_development():
    catalog, rubric = build_novel_cases()
    assert json.loads((FIXTURES / "catalog.json").read_bytes()) == catalog.model_dump(mode="json")
    assert json.loads((FIXTURES / "rubric.json").read_bytes()) == rubric
    old = json.loads(DEFAULT_CATALOG.read_bytes())
    assert not {case.fixture_id for case in catalog.scenarios} & {case["fixture_id"] for case in old["scenarios"]}
    old_signatures = {canonical_sha256(case["observations"]) for case in old["scenarios"]}
    assert not {canonical_sha256(case.model_dump(mode="json")["observations"]) for case in catalog.scenarios} & old_signatures
    design = json.loads((FIXTURES / "design.json").read_bytes())
    assert design["frozen_execution_identity"] == {
        "version": "ops.diagnosis.execution.v3.2",
        "sha256": "e976094b5337868fd6e1b3443ea2e27fbd5ab860fa9ed1a3f15acea77dd4b3a4"}
    assert design["catalog_sha256"] == canonical_sha256(catalog.model_dump(mode="json"))
    assert design["rubric_sha256"] == canonical_sha256(rubric)
    assert design["author_script_sha256"] == hashlib.sha256((ROOT / "scripts/build_diagnosis_v3_novel_cases.py").read_bytes()).hexdigest()


@pytest.mark.parametrize("tool,cause", [
    ("get_worker_replica_status", "WORKER_CAPACITY_SHORTFALL_SUSPECTED"),
    ("get_postgres_health", "POSTGRES_GUARDRAIL_PRESSURE_SUSPECTED"),
])
def test_stale_adverse_flag_cannot_support_a_cause(tool, cause):
    catalog, _ = build_novel_cases()
    registry = ControlledScenarioRegistry(catalog=catalog, fixture_id="novel-02")
    evidence = [registry.execute(tool)]
    raw = fixed_run_v3(catalog=catalog, fixture_id="novel-02")["decision"]
    raw.update(observations=[], context_evidence_ids=[], conclusion="SUPPORTED_CANDIDATES",
        hypotheses=[{"hypothesis": cause, "support_status": "SUPPORTED",
            "supporting_evidence_ids": [evidence[0].evidence_id], "conflicting_evidence_ids": [], "evidence_gaps": []}])
    with pytest.raises(DiagnosisValidationError, match="cannot substitute"):
        validate_decision_v3(AgentDecisionV3.model_validate(raw),
            initial_evidence_ids=catalog.activation["evidence_ids"], acquired_evidence=evidence)


def test_partial_compound_answer_and_blanket_abstention_fail():
    catalog, rubric = build_novel_cases()
    original = fixed_run_v3(catalog=catalog, fixture_id="novel-01")
    run = deepcopy(original)
    run["decision"]["hypotheses"] = run["decision"]["hypotheses"][:1]
    score = score_v3(run, catalog=catalog, rubric=rubric)
    assert score["contract_pass"] and not score["causes_pass"]
    run = deepcopy(original)
    run["decision"].update(hypotheses=[], conclusion="ABSTAIN", investigation_gaps=["MISSING_TELEMETRY"])
    assert not score_v3(run, catalog=catalog, rubric=rubric)["causes_pass"]


def test_novel_catalog_is_never_model_input_or_answer_metadata():
    catalog, rubric = build_novel_cases()
    item = ControlledScenarioRegistry(catalog=catalog, fixture_id="novel-01").execute("get_postgres_health")
    payload = {"input": [{"role": "user", "content": json.dumps({"activation": catalog.activation})},
                         {"type": "function_call_output", "output": item.model_dump_json()}]}
    wire = blind_payload(payload, fixture_id="novel-01", alias="case-random")
    assert "novel-01" not in json.dumps(wire) and "case-random" in json.dumps(wire)
    with pytest.raises(ValueError):
        blind_payload({"input": json.dumps(catalog.model_dump(mode="json"))}, fixture_id="novel-01", alias="case-random")


def test_novel_offline_batch_and_no_overwrite(tmp_path):
    target = tmp_path / "authored"
    assert export_novel_cases(target) == 0
    with pytest.raises(FileExistsError):
        export_novel_cases(target)
    assert run_pilot_v3(output_dir=tmp_path / "pilot", model="offline", client_factory=OfflineClient,
        catalog_path=target / "catalog.json", rubric_path=target / "rubric.json") == 0
    result = json.loads((tmp_path / "pilot/summary.json").read_bytes())
    assert result["live"]["joint_pass"] == result["fixed_runbook"]["joint_pass"] == 8
