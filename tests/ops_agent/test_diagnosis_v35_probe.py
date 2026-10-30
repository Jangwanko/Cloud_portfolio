"""New primary signal/status combinations with frozen v3.5 semantics."""

from copy import deepcopy
import hashlib
import json

import pytest

from ops_agent.citation_selection_v3 import selectable_evidence_ids, validate_citation_selection
from ops_agent.diagnosis_quality_v3 import fixed_run_v3, score_v3
from ops_agent.diagnosis_scenarios import ControlledScenarioRegistry, load_scenario_catalog
from ops_agent.diagnosis_v2_models import DiagnosisEvidenceV2
from ops_agent.diagnosis_v3_models import AgentDecisionV3
from ops_agent.diagnosis_v3_validator import validate_decision_v3
from ops_agent.diagnosis_validator import DiagnosisValidationError
from ops_agent.evaluation_models import canonical_sha256
from ops_agent.investigation_gate_v3 import PRIMARY_TOOLS, pending_investigation
from scripts.build_diagnosis_v35_probe import ROOT, build_probe_cases, export_probe
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG
from scripts.run_diagnosis_v3_pilot import run_pilot_v3
from tests.ops_agent.test_scenario_agent_v3 import OfflineClient

FIXTURES = ROOT / "ops_agent/fixtures/diagnosis/novel_v2"


@pytest.mark.parametrize("index", range(4))
def test_authored_expected_sets_match_rule_baseline(index):
    catalog, rubric = build_probe_cases()
    run = fixed_run_v3(catalog=catalog, fixture_id=catalog.scenarios[index].fixture_id)
    assert score_v3(run, catalog=catalog, rubric=rubric)["joint_pass"]
    if index == 0:
        assert run["decision"]["conclusion"] == "ABSTAIN"
        assert not run["decision"]["observations"] and run["decision"]["investigation_gaps"]
    if index == 3:
        assert len(run["decision"]["hypotheses"]) == 2 and len(run["decision"]["observations"]) == 2


def signature(catalog, case):
    registry = ControlledScenarioRegistry(catalog=catalog, fixture_id=case.fixture_id)
    evidence = [registry.execute(tool) for tool in PRIMARY_TOOLS]
    return tuple((item.status.value, item.freshness.status.value, item.summary.get("semantic_flag")) for item in evidence)


def test_new_semantic_combinations_do_not_duplicate_either_previous_catalog():
    catalog, _ = build_probe_cases()
    previous = [load_scenario_catalog(DEFAULT_CATALOG),
                load_scenario_catalog(ROOT / "ops_agent/fixtures/diagnosis/novel_v1/catalog.json")]
    old_signatures = {signature(old, case) for old in previous for case in old.scenarios}
    new_signatures = {signature(catalog, case) for case in catalog.scenarios}
    assert len(new_signatures) == 4 and not new_signatures & old_signatures


def test_static_probe_reproduces_expected_values_and_design_hashes():
    catalog, rubric = build_probe_cases()
    assert json.loads((FIXTURES / "catalog.json").read_bytes()) == catalog.model_dump(mode="json")
    assert json.loads((FIXTURES / "rubric.json").read_bytes()) == rubric
    design = json.loads((FIXTURES / "design.json").read_bytes())
    assert design["frozen_execution_identity"]["version"] == "ops.diagnosis.execution.v3.5"
    assert design["catalog_sha256"] == canonical_sha256(catalog.model_dump(mode="json"))
    assert design["rubric_sha256"] == canonical_sha256(rubric)
    assert design["author_script_sha256"] == hashlib.sha256((ROOT / "scripts/build_diagnosis_v35_probe.py").read_bytes()).hexdigest()


@pytest.mark.parametrize("fixture_id,tool,cause", [
    ("probe2-02", "get_postgres_health", "POSTGRES_GUARDRAIL_PRESSURE_SUSPECTED"),
    ("probe2-04", "get_keda_status", "WORKER_CAPACITY_SHORTFALL_SUSPECTED"),
])
def test_provided_stale_normal_context_cannot_be_signed_causal_evidence(fixture_id, tool, cause):
    catalog, _ = build_probe_cases()
    run = fixed_run_v3(catalog=catalog, fixture_id=fixture_id)
    raw = deepcopy(run["decision"])
    evidence = [DiagnosisEvidenceV2.model_validate(item) for item in run["acquired_evidence"]]
    value = next(item.evidence_id for item in evidence if item.tool_id == tool)
    raw["context_evidence_ids"].remove(value)
    if tool == "get_postgres_health":
        raw["hypotheses"].append({"hypothesis": cause, "support_status": "INSUFFICIENT",
            "supporting_evidence_ids": [], "conflicting_evidence_ids": [value], "evidence_gaps": ["STALE_TELEMETRY"]})
    else:
        next(item for item in raw["hypotheses"] if item["hypothesis"] == cause)["supporting_evidence_ids"] = [value]
    decision = AgentDecisionV3.model_validate(raw)
    validate_citation_selection(decision, selectable_evidence_ids(
        initial_evidence_ids=catalog.activation["evidence_ids"], acquired_evidence=evidence))
    with pytest.raises(DiagnosisValidationError) as error:
        validate_decision_v3(decision, initial_evidence_ids=catalog.activation["evidence_ids"], acquired_evidence=evidence)
    assert error.value.code == "V3_CONTEXT_IS_NOT_CAUSAL_SUPPORT"


@pytest.mark.parametrize("index,expected", [(0, []), (1, ["get_keda_status"]), (2, []), (3, ["get_keda_status"])])
def test_coverage_and_conditional_keda_use_fresh_actual_replica_signal(index, expected):
    catalog, _ = build_probe_cases()
    registry = ControlledScenarioRegistry(catalog=catalog, fixture_id=catalog.scenarios[index].fixture_id)
    evidence = [registry.execute(tool) for tool in PRIMARY_TOOLS]
    assert pending_investigation(evidence) == expected


def test_probe_export_and_complete_offline_batch_preserve_existing_protocol(tmp_path):
    target = tmp_path / "probe"
    assert export_probe(target) == 0
    with pytest.raises(FileExistsError):
        export_probe(target)
    assert run_pilot_v3(output_dir=tmp_path / "pilot", model="offline", client_factory=OfflineClient,
        catalog_path=target / "catalog.json", rubric_path=target / "rubric.json") == 0
    summary = json.loads((tmp_path / "pilot/summary.json").read_bytes())
    assert summary["live"]["joint_pass"] == summary["fixed_runbook"]["joint_pass"] == 8
    assert summary["api_request_count"] == 40
