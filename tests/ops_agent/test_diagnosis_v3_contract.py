"""Adversarial role/capability tests for the isolated offline decision candidate."""

from copy import deepcopy
import json
import socket

import pytest
from pydantic import ValidationError

from ops_agent.diagnosis_models import DiagnosisEvidenceStatus
from ops_agent.diagnosis_scenarios import load_scenario_catalog
from ops_agent.diagnosis_v2_models import AgentDecisionV2, DiagnosisEvidenceV2
from ops_agent.diagnosis_v3_contract import decision_v3_contract
from ops_agent.diagnosis_v3_models import AgentDecisionV3
from ops_agent.diagnosis_v3_validator import validate_decision_v3
from ops_agent.diagnosis_validator import DiagnosisValidationError
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG
from scripts.export_diagnosis_v3_candidate import build_examples, export_candidate


@pytest.fixture
def examples():
    return build_examples(load_scenario_catalog(DEFAULT_CATALOG))


def validate(case, payload=None, evidence=None):
    return validate_decision_v3(AgentDecisionV3.model_validate(payload or case["decision"]),
        initial_evidence_ids=load_scenario_catalog(DEFAULT_CATALOG).activation["evidence_ids"],
        acquired_evidence=evidence or [DiagnosisEvidenceV2.model_validate(item) for item in case["acquired_evidence"]])


def evidence_id(case, tool):
    return next(item["evidence_id"] for item in case["acquired_evidence"] if item["tool_id"] == tool)


@pytest.mark.parametrize("index", range(4))
def test_authored_examples_validate_without_causal_truth_claim(examples, index):
    case = examples["examples"][index]
    report = validate(case)
    assert report["contract_valid"] and report["evidence_roles_valid"]
    assert report["causal_truth_verified"] is False
    assert report["investigation_completeness"] == "NOT_EVALUATED"


@pytest.mark.parametrize("index", [0, 3])
def test_stage_signal_survives_abstention(examples, index):
    result = examples["examples"][index]["decision"]
    assert result["conclusion"] == "ABSTAIN"
    assert not result["hypotheses"]
    assert result["investigation_gaps"]
    assert any(item["signal"] == "WORKER_STAGE_ELEVATED" for item in result["observations"])


@pytest.mark.parametrize("tool", ["get_worker_stage_latency", "get_keda_status"])
def test_context_only_tools_cannot_substitute_for_replica_support(examples, tool):
    case = examples["examples"][1]
    value = evidence_id(case, tool)
    payload = deepcopy(case["decision"])
    payload["context_evidence_ids"].remove(value)
    payload["hypotheses"][0]["supporting_evidence_ids"] = [value]
    with pytest.raises(DiagnosisValidationError) as error:
        validate(case, payload)
    assert error.value.code == "V3_CONTEXT_IS_NOT_CAUSAL_SUPPORT"


def test_activation_lag_cannot_become_extra_direct_causal_support(examples):
    case = examples["examples"][1]
    payload = deepcopy(case["decision"])
    value = payload["context_evidence_ids"].pop(0)
    payload["hypotheses"][0]["supporting_evidence_ids"].append(value)
    with pytest.raises(DiagnosisValidationError, match="substitute"):
        validate(case, payload)


def test_unknown_context_id_is_rejected(examples):
    case = examples["examples"][0]
    payload = deepcopy(case["decision"])
    payload["context_evidence_ids"].append("fabricated")
    with pytest.raises(DiagnosisValidationError) as error:
        validate(case, payload)
    assert error.value.code == "V3_UNKNOWN_CITATION"


def test_observation_requires_its_actual_measurement(examples):
    case = examples["examples"][0]
    payload = deepcopy(case["decision"])
    payload["observations"][0]["evidence_ids"] = [evidence_id(case, "get_keda_status")]
    with pytest.raises(DiagnosisValidationError) as error:
        validate(case, payload)
    assert error.value.code == "V3_OBSERVATION_NOT_MEASURED"


