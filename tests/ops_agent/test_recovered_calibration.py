from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from ops_agent.recovered_calibration import (
    MEDIUM_REENTRY_CONTRACT_VERSION,
    analyze_medium_reentry,
    build_medium_reentry_contract,
    stable_count_distribution,
)


def _baseline() -> dict:
    return {
        "experiment_id": "phase4-baseline",
        "operating_envelope": {
            "complete": True,
            "rate_window_seconds": 60,
            "profiles": {
                "MEDIUM": {
                    "status": "AVAILABLE",
                    "sample_count": 3,
                    "actual_produce_rate_records_per_second": {
                        "min": 74.98,
                        "max": 77.08,
                    },
                    "total_lag_records": {"max": 22},
                    "lag_slope_records_per_second": {"max": 0},
                }
            },
        },
    }


def _contract(tmp_path: Path) -> dict:
    source = tmp_path / "analysis.json"
    source.write_text(json.dumps(_baseline()), encoding="utf-8")
    return build_medium_reentry_contract(
        _baseline(),
        baseline_path=source,
        baseline_display_path="results/baseline/analysis.json",
        configured_capture_interval_seconds=15,
        capture_interval_min_seconds=9,
        capture_interval_max_seconds=21,
    )


def _sample(index: int, *, lag: int = 10, slope: float = -1) -> dict:
    timestamp = datetime(2026, 8, 17, tzinfo=timezone.utc) + timedelta(
        seconds=index * 15
    )
    return {
        "sequence_index": index,
        "source_bundle_id": f"bundle-{index}",
        "collection_timestamp": timestamp.isoformat(),
        "phase": {"profile": "MEDIUM"},
        "target_arrival_rate_records_per_second": 75,
        "rate_window_settled": True,
        "kafka": {
            "produce_rate_records_per_second": 75.0,
            "committed_offset_rate_records_per_second": 76.0,
            "total_lag": lag,
            "lag_slope_records_per_second": slope,
        },
        "postgres": {
            "readiness_body_status": "ready",
            "values": {"ha_mode": True, "primary_reachable": True},
        },
        "evidence_quality": {"usable": True},
    }


def _recovery(count: int) -> dict:
    return {
        "recovery_evaluation_id": "a" * 64,
        "observations": [
            {
                "sequence_index": index,
                "bundle_id": f"bundle-{index}",
                "usable": True,
                "negative_exporter_lag": [],
                "state_after_capture": "WORKER_BACKLOG_RECOVERING",
            }
            for index in range(count)
        ],
    }


def test_contract_freezes_observed_medium_envelope(tmp_path: Path) -> None:
    contract = _contract(tmp_path)

    assert contract["contract_version"] == MEDIUM_REENTRY_CONTRACT_VERSION
    assert contract["actual_produce_rate_records_per_second"]["minimum"] == 74.98
    assert contract["actual_produce_rate_records_per_second"]["maximum"] == 77.08
    assert contract["total_lag_records"]["maximum"] == 22
    assert contract["lag_slope_records_per_second"]["maximum"] == 0
    assert "lag_equals_zero" in contract["not_required"]
    assert "worker_replica_count" in contract["not_required"]
    assert "keda_inactive" in contract["not_required"]


def test_continuous_ingress_nonzero_lag_can_form_stable_reentry(tmp_path: Path) -> None:
    samples = [
        _sample(0, lag=22),
        _sample(1, lag=14),
        _sample(2, lag=7),
    ]
    result = analyze_medium_reentry(
        samples,
        recovery=_recovery(3),
        contract=_contract(tmp_path),
    )

    assert result["maximum_consecutive_usable_reentry_count"] == 3
    assert result["stable_reentry_windows"] == [
        {"start_index": 0, "end_index": 2, "capture_count": 3}
    ]
    assert all(
        item["produce_rate_records_per_second"] == 75
        for item in result["capture_checks"]
    )


