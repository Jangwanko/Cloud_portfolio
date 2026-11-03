"""Adversarial offline scorer checks over saved, scripted Scenario Lab runs."""

from copy import deepcopy
import json
from pathlib import Path
import socket

import pytest

from ops_agent.diagnosis_quality import (
    QualityAttempt, QualityRubric, evaluation_identity, score_attempt, summarize_scores,
)
from ops_agent.diagnosis_scenarios import load_scenario_catalog
from ops_agent.diagnosis_v2_models import DiagnosisRunV2
from ops_agent.evaluation_models import canonical_sha256
from scripts.evaluate_diagnosis_quality import (
    DEFAULT_CATALOG, DEFAULT_REPLAY, DEFAULT_RUBRIC, evaluate, load_attempts,
)


@pytest.fixture
def inputs():
    catalog = load_scenario_catalog(DEFAULT_CATALOG)
    rubric = QualityRubric.model_validate_json(DEFAULT_RUBRIC.read_bytes())
    replay = json.loads(DEFAULT_REPLAY.read_bytes())
    return catalog, rubric, replay


def rehash(run):
    # Rebuild a valid identity after deliberately changing a diagnosis claim.
    # Thus negative tests exercise quality scoring beyond the integrity gate.
    reference = DiagnosisRunV2.model_validate(
        json.loads(DEFAULT_REPLAY.read_bytes())["scenarios"][0]["run"]
    )
    keys = reference._identity_payload().keys()
    payload = {key: deepcopy(run[key]) for key in keys}
    payload["steps"] = [
        {key: step[key] for key in ("step", "tool_id", "reason_code", "returned_evidence_ids")}
        for step in run["steps"]
    ]
    run["diagnosis_id"] = canonical_sha256(payload)
    return run


def attempt(item, **kwargs):
    return QualityAttempt(attempt_id="test", fixture_id=item["fixture_id"],
                          execution_kind="scripted", run=item["run"], **kwargs)


def score(item, inputs):
    catalog, rubric, _ = inputs
    return score_attempt(attempt(item), catalog=catalog, rubric=rubric)


@pytest.mark.parametrize("index", range(4))
def test_existing_scripted_cases_pass_without_latency_inference(inputs, index):
    result = score(inputs[2]["scenarios"][index], inputs)
    assert result["contract_pass"] and result["quality_pass"]
    assert result["elapsed_seconds"] is None
    assert result["repair_used"] == 0


def test_existing_but_semantically_wrong_citation_fails(inputs):
    item = deepcopy(inputs[2]["scenarios"][0])
    hypothesis = item["run"]["hypotheses"][0]
    hypothesis["hypothesis"] = "POSTGRES_PATH_DEGRADED_SUSPECTED"
    postgres_id = next(e["evidence_id"] for e in item["run"]["additional_evidence"]
                       if e["tool_id"] == "get_postgres_health")
    hypothesis["supporting_evidence_ids"] = [postgres_id]
    rehash(item["run"])
    result = score(item, inputs)
    assert result["contract_pass"] is True
    assert result["checks"]["citation_semantics_pass"] is False
    assert result["citation_edges"][0]["rubric_relation"] == "conflicting"
    assert result["quality_pass"] is False


def test_contextual_support_without_anchor_fails(inputs):
    item = deepcopy(inputs[2]["scenarios"][2])
    hypothesis = item["run"]["hypotheses"][0]
    hypothesis["supporting_evidence_ids"] = [item["run"]["additional_evidence"][0]["evidence_id"]]
    rehash(item["run"])
    result = score(item, inputs)
    assert result["contract_pass"] and result["checks"]["citation_semantics_pass"]
    assert result["missing_support_anchors"] == ["get_postgres_health"]
    assert result["quality_pass"] is False


def test_fabricated_id_cannot_hide_behind_recorded_valid(inputs):
    item = deepcopy(inputs[2]["scenarios"][0])
    item["run"]["hypotheses"][0]["supporting_evidence_ids"] = ["fabricated"]
    rehash(item["run"])
    result = score(item, inputs)
    assert result["failure_code"] == "INVALID_COMPLETED_ARTIFACT"
    assert not result["contract_pass"]


def test_fixture_payload_cannot_be_forged_even_with_recomputed_run_hash(inputs):
    item = deepcopy(inputs[2]["scenarios"][0])
    item["run"]["additional_evidence"][0]["summary"]["worker_stage_mean_ms"] = 999
    rehash(item["run"])
    assert not score(item, inputs)["contract_pass"]


def test_fixture_assignment_mismatch_rejected(inputs):
    item = deepcopy(inputs[2]["scenarios"][0])
    item["fixture_id"] = "postgres-path-degradation"
    assert not score(item, inputs)["contract_pass"]


def test_wrong_activation_rejected(inputs):
    item = deepcopy(inputs[2]["scenarios"][0])
    item["run"]["context"]["namespace"] = "different"
    rehash(item["run"])
    assert not score(item, inputs)["contract_pass"]


def test_missing_required_acquisition_fails(inputs):
    item = deepcopy(inputs[2]["scenarios"][0])
    item["run"]["steps"].pop()
    item["run"]["additional_evidence"].pop()
    item["run"]["steps_used"] -= 1
    rehash(item["run"])
    result = score(item, inputs)
    assert result["contract_pass"]
    assert result["missing_required_tools"] == ["get_worker_replica_status"]
    assert not result["quality_pass"]


