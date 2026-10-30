import hashlib
import json

import pytest

from ops_agent.models import EvidenceBundle
from ops_agent.observation import summarize_observation
from scripts.observe_worker_backlog import capture_observation
from tests.ops_agent.synthetic_evidence import build_positive_sequence, set_measurement


def test_present_is_only_eligibility_without_model_or_runbook():
    summary, conditions = summarize_observation(build_positive_sequence())
    assert summary["activation_state"] == "PRESENT"
    assert summary["diagnosis_gate"] == "ELIGIBLE_FOR_DIAGNOSIS"
    assert summary["sequence_evaluation_id"] == conditions["evaluation_id"]
    assert summary["model"] == {"status": "NOT_RUN", "api_request_count": 0}
    assert summary["fixed_diagnosis_runbook"]["status"] == "NOT_RUN"
    assert summary["diagnosis_comparison"] == "NOT_EVALUATED"
    assert summary["runtime_v35_adapter"] == "PENDING"


def test_zero_lag_skips_diagnosis_and_preserves_frozen_inputs():
    sequence = build_positive_sequence()
    for bundle in sequence:
        set_measurement(bundle, 0, 0)
    before = json.dumps(sequence, sort_keys=True)
    summary, _ = summarize_observation(sequence)
    assert summary["activation_state"] == "ABSENT"
    assert summary["diagnosis_gate"] == "SKIPPED_NO_BACKLOG_PRESSURE"
    assert [item["total_lag_records"] for item in summary["captures"]] == [0, 0, 0]
    assert json.dumps(sequence, sort_keys=True) == before


def test_required_missing_is_unknown_never_healthy():
    sequence = build_positive_sequence()
    sequence[-1]["evidence"] = [item for item in sequence[-1]["evidence"]
        if item["metric"]["name"] != "kafka_consumergroup_lag"]
    summary, _ = summarize_observation(sequence)
    assert summary["activation_state"] == "UNKNOWN"
    assert summary["diagnosis_gate"] == "SKIPPED_INSUFFICIENT_ACTIVATION_EVIDENCE"
    assert summary["model"]["api_request_count"] == 0


def test_capture_writes_replayable_bundles_inventory_and_refuses_overwrite(tmp_path):
    sequence = iter(build_positive_sequence())
    calls, waits = [], []
    def collect(**kwargs):
        calls.append(kwargs)
        return EvidenceBundle.model_validate(next(sequence))
    destination = tmp_path / "observation"
    summary = capture_observation(output_dir=destination, collector=collect,
        clock=lambda: 0, wait=waits.append)
    assert len(calls) == 3
    assert waits == [15, 15]
    assert all(call["cluster_context"] == "kind-messaging-ha" for call in calls)
    assert len({call["incident_id"] for call in calls}) == 1
    paths = sorted((destination / "bundles").glob("*.json"))
    replay, _ = summarize_observation([json.loads(path.read_bytes()) for path in paths])
    assert replay == summary
    inventory = json.loads((destination / "inventory.json").read_bytes())
    for relative, expected in inventory["files"].items():
        assert hashlib.sha256((destination / relative).read_bytes()).hexdigest() == expected
    with pytest.raises(FileExistsError):
        capture_observation(output_dir=destination, collector=collect)
    assert len(calls) == 3


def test_failure_keeps_partial_capture_and_only_error_type(tmp_path):
    sequence = build_positive_sequence()
    count = 0
    def collect(**kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("sensitive transport text")
        return EvidenceBundle.model_validate(sequence[0])
    destination = tmp_path / "failed"
    with pytest.raises(RuntimeError):
        capture_observation(output_dir=destination, collector=collect,
            clock=lambda: 0, wait=lambda _: None)
    failure = json.loads((destination / "failure.json").read_bytes())
    assert failure == {"status": "FAILED", "completed_captures": 1, "error_type": "RuntimeError"}
    assert (destination / "bundles/sample-001.json").is_file()
    assert not (destination / "summary.json").exists()
    assert not (destination / "inventory.json").exists()


@pytest.mark.parametrize("options", [{"interval_seconds": 0}, {"interval_seconds": 31},
    {"context": "other-cluster"}])
def test_invalid_sampling_scope_rejected_before_collection(tmp_path, options):
    with pytest.raises(ValueError):
        capture_observation(output_dir=tmp_path / "invalid", **options)
    assert not (tmp_path / "invalid").exists()


def test_exact_three_captures_required():
    with pytest.raises(ValueError):
        summarize_observation(build_positive_sequence()[:2])