def test_zero_ingress_is_not_intrinsically_invalid(tmp_path: Path) -> None:
    samples = [_sample(index, lag=10 - index) for index in range(3)]
    for sample in samples:
        sample["kafka"]["produce_rate_records_per_second"] = 0
        sample["kafka"]["committed_offset_rate_records_per_second"] = 1
    contract = _contract(tmp_path)
    contract["actual_produce_rate_records_per_second"] = {
        "minimum": 0,
        "maximum": 0,
    }

    result = analyze_medium_reentry(samples, recovery=_recovery(3), contract=contract)

    assert result["maximum_consecutive_usable_reentry_count"] == 3


def test_unknown_resets_consecutive_reentry_count(tmp_path: Path) -> None:
    samples = [_sample(index) for index in range(4)]
    recovery = _recovery(4)
    recovery["observations"][2].update(
        {"usable": False, "state_after_capture": "WORKER_BACKLOG_UNKNOWN"}
    )
    result = analyze_medium_reentry(
        samples,
        recovery=recovery,
        contract=_contract(tmp_path),
    )

    assert result["maximum_consecutive_usable_reentry_count"] == 2
    assert result["unknown_capture_count"] == 1
    assert [item["capture_count"] for item in result["stable_reentry_windows"]] == [
        2,
        1,
    ]


def test_negative_exporter_lag_is_invalid_and_resets_window(tmp_path: Path) -> None:
    samples = [_sample(index) for index in range(3)]
    recovery = _recovery(3)
    recovery["observations"][1].update(
        {
            "usable": False,
            "state_after_capture": "WORKER_BACKLOG_UNKNOWN",
            "negative_exporter_lag": [{"exporter_lag_records": -2}],
        }
    )
    result = analyze_medium_reentry(
        samples,
        recovery=recovery,
        contract=_contract(tmp_path),
    )

    assert result["maximum_consecutive_usable_reentry_count"] == 1
    assert result["negative_exporter_lag_invalid_capture_count"] == 1


def test_brief_envelope_entry_then_regrowth_is_not_stable(tmp_path: Path) -> None:
    samples = [_sample(0), _sample(1), _sample(2, lag=1000, slope=10)]
    result = analyze_medium_reentry(
        samples,
        recovery=_recovery(3),
        contract=_contract(tmp_path),
    )

    assert result["maximum_consecutive_usable_reentry_count"] == 2
    assert result["reexit_after_first_reentry_indexes"] == [2]


def test_postgres_degradation_and_cadence_defect_break_reentry(tmp_path: Path) -> None:
    samples = [_sample(index) for index in range(3)]
    samples[1]["postgres"]["readiness_body_status"] = "degraded"
    samples[2]["collection_timestamp"] = (
        datetime(2026, 8, 17, tzinfo=timezone.utc) + timedelta(seconds=90)
    ).isoformat()
    result = analyze_medium_reentry(
        samples,
        recovery=_recovery(3),
        contract=_contract(tmp_path),
    )

    assert result["maximum_consecutive_usable_reentry_count"] == 1
    assert result["capture_checks"][1]["checks"]["postgres_guardrail_acceptable"] is False
    assert result["capture_checks"][2]["checks"]["capture_cadence_acceptable"] is False


def test_worker_and_keda_are_not_reentry_dependencies(tmp_path: Path) -> None:
    samples = [_sample(index) for index in range(3)]
    result = analyze_medium_reentry(
        samples,
        recovery=_recovery(3),
        contract=_contract(tmp_path),
    )

    assert all("worker" not in sample and "keda" not in sample for sample in samples)
    assert result["maximum_consecutive_usable_reentry_count"] == 3


def test_stable_count_distribution_does_not_preselect_n() -> None:
    result = stable_count_distribution(
        {
            "E-run-01": {"maximum_consecutive_usable_reentry_count": 3},
            "E-run-02": {"maximum_consecutive_usable_reentry_count": 1},
            "E-run-03": {"maximum_consecutive_usable_reentry_count": 4},
        }
    )

    assert result["candidate_supporting_runs"]["3"] == ["E-run-01", "E-run-03"]
    assert result["candidate_selected"] is None
    assert result["selection_status"] == "PENDING_EVIDENCE_REVIEW"
