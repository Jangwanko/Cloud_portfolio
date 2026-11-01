import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ops_agent.diagnosis_agent import ModelResponse
from ops_agent.runbook_assistant import catalog, prepare, read_json, run, validate_advice

ROOT = Path(__file__).resolve().parents[2]


def fixture(index=0, step=0):
    case = read_json(ROOT / "ops_agent/fixtures/runbook_assistance_v1.json")["cases"][index]
    return case["steps"][step], case["question"]


def answer(context):
    refs = [context["evidence"][0]["evidence_id"]]
    claim = {"text": "Inspect evidence before concluding", "evidence_ids": refs}
    return {"current_summary": claim, "impact": {**claim, "severity": "UNKNOWN"},
        "hypotheses": [], "unknowns": ["cause unconfirmed"], "change": claim,
        "next_checks": [{**claim, "runbook_id": None}],
        "runbook_assessments": [{"runbook_id": b["id"], "applicability": "NEEDS_MORE_EVIDENCE",
            "rationale": "Needs direct evidence", "conditions": [{"id": c["id"], "status": "UNKNOWN",
                "evidence_ids": []} for c in b["conditions"]]} for b in context["runbooks"]]}


def fake_client():
    def create(payload):
        context = json.loads(payload["input"][1]["content"])
        return ModelResponse("fake", [], json.dumps(answer(context)), 1, 1, 2)
    return SimpleNamespace(create=create)


def test_catalog_excerpts_retain_source_and_version():
    entries = catalog()
    assert len(entries) == 3
    assert all(e["source_heading"] in e["excerpt"] and len(e["source_sha256"]) == 64 for e in entries)
    assert "partition=1" in next(e["excerpt"] for e in entries if e["id"] == "RB-PG-HEALTH")


def test_partition_and_endpoint_identity_survive_model_projection():
    document, question = fixture()
    document["evidence"][0]["labels"] = {"partition": "7", "topic": "message-ingress", "endpoint": "/health/ready"}
    context = prepare(document, question, "recorded")
    assert context["evidence"][0]["labels"] == document["evidence"][0]["labels"]


def test_unmatched_case_does_not_invent_runbook_and_still_supports_next_check():
    document, question = fixture(2)
    context = prepare(document, question, "synthetic")
    assert context["runbooks"] == []
    assert validate_advice(json.dumps(answer(context)), context)["next_checks"]


def test_similarity_cannot_override_unknown_conditions():
    document, question = fixture(1)
    context = prepare(document, question, "synthetic")
    decision = answer(context)
    decision["runbook_assessments"][0]["applicability"] = "APPLICABLE"
    with pytest.raises(ValueError, match="contradicts"):
        validate_advice(json.dumps(decision), context)


@pytest.mark.parametrize("mode", ["missing", "stale", "partial"])
def test_bad_evidence_cannot_establish_condition(mode):
    document, question = fixture()
    item = document["evidence"][0]
    if mode == "missing":
        item["status"] = "UNAVAILABLE"
    elif mode == "stale":
        item["source_timestamp"] = "2026-11-01T01:59:00+00:00"
        item["freshness"].update(status="STALE", age_seconds=60)
    else:
        item["coverage"]["complete"] = False
    context = prepare(document, question, "synthetic")
    decision = answer(context)
    assessment = decision["runbook_assessments"][0]
    assessment["applicability"] = "APPLICABLE"
    assessment["conditions"][0].update(status="MET", evidence_ids=[context["evidence"][0]["evidence_id"]])
    with pytest.raises(ValueError, match="missing or stale"):
        validate_advice(json.dumps(decision), context)


def test_followup_rejects_other_incident_old_timestamp_and_tampering(tmp_path):
    document, question = fixture(1)
    prior = run(document, question, "synthetic", tmp_path / "first", client=fake_client(), model="fake")
    later, question = fixture(1, 1)
    context = prepare(later, question, "synthetic", prior)
    assert context["previous"]["record_sha256"] == prior["record_sha256"]
    assert {e["evidence_id"] for e in context["evidence"]}.isdisjoint(
        {e["evidence_id"] for e in prior["input"]["evidence"]})
    for broken in (document, dict(later, incident_id="other")):
        with pytest.raises(ValueError): prepare(broken, question, "synthetic", prior)
    prior["advice"]["impact"]["severity"] = "CRITICAL"
    with pytest.raises(ValueError, match="tampered"):
        prepare(later, question, "synthetic", prior)


def test_previous_evidence_cannot_be_cited_as_current(tmp_path):
    document, question = fixture(1)
    prior = run(document, question, "synthetic", tmp_path / "first", client=fake_client(), model="fake")
    later, question = fixture(1, 1)
    context = prepare(later, question, "synthetic", prior)
    decision = answer(context)
    decision["change"]["evidence_ids"] = [prior["input"]["evidence"][0]["evidence_id"]]
    with pytest.raises(ValueError, match="unavailable evidence"):
        validate_advice(json.dumps(decision), context)


def test_unseen_runbook_and_duplicate_condition_rejected():
    document, question = fixture()
    context = prepare(document, question, "synthetic")
    decision = answer(context)
    decision["next_checks"][0]["runbook_id"] = "INVENTED"
    with pytest.raises(ValueError, match="unseen runbook"):
        validate_advice(json.dumps(decision), context)
    decision = answer(context)
    decision["runbook_assessments"][0]["conditions"] *= 2
    with pytest.raises(ValueError, match="coverage"):
        validate_advice(json.dumps(decision), context)


@pytest.mark.parametrize("output", ["", '{"current_summary":{"text":"truncated'])
def test_invalid_response_retained_as_failure_not_completed_record(tmp_path, output):
    document, question = fixture()
    client = SimpleNamespace(create=lambda _: ModelResponse("id", [], output, 0, 0, 0))
    directory = tmp_path / "failed"
    with pytest.raises(ValueError): run(document, question, "synthetic", directory, client=client, model="fake")
    assert (directory / "response.json").exists()
    assert (directory / "failure.json").exists()
    assert not (directory / "record.json").exists()


def test_offline_prepares_sources_without_a_claimed_diagnosis(tmp_path):
    document, question = fixture()
    result = run(document, question, "synthetic", tmp_path / "prepared")
    assert result["status"] == "PREPARED"
    assert not (tmp_path / "prepared/record.json").exists()


def test_unknown_telemetry_cannot_support_normal_impact():
    document, question = fixture(1)
    context = prepare(document, question, "synthetic")
    decision = answer(context)
    decision["impact"].update(severity="NORMAL", evidence_ids=[context["evidence"][1]["evidence_id"]])
    with pytest.raises(ValueError, match="missing or stale"):
        validate_advice(json.dumps(decision), context)
