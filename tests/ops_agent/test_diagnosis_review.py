"""Offline review preserves v1 decisions and isolates unknown relations."""

import hashlib
import json
import socket

import pytest

from ops_agent.diagnosis_quality import QualityAttempt, QualityRubric
from ops_agent.diagnosis_models import DiagnosisStopReason
from ops_agent.diagnosis_review import review_attempt, summarize_reviews
from ops_agent.diagnosis_scenarios import load_scenario_catalog
from ops_agent.diagnosis_v2_models import DiagnosisRunV2, HypothesisResultV2
from ops_agent.evaluation_models import canonical_sha256
from ops_agent.scenario_agent import RecordedBranchModelClient
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG, DEFAULT_REPLAY, DEFAULT_RUBRIC
from scripts.review_diagnosis_pilot import review_pilot
from scripts.run_diagnosis_pilot import run_pilot


def load_case(index):
    item = json.loads(DEFAULT_REPLAY.read_bytes())["scenarios"][index]
    return item["fixture_id"], DiagnosisRunV2.model_validate(item["run"])


def reviewed(fixture_id, run):
    return review_attempt(QualityAttempt(attempt_id="test", fixture_id=fixture_id,
        execution_kind="scripted", run=run.model_dump(mode="json")),
        catalog=load_scenario_catalog(DEFAULT_CATALOG),
        rubric=QualityRubric.model_validate_json(DEFAULT_RUBRIC.read_bytes()))


def changed(run, **updates):
    result = run.model_copy(update=updates)
    return result.model_copy(update={"diagnosis_id": canonical_sha256(result._identity_payload())})


def test_unscored_activation_citation_requires_review_without_changing_legacy_gate():
    fixture, run = load_case(0)
    hypothesis = run.hypotheses[0].model_copy(update={"supporting_evidence_ids": [
        *run.hypotheses[0].supporting_evidence_ids, run.initial_evidence_ids[0],
    ]})
    run = changed(run, hypotheses=[hypothesis])
    original = run.model_dump(mode="json")
    result = reviewed(fixture, run)
    assert result["legacy_score"]["quality_pass"] is False
    assert result["dimensions"]["fixture_label_alignment"] == "PASS"
    assert result["dimensions"]["citation_relations"] == "REVIEW_REQUIRED"
    assert result["citation_reviews"][-1]["review_reason"] == "ACTIVATION_CONTEXT_WITHOUT_SIGNED_CAUSAL_RULE"
    assert run.model_dump(mode="json") == original


def test_keda_omission_remains_a_completeness_failure_separate_from_label():
    fixture, run = load_case(1)
    hypothesis = run.hypotheses[0].model_copy(update={"supporting_evidence_ids": [
        run.additional_evidence[1].evidence_id,
    ]})
    run = changed(run, hypotheses=[hypothesis], steps=run.steps[:2],
                  additional_evidence=run.additional_evidence[:2], steps_used=2)
    result = reviewed(fixture, run)
    assert result["legacy_score"]["quality_pass"] is False
    assert result["dimensions"]["fixture_label_alignment"] == "PASS"
    assert result["dimensions"]["investigation_completeness"] == "FAIL"
    assert "KEDA_INVESTIGATION_INCOMPLETE_NOT_LABEL_ERROR" in result["review_flags"]


@pytest.mark.parametrize("side", ["supporting", "conflicting"])
def test_keda_state_relation_needs_context_on_either_citation_side(side):
    fixture, run = load_case(1)
    result = reviewed(fixture, run)
    if side == "conflicting":
        hypothesis = run.hypotheses[0].model_copy(update={
            "supporting_evidence_ids": [run.additional_evidence[1].evidence_id],
            "conflicting_evidence_ids": [run.additional_evidence[2].evidence_id],
        })
        result = reviewed(fixture, changed(run, hypotheses=[hypothesis]))
    assert result["dimensions"]["citation_relations"] == "REVIEW_REQUIRED"
    assert result["citation_reviews"][-1]["review_reason"] == "KEDA_STATE_REQUIRES_REPLICA_AND_LAG_CONTEXT"


