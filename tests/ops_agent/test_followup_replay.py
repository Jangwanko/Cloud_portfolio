from copy import deepcopy
import json

import pytest

from ops_agent.followup_replay import FollowupReplay, VERSION
from ops_agent.sequence_evaluator import evaluate_bundle_sequence
from tests.ops_agent.test_incident_followup_audit import sequence
from tests.ops_agent.test_recovery_evaluator import _make_bundle
from tests.ops_agent.test_runtime_case_audit import write_sequence
from scripts.replay_followup_diagnosis import replay_run


def start(documents):
    return FollowupReplay(activation_bundles=documents[:3],
        activation=evaluate_bundle_sequence(documents[:3]))


def test_latest_capture_is_projected_not_activation():
    docs = sequence()
    replica = next(e for e in docs[3]["evidence"] if e["metric"]["name"] == "kubernetes_worker_deployment_observation")
    replica["metric"]["value"]["available_replicas"] = 1
    replay = start(docs)
    step = replay.append(docs[3])
    registry = replay.registry()
    evidence = registry.execute("get_worker_replica_status")
    assert evidence.summary["semantic_flag"] == "WORKER_CAPACITY_SHORTFALL"
    assert evidence.summary["adapter_version"] == VERSION
    assert evidence.provenance.source_bundle_digests == [step["projection_bundle_sha256"]]
    assert registry.activation["diagnosis_observed_at"] == step["observed_at"]
    assert registry.activation["activation_observed_at"] != step["observed_at"]


def test_future_changes_cannot_change_emitted_prefix():
    docs = sequence()
    first = start(docs)
    before = first.append(docs[3])
    saved = first.registry().execute("get_postgres_health").model_dump(mode="json")
    bad_future = deepcopy(docs[4])
    bad_future["incident_id"] = "other"
    with pytest.raises(ValueError):
        first.append(bad_future)
    second = start(docs)
    assert second.append(docs[3]) == before
    assert second.registry().execute("get_postgres_health").model_dump(mode="json") == saved


def test_unknown_is_sticky_even_after_good_observations():
    docs = sequence()
    replay = start(docs)
    # Missing a capture produces an invalid cadence, not a padded history.
    assert replay.append(docs[4])["status"] == "BLOCKED"
    assert replay.append(docs[5])["status"] == "BLOCKED"
    with pytest.raises(ValueError):
        replay.registry()


def test_closure_blocks_projection_and_regrowth():
    docs = sequence()
    replay = start(docs)
    for d in docs[3:]:
        assert replay.append(d)["status"] == "ELIGIBLE"
    for i in range(6, 9):
        last = replay.append(_make_bundle(sample_index=i, latest_lag=10, slope=0,
            produce_rate=75, offset_base=100000 + i * 30000))
    assert last["status"] == "CLOSED"
    with pytest.raises(ValueError):
        replay.registry()
    late = replay.append(_make_bundle(sample_index=9, latest_lag=9000, slope=100,
        produce_rate=200, offset_base=370000))
    assert late["status"] == "CLOSED"
    assert late["first_boundary"] == last["first_boundary"]


@pytest.mark.parametrize("kind", ["incident", "timestamp", "scope", "schema"])
def test_invalid_followup_never_leaves_old_projection_available(kind):
    docs = sequence()
    replay = start(docs)
    replay.append(docs[3])
    bad = deepcopy(docs[4])
    if kind == "incident":
        bad["incident_id"] = "foreign-000"
    elif kind == "timestamp":
        bad = deepcopy(docs[3])
    elif kind == "scope":
        bad["scope"]["consumer_group"] = "foreign"
    else:
        bad["collection"]["completed_at"] = "invalid"
    try:
        assert replay.append(bad)["status"] == "BLOCKED"
    except ValueError:
        pass
    with pytest.raises(ValueError):
        replay.registry()


def test_caller_mutation_cannot_change_frozen_tools():
    docs = sequence()
    replay = start(docs)
    step = replay.append(docs[3])
    registry = replay.registry()
    docs[3]["evidence"].clear()
    step["followup_bundle_digests"].clear()
    assert registry.execute("get_postgres_health").status.value == "OK"
    assert registry.activation["followup"]["followup_bundle_digests"]


def test_forged_activation_rejected():
    docs = sequence()
    activation = evaluate_bundle_sequence(docs[:3]).model_dump(mode="json")
    docs[0]["bundle_id"] = "modified"
    with pytest.raises(ValueError):
        FollowupReplay(activation_bundles=docs[:3], activation=activation)


def test_first_followup_offset_reset_is_rejected_against_activation_tail():
    docs = sequence()
    replay = start(docs)
    reset = _make_bundle(sample_index=3, latest_lag=9000, slope=-50,
        produce_rate=75, offset_base=10000)
    with pytest.raises(ValueError, match="offset reset"):
        replay.append(reset)
    with pytest.raises(ValueError):
        replay.registry()


def test_unusable_replica_is_not_promoted_by_valid_continuity():
    docs = sequence()
    e = next(e for e in docs[3]["evidence"] if e["metric"]["name"] == "kubernetes_worker_deployment_observation")
    e["metric"]["value"]["observed_generation"] += 1
    replay = start(docs)
    assert replay.append(docs[3])["status"] == "ELIGIBLE"
    evidence = replay.registry().execute("get_worker_replica_status")
    assert evidence.status.value == "UNAVAILABLE"
    assert "semantic_flag" not in evidence.summary


def test_file_replay_writes_prefix_before_reading_invalid_future(tmp_path):
    docs = sequence()
    paths = write_sequence(tmp_path, docs)
    run = paths[0].parent.parent
    (run / "conditions.v2.activation.json").write_text(
        evaluate_bundle_sequence(docs[:3]).model_dump_json(), encoding="utf-8")
    short = tmp_path / "short"
    result = replay_run(run_dir=run, activation_end=2, through=3, output_dir=short)
    assert result["status_counts"] == {"ELIGIBLE": 1}
    assert result["steps"][0]["fixed_contract_valid"] is True
    paths[4].write_text("invalid future json", encoding="utf-8")
    longer = tmp_path / "longer"
    with pytest.raises(ValueError):
        replay_run(run_dir=run, activation_end=2, through=5, output_dir=longer)
    assert (short / "step-003.json").read_bytes() == (longer / "step-003.json").read_bytes()
    assert json.loads((longer / "failure.json").read_text())["completed_steps"] == 1
    with pytest.raises(FileExistsError):
        replay_run(run_dir=run, activation_end=2, through=3, output_dir=short)


def test_different_history_changes_binding_even_for_same_current_snapshot():
    docs = sequence()
    other = deepcopy(docs)
    # Earlier metadata differs without changing the current snapshot or measurements.
    other[3]["bundle_id"] = "other-valid-bundle"
    a, b = start(docs), start(other)
    a.append(docs[3])
    b.append(other[3])
    a.append(docs[4])
    b.append(other[4])
    ea = a.registry().execute("get_postgres_health")
    eb = b.registry().execute("get_postgres_health")
    assert ea.provenance.source_bundle_digests == eb.provenance.source_bundle_digests
    assert ea.evidence_id != eb.evidence_id
