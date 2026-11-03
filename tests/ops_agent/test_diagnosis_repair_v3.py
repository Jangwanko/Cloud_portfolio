"""Narrow removal permissions and preservation of first-pass failures."""

from copy import deepcopy
import json

import pytest

from ops_agent.diagnosis_quality_v3 import fixed_run_v3, score_v3
from ops_agent.diagnosis_repair_v3 import enforce_repair_scope, removable_observations
from ops_agent.diagnosis_scenarios import load_scenario_catalog
from ops_agent.diagnosis_v2_models import DiagnosisEvidenceV2
from ops_agent.scenario_agent_v3 import run_scenario_v3
from scripts.run_diagnosis_repair_regression import initial_score_v3, run_regression
from tests.ops_agent.test_scenario_agent_v3 import OfflineClient


@pytest.fixture
def case():
    from scripts.build_diagnosis_v3_novel_cases import ROOT
    path = ROOT / "ops_agent/fixtures/diagnosis/novel_v1"
    catalog = load_scenario_catalog(path / "catalog.json")
    rubric = json.loads((path / "rubric.json").read_bytes())
    run = fixed_run_v3(catalog=catalog, fixture_id="novel-03")
    evidence = [DiagnosisEvidenceV2.model_validate(item) for item in run["acquired_evidence"]]
    original = deepcopy(run["decision"])
    add_false_pg_observation(original)
    return catalog, rubric, run, evidence, original


def add_false_pg_observation(raw):
    value = next(value for value in raw["context_evidence_ids"] if value.startswith("diagnosis.get_postgres_health."))
    raw["observations"].append({"signal": "POSTGRES_GUARDRAIL_DEGRADED", "evidence_ids": [value]})


def test_only_directly_contradicted_observation_can_be_removed(case):
    catalog, rubric, run, evidence, original = case
    audit = enforce_repair_scope(original, run["decision"], acquired_evidence=evidence)
    assert audit["removed_observations"] == removable_observations(original, evidence)
    assert audit["removed_observations"][0]["signal"] == "POSTGRES_GUARDRAIL_DEGRADED"
    assert score_v3(run, catalog=catalog, rubric=rubric)["joint_pass"]


@pytest.mark.parametrize("change", ["no_evidence", "stale", "unavailable", "unknown_id", "wrong_tool", "positive_flag"])
def test_unverified_or_supported_observation_removal_rejected(case, change):
    _, _, run, evidence, original = case
    pg = next(item for item in evidence if item.tool_id == "get_postgres_health")
    if change == "no_evidence":
        evidence = []
    elif change == "unknown_id":
        original["observations"][-1]["evidence_ids"] = ["not-acquired"]
    elif change == "wrong_tool":
        original["observations"][-1]["evidence_ids"] = [evidence[2].evidence_id]
    else:
        raw = pg.model_dump(mode="json")
        if change == "stale":
            raw["freshness"]["status"] = "STALE"
        elif change == "unavailable":
            raw["status"] = "UNAVAILABLE"
            raw["error_code"] = "SOURCE_TIMEOUT"
        else:
            raw["summary"]["semantic_flag"] = "POSTGRES_PATH_DEGRADED"
        replacement = DiagnosisEvidenceV2.model_validate(raw)
        evidence = [replacement if item.evidence_id == pg.evidence_id else item for item in evidence]
    with pytest.raises(ValueError, match="scope"):
        enforce_repair_scope(original, run["decision"], acquired_evidence=evidence)


@pytest.mark.parametrize("change", ["remove_valid", "add_observation", "change_status", "add_hypothesis", "add_citation"])
def test_valid_observations_and_causal_scope_remain_protected(case, change):
    _, _, run, evidence, original = case
    repaired = deepcopy(run["decision"])
    if change == "remove_valid":
        repaired["observations"] = []
    elif change == "add_observation":
        repaired["observations"].append({"signal": "WORKER_STAGE_ELEVATED", "evidence_ids": []})
    elif change == "change_status":
        repaired["hypotheses"][0]["support_status"] = "INSUFFICIENT"
    elif change == "add_hypothesis":
        repaired["hypotheses"].append({"hypothesis": "HOT_KEY_SUSPECTED", "support_status": "INSUFFICIENT"})
    else:
        repaired["context_evidence_ids"].append("new-id")
    with pytest.raises(ValueError):
        enforce_repair_scope(original, repaired, acquired_evidence=evidence)


def test_model_repairs_false_observation_with_audit_and_first_score_retained(case):
    catalog, rubric, _, _, _ = case
    client = OfflineClient(mutate=add_false_pg_observation, repair=True)
    run = run_scenario_v3(catalog=catalog, fixture_id="novel-03", client=client, model="offline")
    assert run["contract_pass"] and not run["first_pass_contract"] and run["repair_used"] == 1
    assert run["decisions"][0]["failure_code"] == "V3_OBSERVATION_NOT_MEASURED"
    assert run["repair_scope_audit"]["removed_observations"][0]["signal"] == "POSTGRES_GUARDRAIL_DEGRADED"
    assert not initial_score_v3(run, catalog=catalog, rubric=rubric)["joint_pass"]
    assert score_v3(run, catalog=catalog, rubric=rubric)["joint_pass"]
    request = json.loads(client.requests[-1]["input"][-1]["content"])
    assert request["removable_observations"] == run["repair_scope_audit"]["removed_observations"]
    assert len(client.requests) == 6 and "tools" not in client.requests[-1]


def test_initial_joint_score_detects_contract_valid_omission(case):
    catalog, rubric, _, _, _ = case
    run = run_scenario_v3(catalog=catalog, fixture_id="novel-03",
        client=OfflineClient(mutate=lambda value: value.update(observations=[])), model="offline")
    first = initial_score_v3(run, catalog=catalog, rubric=rubric)
    assert first["contract_pass"] and not first["joint_pass"]


def test_complete_16_trial_offline_protocol_and_source_export(tmp_path):
    assert run_regression(output_dir=tmp_path / "regression", model="offline", client_factory=OfflineClient) == 0
    summary = json.loads((tmp_path / "regression/summary.json").read_bytes())
    assert summary["first_pass_joint_pass"] == summary["final_joint_pass"] == 16
    assert summary["api_request_count"] == 80 and summary["observation_removal_attempt_count"] == 0
    assert summary["usage_coverage_complete"]
    with pytest.raises(FileExistsError):
        run_regression(output_dir=tmp_path / "regression", model="offline", client_factory=OfflineClient)
