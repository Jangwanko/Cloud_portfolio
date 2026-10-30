"""Finite request-time IDs do not weaken semantic or repair permissions."""

from copy import deepcopy
import json

import pytest

from ops_agent.citation_selection_v3 import (
    citation_schema_v3, decision_citations, selectable_evidence_ids, validate_citation_selection,
)
from ops_agent.diagnosis_quality_v3 import fixed_run_v3
from ops_agent.diagnosis_scenarios import load_scenario_catalog
from ops_agent.diagnosis_v2_models import DiagnosisEvidenceV2
from ops_agent.diagnosis_v3_contract import decision_v3_contract
from ops_agent.diagnosis_v3_models import AgentDecisionV3
from ops_agent.diagnosis_v3_validator import validate_decision_v3
from ops_agent.diagnosis_validator import DiagnosisValidationError
from ops_agent.evaluation_models import canonical_sha256
from ops_agent.scenario_agent_v3 import run_scenario_v3
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG
from tests.ops_agent.test_scenario_agent_v3 import OfflineClient


@pytest.fixture
def setup():
    catalog = load_scenario_catalog(DEFAULT_CATALOG)
    run = fixed_run_v3(catalog=catalog, fixture_id="postgres-path-degradation")
    evidence = [DiagnosisEvidenceV2.model_validate(item) for item in run["acquired_evidence"]]
    allowed = selectable_evidence_ids(initial_evidence_ids=catalog.activation["evidence_ids"], acquired_evidence=evidence)
    return catalog, run, evidence, allowed


def test_all_citation_fields_share_one_enum_and_base_contract_is_unchanged(setup):
    _, _, _, allowed = setup
    before = decision_v3_contract()
    schema = citation_schema_v3(allowed)
    assert schema["$defs"]["ProvidedEvidenceIdV3"]["enum"] == allowed
    fields = [schema["$defs"]["ObservedSignalV3"]["properties"]["evidence_ids"],
        schema["$defs"]["CandidateCauseV3"]["properties"]["supporting_evidence_ids"],
        schema["$defs"]["CandidateCauseV3"]["properties"]["conflicting_evidence_ids"],
        schema["properties"]["context_evidence_ids"]]
    assert all(field["items"] == {"$ref": "#/$defs/ProvidedEvidenceIdV3"} for field in fields)
    assert decision_v3_contract() == before
    assert "ProvidedEvidenceIdV3" not in before["schema"]["$defs"]


@pytest.mark.parametrize("field", ["observation", "supporting", "conflicting", "context"])
def test_unknown_id_rejected_in_every_citation_role(setup, field):
    _, run, _, allowed = setup
    raw = deepcopy(run["decision"])
    if field == "observation":
        raw["observations"][0]["evidence_ids"] = ["typo-not-provided"]
    elif field == "context":
        raw["context_evidence_ids"].append("typo-not-provided")
    else:
        raw["hypotheses"][0][f"{field}_evidence_ids"] = ["typo-not-provided"]
    with pytest.raises(DiagnosisValidationError) as error:
        validate_citation_selection(AgentDecisionV3.model_validate(raw), allowed)
    assert error.value.code == "V3_CITATION_SELECTION_OUTSIDE_ENUM"


def test_correct_id_can_still_be_wrong_semantic_support(setup):
    catalog, run, evidence, allowed = setup
    raw = deepcopy(run["decision"])
    stage = next(item.evidence_id for item in evidence if item.tool_id == "get_worker_stage_latency")
    raw["context_evidence_ids"].remove(stage)
    raw["hypotheses"][0]["supporting_evidence_ids"] = [stage]
    decision = AgentDecisionV3.model_validate(raw)
    validate_citation_selection(decision, allowed)
    with pytest.raises(DiagnosisValidationError) as error:
        validate_decision_v3(decision, initial_evidence_ids=catalog.activation["evidence_ids"], acquired_evidence=evidence)
    assert error.value.code == "V3_CONTEXT_IS_NOT_CAUSAL_SUPPORT"


def test_repair_choices_intersect_original_citations_and_exclude_forged_id(setup):
    catalog, _, evidence, _ = setup
    original = {"context_evidence_ids": [catalog.activation["evidence_ids"][0], "forged-id"]}
    allowed = selectable_evidence_ids(initial_evidence_ids=catalog.activation["evidence_ids"],
                                     acquired_evidence=evidence, original=original)
    assert allowed == [catalog.activation["evidence_ids"][0]]
    assert not any(item.evidence_id in allowed for item in evidence)


def test_empty_selection_universe_uses_empty_arrays_without_invalid_enum():
    schema = citation_schema_v3([])
    assert schema["properties"]["observations"]["maxItems"] == 0
    assert schema["properties"]["context_evidence_ids"]["maxItems"] == 0
    assert schema["$defs"]["CandidateCauseV3"]["properties"]["supporting_evidence_ids"]["maxItems"] == 0
    assert schema["$defs"]["CandidateCauseV3"]["properties"]["conflicting_evidence_ids"]["maxItems"] == 0
    assert '"enum": []' not in json.dumps(schema)


@pytest.mark.parametrize("ids", [[""], ["x" * 257], ["a", 42], [str(i) for i in range(257)]])
def test_invalid_or_excessive_enum_rejected_without_truncation(ids):
    with pytest.raises(ValueError):
        citation_schema_v3(ids)


def test_schema_choices_grow_only_after_actual_model_tool_calls():
    catalog = load_scenario_catalog(DEFAULT_CATALOG)
    client = OfflineClient()
    run = run_scenario_v3(catalog=catalog, fixture_id="worker-replica-shortfall", client=client, model="offline")
    assert run["contract_pass"]
    known = set(catalog.activation["evidence_ids"])
    for index, request in enumerate(client.requests):
        schema = request["text"]["format"]["schema"]
        assert set(schema["$defs"]["ProvidedEvidenceIdV3"]["enum"]) == known
        assert run["citation_selection_audit"][index]["schema_sha256"] == canonical_sha256(schema)
        if index < len(run["acquired_evidence"]):
            known.add(run["acquired_evidence"][index]["evidence_id"])


def test_mock_provider_ignoring_enum_is_rejected_locally():
    catalog = load_scenario_catalog(DEFAULT_CATALOG)
    def typo(raw):
        raw["observations"][0]["evidence_ids"][0] += "typo"
    run = run_scenario_v3(catalog=catalog, fixture_id="worker-db-path-pressure",
                          client=OfflineClient(mutate=typo), model="offline")
    assert not run["contract_pass"]
    assert run["decisions"][0]["failure_code"] == "V3_CITATION_SELECTION_OUTSIDE_ENUM"


def test_malformed_original_does_not_expand_repair_universe():
    original = {"observations": [None, {"evidence_ids": "not-an-array"}], "hypotheses": None,
                "context_evidence_ids": ["known", None]}
    assert decision_citations(original) == {"known"}
    assert selectable_evidence_ids(initial_evidence_ids=["known", "unused"], acquired_evidence=[], original=original) == ["known"]
