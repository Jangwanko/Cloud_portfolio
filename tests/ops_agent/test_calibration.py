from __future__ import annotations

import json
from pathlib import Path

from ops_agent.calibration import (
    evaluate_pressure_activation_candidate,
    summarize_bundle,
)
from scripts.worker_backlog_negative_controls import SCENARIOS


ROOT = Path(__file__).resolve().parents[2]
CAPTURE = (
    ROOT
    / "results"
    / "ops-agent"
    / "live-baseline"
    / "no-backlog-20260812.json"
)


def test_captured_no_backlog_bundle_has_zero_rates_and_lag() -> None:
    summary = summarize_bundle(json.loads(CAPTURE.read_text(encoding="utf-8")))

    assert summary["kafka"]["anomalies"] == []
    assert summary["kafka"]["window_seconds"] == 60
    assert summary["kafka"]["produce_rate_records_per_second"] == 0.0
    assert summary["kafka"]["committed_offset_rate_records_per_second"] == 0.0
    assert summary["kafka"]["total_lag"] == 0
    assert summary["kafka"]["lag_slope_records_per_second"] == 0.0
    assert set(summary["kafka"]["per_partition"]) == {str(value) for value in range(8)}
    assert summary["worker"]["desired_replicas"] == 2
    assert summary["worker"]["available_replicas"] == 2
    assert summary["keda"]["conditions"]["Ready"] == "True"
    assert summary["postgres"]["values"]["primary_reachable"] is True


def test_missing_stage_series_is_not_coerced_to_zero_latency() -> None:
    summary = summarize_bundle(json.loads(CAPTURE.read_text(encoding="utf-8")))
    stage = summary["worker_db_persist_stage_latency"]

    assert stage["status"] == "MISSING"
    assert stage["observation_count_delta"] is None
    assert stage["mean_seconds"] is None
    assert stage["p95_finite_bucket_upper_bound_seconds"] is None
    assert stage["count_series"] == 0
    assert stage["sum_series"] == 0
    assert stage["matching_evidence_items"] == 1
    assert stage["evidence_status_counts"] == {"MISSING": 1}
    assert stage["freshness_status_counts"] == {"UNKNOWN": 1}


def test_stage_series_with_unknown_freshness_is_not_reported_missing() -> None:
    payload = json.loads(CAPTURE.read_text(encoding="utf-8"))
    source_item = next(
        item
        for item in payload["evidence"]
        if item["tool_id"]
        == "prometheus.messaging_worker_db_persist_stage_latency_seconds.range.v1"
    )
    source_item.update(
        {
            "status": "OK",
            "source_timestamp": source_item["collected_at"],
            "labels": {
                "stage": "db_persist",
                "job": "worker",
                "instance": "worker-0",
            },
        }
    )
    source_item["metric"].update(
        {
            "name": "messaging_worker_stage_latency_seconds_count",
            "value": [
                {"timestamp": 1_000, "value": "1"},
                {"timestamp": 1_060, "value": "2"},
            ],
        }
    )
    source_item["freshness"].update(
        {
            "status": "UNKNOWN",
            "age_seconds": None,
            "max_age_seconds": 15,
        }
    )

    stage = summarize_bundle(payload)["worker_db_persist_stage_latency"]

    assert stage["status"] == "UNKNOWN"
    assert stage["observation_count_delta"] is None
    assert stage["matching_evidence_items"] == 1
    assert stage["evidence_status_counts"] == {"OK": 1}
    assert stage["freshness_status_counts"] == {"UNKNOWN": 1}


def test_frozen_pressure_candidate_matches_all_three_positive_runs() -> None:
    root = ROOT / "results" / "ops-agent" / "calibration" / "20260816T032411Z"

    for run_number in range(1, 4):
        summary = json.loads(
            (root / f"run-{run_number:02d}" / "summary.json").read_text(
                encoding="utf-8"
            )
        )
        result = evaluate_pressure_activation_candidate(summary["samples"])

        assert result["result"] == "PRESENT"
        assert result["matched_windows"]
        assert result["growth_signal"]["independent_vote_count"] == 1
        assert (
            result["growth_signal"]["produce_minus_committed_role"]
            == "arithmetic_consistency_check"
        )


def test_frozen_pressure_candidate_rejects_one_transient_spike() -> None:
    samples = []
    for sample_number, lag, slope in (
        (0, 0, 0),
        (1, 8_000, 133),
        (2, 6_000, -33),
        (3, 2_000, -100),
    ):
        samples.append(
            {
                "sample_number": sample_number,
                "kafka": {
                    "window_seconds": 60,
                    "produce_rate_records_per_second": max(slope, 0) + 120,
                    "committed_offset_rate_records_per_second": 120,
                    "total_lag": lag,
                    "lag_slope_records_per_second": slope,
                    "per_partition": {str(value): {} for value in range(8)},
                    "anomalies": [],
                },
            }
        )

    result = evaluate_pressure_activation_candidate(samples)

    assert result["result"] == "NOT_PRESENT"
    assert result["matched_windows"] == []


def test_negative_control_profiles_are_fixed_and_do_not_mutate_scaling() -> None:
    assert [
        (scenario.name, scenario.vus, scenario.streams, scenario.duration)
        for scenario in SCENARIOS
    ] == [
        ("short-burst", 100, 64, "5s"),
        ("sustainable-high", 8, 64, "180s"),
        ("single-transient-spike", 100, 64, "10s"),
    ]
    source = (
        ROOT / "scripts" / "worker_backlog_negative_controls.py"
    ).read_text(encoding="utf-8").lower()

    for forbidden in (
        "kubectl scale",
        "kubectl patch",
        "kubectl rollout",
        "kubectl delete deployment",
        "kubectl delete scaledobject",
    ):
        assert forbidden not in source
