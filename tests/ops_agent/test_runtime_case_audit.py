import json

import pytest

from scripts.audit_runtime_case_candidates import adverse_signals, audit_archive, audit_candidate
from tests.ops_agent.synthetic_evidence import build_positive_sequence, set_measurement


def write_sequence(root, sequence):
    paths = []
    for index, bundle in enumerate(sequence):
        path = root / "calibration" / "run-01" / "bundles" / f"sample-{index:03d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bundle), encoding="utf-8")
        paths.append(path)
    return paths


def shortfall_sequence():
    sequence = build_positive_sequence()
    item = next(item for item in sequence[-1]["evidence"]
        if item["metric"]["name"] == "kubernetes_worker_deployment_observation")
    item["metric"]["value"]["available_replicas"] = 1
    return sequence


def test_qualifies_coincident_verified_activation_and_fresh_adverse_snapshot(tmp_path):
    paths = write_sequence(tmp_path, shortfall_sequence())
    result = audit_candidate(paths[-1], source_root=tmp_path)
    assert result["eligible"] and result["activation_state"] == "PRESENT"
    assert result["projection"]["summary"]["semantic_flag"] == "WORKER_CAPACITY_SHORTFALL"
    assert result["causal_truth_verified"] is False
    assert len(result["window_sources"]) == 3


def test_shortfall_without_pressure_is_excluded(tmp_path):
    sequence = shortfall_sequence()
    for bundle in sequence:
        set_measurement(bundle, 0, 0)
    paths = write_sequence(tmp_path, sequence)
    result = audit_candidate(paths[-1], source_root=tmp_path)
    assert not result["eligible"] and result["activation_state"] == "ABSENT"
    assert result["exclusion"] == "BACKLOG_ACTIVATION_NOT_PRESENT"


def test_insufficient_and_missing_windows_not_padded_or_reordered(tmp_path):
    paths = write_sequence(tmp_path, shortfall_sequence())
    assert audit_candidate(paths[1], source_root=tmp_path)["exclusion"] == "THREE_CAPTURE_WINDOW_UNAVAILABLE"
    paths[1].unlink()
    assert audit_candidate(paths[2], source_root=tmp_path)["exclusion"] == "THREE_CAPTURE_WINDOW_UNAVAILABLE"


def test_malformed_required_dependency_is_not_usable_positive(tmp_path):
    sequence = shortfall_sequence()
    item = next(item for item in sequence[-1]["evidence"]
        if item["metric"]["name"] == "kubernetes_worker_deployment_observation")
    item["metric"]["value"]["observed_generation"] += 1
    paths = write_sequence(tmp_path, sequence)
    result = audit_candidate(paths[-1], source_root=tmp_path)
    assert result["activation_state"] == "PRESENT"
    assert result["exclusion"] == "ADVERSE_PROJECTION_NOT_USABLE"
    assert not result["eligible"]


def test_bad_source_schema_stays_in_denominator(tmp_path):
    sequence = shortfall_sequence()
    sequence[-1]["collection"]["completed_at"] = "invalid"
    paths = write_sequence(tmp_path, sequence)
    result = audit_candidate(paths[-1], source_root=tmp_path)
    assert result["exclusion"] == "SOURCE_OR_EVALUATION_INVALID"
    assert not result["eligible"]


def test_archive_records_all_candidates_and_refuses_overwrite(tmp_path):
    source = tmp_path / "source"
    write_sequence(source, shortfall_sequence())
    raw = source / "calibration" / "raw" / "ignored.json"
    raw.parent.mkdir()
    raw.write_text(json.dumps(shortfall_sequence()[-1]), encoding="utf-8")
    output = tmp_path / "output"
    result = audit_archive(source_root=source, output_dir=output)
    assert result["bundle_file_count"] == 3
    assert result["candidate_snapshot_count"] == result["eligible_count"] == 1
    assert result["model_calls"] == result["cluster_writes"] == 0
    assert (output / "inventory.json").exists()
    with pytest.raises(FileExistsError):
        audit_archive(source_root=source, output_dir=output)


def test_absent_fields_and_boolean_counts_do_not_discover_fake_shortfall():
    bundle = {"evidence": [{"metric": {"name": "kubernetes_worker_deployment_observation",
        "value": {"desired_replicas": 2, "available_replicas": False}}}]}
    assert adverse_signals(bundle) == []