def test_known_signed_relation_mismatch_is_not_hidden_by_review_status():
    fixture, run = load_case(0)
    hypothesis = HypothesisResultV2.model_validate({
        "hypothesis": "POSTGRES_PATH_DEGRADED_SUSPECTED", "support_status": "SUPPORTED",
        "reason_codes": ["TEST"], "supporting_evidence_ids": [run.additional_evidence[1].evidence_id],
    })
    result = reviewed(fixture, changed(run, hypotheses=[hypothesis]))
    assert result["dimensions"]["citation_relations"] == "FAIL"
    assert result["citation_reviews"][0]["review_status"] == "MISMATCH"


def test_symptom_with_missing_discriminator_preserves_abstention_failure():
    fixture, run = load_case(3)
    hypothesis = HypothesisResultV2.model_validate({
        "hypothesis": "WORKER_PATH_PRESSURE_SUSPECTED", "support_status": "SUPPORTED",
        "reason_codes": ["TEST"], "supporting_evidence_ids": [run.additional_evidence[0].evidence_id],
    })
    result = reviewed(fixture, changed(run, hypotheses=[hypothesis],
        stop_reason=DiagnosisStopReason.SUFFICIENT_EVIDENCE))
    assert result["dimensions"]["abstention"] == "FAIL"
    assert "SUPPORTED_PATH_WITH_MISSING_POSTGRES_DISCRIMINATOR" in result["review_flags"]
    assert not result["legacy_score"]["quality_pass"]


def test_symptom_and_explanation_flag_does_not_rewrite_supported_set():
    fixture, run = load_case(2)
    extra = HypothesisResultV2.model_validate({
        "hypothesis": "WORKER_PATH_PRESSURE_SUSPECTED", "support_status": "SUPPORTED",
        "reason_codes": ["TEST"], "supporting_evidence_ids": [run.additional_evidence[0].evidence_id],
    })
    result = reviewed(fixture, changed(run, hypotheses=[*run.hypotheses, extra]))
    assert result["dimensions"]["fixture_label_alignment"] == "FAIL"
    assert "SYMPTOM_AND_EXPLANATION_BOTH_SUPPORTED" in result["review_flags"]
    assert len(result["legacy_score"]["supported_hypotheses"]) == 2


def test_output_failure_is_not_mislabeled_as_diagnostic_failure():
    result = review_attempt(QualityAttempt(attempt_id="failure", fixture_id="telemetry-unavailable",
        execution_kind="scripted", failure_code="SCHEMA_FAILURE"),
        catalog=load_scenario_catalog(DEFAULT_CATALOG),
        rubric=QualityRubric.model_validate_json(DEFAULT_RUBRIC.read_bytes()))
    assert set(result["dimensions"].values()) == {"NOT_EVALUATED"}
    assert result["legacy_score"]["quality_pass"] is False
    assert summarize_reviews([result])["new_integrated_pass_rate"] is None


def test_cli_network_free_source_immutable_and_no_overwrite(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("offline review must not access network")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    source = tmp_path / "pilot"
    run_pilot(output_dir=source, model="injected-scripted-client", client_factory=RecordedBranchModelClient)
    before = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in source.rglob("*") if path.is_file()}
    output = tmp_path / "review"
    assert review_pilot(input_dir=source, output_dir=output) == 0
    data = json.loads((output / "review.json").read_bytes())
    assert data["summary"]["legacy_quality_pass_count"] == 8
    assert data["summary"]["attempt_count"] == 8
    assert data["summary"]["new_integrated_pass_rate"] is None
    assert before == {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in source.rglob("*") if path.is_file()}
    with pytest.raises(FileExistsError):
        review_pilot(input_dir=source, output_dir=output)
    summary_path = source / "summary.json"
    original = json.loads(summary_path.read_bytes())
    original["live"]["quality_success_rate"] = 0
    summary_path.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(ValueError, match="differ"):
        review_pilot(input_dir=source, output_dir=tmp_path / "tampered")
