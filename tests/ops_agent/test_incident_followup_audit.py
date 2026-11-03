from copy import deepcopy

from scripts.audit_incident_followups import review_candidate, review_sequence
from tests.ops_agent.test_recovery_evaluator import _make_bundle
from tests.ops_agent.test_runtime_case_audit import write_sequence


def sequence():
    values = [(8000, 100, 200), (10000, 100, 200), (12000, 100, 200),
        (9000, -50, 75), (6000, -50, 75), (3000, -50, 75)]
    return [_make_bundle(sample_index=i, latest_lag=lag, slope=slope,
        produce_rate=rate, offset_base=100000 + i * 30000)
        for i, (lag, slope, rate) in enumerate(values)]


def test_followup_does_not_require_reactivation():
    result = review_sequence(sequence())
    assert result["reviewable_followup"]
    assert result["recovery_prefixes"][-1]["state"] == "WORKER_BACKLOG_RECOVERING"
    assert not result["runtime_adapter_eligible"] and not result["causal_truth_verified"]


def test_activation_snapshot_not_relabelled_followup():
    assert review_sequence(sequence()[:3])["status"] == "ACTIVATION_SNAPSHOT_NOT_FOLLOWUP"


def test_cross_incident_and_reordered_time_break_continuity():
    for field in ("identity", "time"):
        documents = sequence()
        if field == "identity":
            documents[3]["incident_id"] = "another-incident"
        else:
            documents[3] = deepcopy(documents[2])
        assert review_sequence(documents)["status"] == "CONTINUITY_UNVERIFIED"


def test_closure_is_sticky_even_if_backlog_regrows():
    documents = sequence()
    for i in range(6, 9):
        documents.append(_make_bundle(sample_index=i, latest_lag=10, slope=0,
            produce_rate=75, offset_base=100000 + i * 30000))
    documents.append(_make_bundle(sample_index=9, latest_lag=9000, slope=100,
        produce_rate=200, offset_base=370000))
    result = review_sequence(documents)
    assert result["status"] == "INCIDENT_CLOSED_BEFORE_OR_AT_CANDIDATE"
    assert result["recovery_prefixes"][-1]["through_index"] == 8


def test_missing_capture_is_not_skipped(tmp_path):
    paths = write_sequence(tmp_path, sequence())
    paths[3].unlink()
    assert review_candidate(paths[-1], tmp_path)["status"] == "CAPTURE_SEQUENCE_GAP"


def test_no_prior_activation_is_not_invented():
    assert review_sequence(sequence()[3:])["status"] == "NO_OBSERVED_ACTIVATION"