@pytest.mark.parametrize("quality", ["stale", "partial"])
def test_unusable_data_cannot_anchor_observation_or_cause(examples, quality):
    case = examples["examples"][1]
    evidence = [DiagnosisEvidenceV2.model_validate(item) for item in case["acquired_evidence"]]
    index = next(i for i, item in enumerate(evidence) if item.tool_id == "get_worker_replica_status")
    item = evidence[index]
    evidence[index] = item.model_copy(update={
        "status": DiagnosisEvidenceStatus.PARTIAL,
    }) if quality == "partial" else item.model_copy(update={
        "freshness": item.freshness.model_copy(update={"status": type(item.freshness.status)("STALE")}),
    })
    with pytest.raises(DiagnosisValidationError):
        validate(case, evidence=evidence)


def test_worker_path_symptom_is_not_a_causal_enum(examples):
    payload = deepcopy(examples["examples"][1]["decision"])
    payload["hypotheses"][0]["hypothesis"] = "WORKER_PATH_PRESSURE_SUSPECTED"
    with pytest.raises(ValidationError):
        AgentDecisionV3.model_validate(payload)


def test_context_and_signed_roles_cannot_overlap(examples):
    payload = deepcopy(examples["examples"][1]["decision"])
    payload["context_evidence_ids"].append(payload["hypotheses"][0]["supporting_evidence_ids"][0])
    with pytest.raises(ValidationError, match="separate"):
        AgentDecisionV3.model_validate(payload)


def test_unavailable_rebalance_cannot_be_promoted(examples):
    case = examples["examples"][0]
    payload = deepcopy(case["decision"])
    payload["hypotheses"] = [{"hypothesis": "CONSUMER_REBALANCE_SUSPECTED", "support_status": "INSUFFICIENT",
        "supporting_evidence_ids": [], "conflicting_evidence_ids": [],
        "evidence_gaps": ["CONSUMER_REBALANCE_TELEMETRY_UNAVAILABLE"]}]
    assert validate(case, payload)["contract_valid"]
    payload["hypotheses"][0]["evidence_gaps"] = ["wrong_code"]
    with pytest.raises(DiagnosisValidationError) as error:
        validate(case, payload)
    assert error.value.code == "V3_REBALANCE_GAP_REQUIRED"
    payload["hypotheses"][0]["support_status"] = "SUPPORTED"
    payload["hypotheses"][0]["supporting_evidence_ids"] = [evidence_id(case, "get_worker_stage_latency")]
    payload["context_evidence_ids"].remove(evidence_id(case, "get_worker_stage_latency"))
    with pytest.raises(DiagnosisValidationError) as error:
        validate(case, payload)
    assert error.value.code == "V3_CAUSE_TELEMETRY_UNAVAILABLE"


@pytest.mark.parametrize("change", ["missing_gap", "wrong_conclusion", "action"])
def test_abstention_gap_conclusion_and_authority_boundaries(examples, change):
    case = examples["examples"][3]
    payload = deepcopy(case["decision"])
    if change == "missing_gap":
        payload["investigation_gaps"] = []
    elif change == "wrong_conclusion":
        payload["conclusion"] = "SUPPORTED_CANDIDATES"
    else:
        payload["investigation_gaps"] = ["KUBECTL_DELETE_POD"]
    with pytest.raises(DiagnosisValidationError):
        validate(case, payload)


def test_v3_does_not_parse_as_a_v2_result(examples):
    with pytest.raises(ValidationError):
        AgentDecisionV2.model_validate(examples["examples"][0]["decision"])


def test_schema_requires_all_fields_and_forbids_extra_keys():
    contract = decision_v3_contract()
    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(contract["schema"])
    assert contract["identity"]["version"] == "ops.diagnosis.prompt.v3-candidate"
    assert "fixture_id" not in json.dumps(contract)
    assert "expected_primary_hypothesis" not in json.dumps(contract)


def test_export_is_offline_and_never_overwrites_existing_candidate(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("candidate export cannot use network")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    output = tmp_path / "candidate"
    assert export_candidate(output) == 0
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["api_requests"] == 0
    assert manifest["authored_example_count"] == 4
    assert manifest["status"] == "completed"
    with pytest.raises(FileExistsError):
        export_candidate(output)