def test_alternate_order_can_pass_quality_while_strict_branch_fails(inputs):
    item = deepcopy(inputs[2]["scenarios"][0])
    run = item["run"]
    run["steps"][1], run["steps"][2] = run["steps"][2], run["steps"][1]
    run["additional_evidence"][1], run["additional_evidence"][2] = (
        run["additional_evidence"][2], run["additional_evidence"][1]
    )
    for index, step in enumerate(run["steps"], 1):
        step["step"] = index
    for branch in run["branch_evaluations"]:
        branch["result"] = "FAIL"
    rehash(run)
    result = score(item, inputs)
    assert result["quality_pass"] is True
    assert result["strict_branch_pass"] is False


def test_unavailable_evidence_cannot_support_a_cause(inputs):
    item = deepcopy(inputs[2]["scenarios"][3])
    run = item["run"]
    run["hypotheses"] = [{
        "hypothesis": "POSTGRES_PATH_DEGRADED_SUSPECTED", "support_status": "SUPPORTED",
        "reason_codes": ["TEST_CLAIM"],
        "supporting_evidence_ids": [run["additional_evidence"][1]["evidence_id"]],
        "conflicting_evidence_ids": [], "evidence_gaps": [],
    }]
    run["stop_reason"] = "sufficient_evidence"
    rehash(run)
    result = score(item, inputs)
    assert result["contract_pass"]
    assert not result["checks"]["abstention_correct"]
    assert result["citation_edges"][0]["rubric_relation"] == "unusable"
    assert not result["quality_pass"]


def test_repaired_success_and_failure_remain_in_denominator(inputs):
    catalog, rubric, replay = inputs
    first = score(replay["scenarios"][0], inputs)
    first["attempt_id"] = "first"
    item = deepcopy(replay["scenarios"][1])
    run = item["run"]
    run["policy"]["max_output_repairs"] = 1
    run["output_repairs_used"] = 1
    run["model_turns"] += 1
    run["model_response_ids"].append("repair-response")
    run["validation_attempts"][0].update(result="INVALID", error_code="TEST_ERROR",
                                          error_message="Test initial invalid result")
    run["validation_attempts"].append({
        "attempt": 2, "phase": "repair", "result": "VALID", "response_id": "repair-response",
    })
    rehash(run)
    repaired = score(item, inputs)
    repaired["attempt_id"] = "repaired"
    failed = score_attempt(QualityAttempt(
        attempt_id="failed", fixture_id=item["fixture_id"], execution_kind="scripted",
        failure_code="MODEL_RESPONSE_INVALID", responses=[{"output_text": "malformed"}],
    ), catalog=catalog, rubric=rubric)
    summary = summarize_scores([first, repaired, failed])
    assert summary["attempt_count"] == 3
    assert summary["contract_success_rate"] == pytest.approx(2 / 3)
    assert summary["first_pass_contract_success_rate"] == pytest.approx(1 / 3)
    assert summary["repaired_contract_success_count"] == 1
    assert summary["final_contract_failure_count"] == 1
    assert summary["first_pass_unknown_count"] == 1
    assert summary["telemetry"]["input_tokens"]["known_attempt_count"] == 2


def test_scripted_and_live_scores_cannot_be_pooled(inputs):
    first = score(inputs[2]["scenarios"][0], inputs)
    other = dict(first, attempt_id="live", execution_kind="live")
    with pytest.raises(ValueError, match="separately"):
        summarize_scores([first, other])


def test_rubric_rejects_missing_scenario(inputs):
    catalog, rubric, _ = inputs
    payload = rubric.model_dump(mode="json")
    payload["cases"].pop("telemetry-unavailable")
    with pytest.raises(ValueError, match="exactly"):
        QualityRubric.model_validate(payload).verify_catalog(catalog)


def test_cli_is_network_free_and_preserves_sources(inputs, tmp_path, monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("offline scoring attempted network access")
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    output = tmp_path / "evaluation"
    args = dict(source_path=DEFAULT_REPLAY, replay=True, catalog_path=DEFAULT_CATALOG,
                rubric_path=DEFAULT_RUBRIC, output_dir=output)
    assert evaluate(**args) == 0
    assert (output / "source.json").read_bytes() == DEFAULT_REPLAY.read_bytes()
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["status"] == "completed"
    assert manifest["classification"] == "SCRIPTED_SCORER_CHECK"
    assert len(list(output.glob("attempt-*.json"))) == 4
    assert json.loads((output / "summary.json").read_bytes())["attempt_count"] == 4
    with pytest.raises(FileExistsError):
        evaluate(**args)


def test_cli_failed_attempt_is_saved_and_returns_failure(inputs, tmp_path):
    catalog, rubric, _ = inputs
    source = tmp_path / "batch.json"
    source.write_text(json.dumps({
        "schema_version": "ops.diagnosis.quality-attempts.v1",
        "evaluation_identity": evaluation_identity(catalog, rubric),
        "attempts": [{
            "attempt_id": "failed", "fixture_id": "worker-db-path-pressure",
            "execution_kind": "scripted", "failure_code": "TEST_FAILURE",
            "responses": [{"output_text": "invalid response"}],
        }],
    }), encoding="utf-8")
    output = tmp_path / "failure"
    assert evaluate(source_path=source, replay=False, catalog_path=DEFAULT_CATALOG,
                    rubric_path=DEFAULT_RUBRIC, output_dir=output) == 1
    record = json.loads((output / "attempt-001.json").read_bytes())
    assert record["attempt"]["responses"][0]["output_text"] == "invalid response"
    assert record["score"]["failure_code"] == "TEST_FAILURE"
    assert json.loads((output / "manifest.json").read_bytes())["status"] == "completed"


def test_mismatched_rubric_digest_batch_rejected(inputs):
    catalog, rubric, _ = inputs
    with pytest.raises(ValueError, match="does not match"):
        load_attempts({"schema_version": "ops.diagnosis.quality-attempts.v1",
                       "evaluation_identity": {}, "attempts": []}, replay=False,
                      identity=evaluation_identity(catalog, rubric))
