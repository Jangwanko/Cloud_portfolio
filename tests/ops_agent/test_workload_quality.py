from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from ops_agent.recovery_calibration import build_scenario_plan
from scripts.worker_incident_e2e import require_workload_quality_before_recovery
from scripts.worker_recovery_calibration import (
    WorkloadQualityError,
    analyze_k6_granular_metrics,
    parse_load_generator_samples,
    validate_workload_quality,
)


RATES = {
    "IDLE": 0,
    "LOW": 30,
    "MEDIUM": 75,
    "HIGH_SUSTAINABLE": 110,
    "OVERLOAD": 330,
}


def _trend(value: float = 100.0) -> dict[str, float]:
    return {
        "avg": value,
        "min": value,
        "med": value,
        "p90": value,
        "p95": value,
        "p99": value,
        "max": value,
    }


def _summary(
    *,
    dropped: int = 0,
    failed: int = 0,
    actual_rate: float = 330.0,
) -> dict:
    return {
        "schema_version": "ops.recovery-arrival-workload.v1",
        "executor": "constant-arrival-rate",
        "time_unit": "1s",
        "stream_count": 64,
        "pre_allocated_vus": 100,
        "max_vus": 400,
        "dropped_iterations": dropped,
        "phases": [
            {
                "phase_id": "00_OVERLOAD",
                "profile": "OVERLOAD",
                "scenario_name": "arrival_00_overload",
                "target_rate": 330,
                "time_unit": "1s",
                "duration_seconds": 90,
                "start_offset_seconds": 0,
                "end_offset_seconds": 90,
                "pre_allocated_vus": 100,
                "max_vus": 400,
                "accepted_202": int(actual_rate * 90),
                "failed": failed,
                "iterations": int(actual_rate * 90),
                "dropped_iterations": dropped,
                "http_req_failed_rate": 0.0 if failed == 0 else 0.01,
                "iteration_duration_ms": _trend(),
                "http_req_duration_ms": _trend(90.0),
                "http_accepted_rate_per_second": actual_rate,
            }
        ],
    }


def test_zero_dropped_iterations_allows_workload_quality_to_pass() -> None:
    result = validate_workload_quality(_summary())

    assert result["status"] == "PASS"
    assert result["dropped_iterations_zero"] is True
    assert result["http_failures_zero"] is True
    assert result["all_phase_targets_attained"] is True


def test_dropped_iteration_fails_without_relaxing_target_attainment() -> None:
    result = validate_workload_quality(_summary(dropped=1))

    assert result["status"] == "FAIL"
    assert result["all_phase_targets_attained"] is True
    assert result["dropped_iterations_zero"] is False
    assert result["reason_codes"] == ["DROPPED_ITERATIONS_OBSERVED"]


def test_http_failure_fails_workload_quality() -> None:
    result = validate_workload_quality(_summary(failed=1))

    assert result["status"] == "FAIL"
    assert result["http_failures"] == 1
    assert "HTTP_FAILURES_OBSERVED" in result["reason_codes"]


def test_transient_standard_http_failure_fails_even_when_final_attempt_succeeds() -> None:
    summary = _summary()
    summary["phases"][0]["http_req_failed_rate"] = 0.001

    result = validate_workload_quality(summary)

    assert result["status"] == "FAIL"
    assert result["application_failures"] == 0
    assert result["standard_http_failures"] == 1
    assert result["http_failures"] == 1
    assert "HTTP_FAILURES_OBSERVED" in result["reason_codes"]


def test_phase_target_attainment_failure_is_independent_of_zero_drop_gate() -> None:
    result = validate_workload_quality(_summary(actual_rate=250.0))

    assert result["status"] == "FAIL"
    assert result["dropped_iterations_zero"] is True
    assert result["all_phase_targets_attained"] is False
    assert "PHASE_TARGET_ATTAINMENT_FAILED" in result["reason_codes"]


def test_quality_result_records_vu_allocation_provenance() -> None:
    result = validate_workload_quality(_summary())

    assert result["allocation"] == {
        "pre_allocated_vus": 100,
        "max_vus": 400,
    }
    assert result["executor"] == "constant-arrival-rate"
    assert result["time_unit"] == "1s"


def test_quality_failure_prevents_recovery_and_incident_closure_even_at_zero_lag() -> None:
    manifest = {
        "recovery_evaluator_started": False,
        "incident_closed": False,
        "current_total_lag": 0,
    }
    quality = validate_workload_quality(_summary(dropped=19))

    with pytest.raises(WorkloadQualityError):
        require_workload_quality_before_recovery(manifest, quality)

    assert manifest["workload_quality"] == quality
    assert manifest["workload_quality_gate"] == "FAIL"
    assert manifest["recovery_evaluator_started"] is False
    assert manifest["incident_closed"] is False


def test_granular_audit_preserves_drop_timestamp_phase_and_vu_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = build_scenario_plan(
        scenario="E",
        rates=RATES,
        durations_seconds=[90, 90, 90],
        streams=64,
        pre_allocated_vus=100,
        max_vus=400,
    )
    path = tmp_path / "k6-metrics.json.gz"
    points = [
        {
            "type": "Point",
            "metric": "vus",
            "data": {"time": "2026-08-24T00:00:01Z", "value": 100, "tags": {}},
        },
        {
            "type": "Point",
            "metric": "vus_max",
            "data": {"time": "2026-08-24T00:00:01Z", "value": 400, "tags": {}},
        },
        {
            "type": "Point",
            "metric": "dropped_iterations",
            "data": {
                "time": "2026-08-24T00:01:45Z",
                "value": 2,
                "tags": {"scenario": "arrival_01_overload"},
            },
        },
    ]
    with gzip.open(path, "wt", encoding="utf-8") as target:
        for point in points:
            target.write(json.dumps(point) + "\n")
    monkeypatch.setattr(
        "scripts.worker_recovery_calibration.ROOT",
        tmp_path,
    )

    result = analyze_k6_granular_metrics(path, plan=plan)

    assert result["max_vus_active"] == 100
    assert result["configured_vus_max_observed"] == 400
    assert result["max_vus_initialized"] is None
    assert result["dropped_iterations_observed"] == 2
    assert result["dropped_iteration_points"] == [
        {
            "timestamp": "2026-08-24T00:01:45Z",
            "value": 2,
            "scenario_name": "arrival_01_overload",
            "phase_id": "01_OVERLOAD",
            "profile": "OVERLOAD",
        }
    ]


def test_load_generator_samples_preserve_active_and_initialized_vus(
    tmp_path: Path,
) -> None:
    log = tmp_path / "k6.log"
    log.write_text(
        'time="2026-08-24T00:00:01Z" level=info '
        'msg="PHASE4_LOADGEN_SAMPLE|1787529601000|15.250|01_OVERLOAD|OVERLOAD|'
        '387|400|5000|0" source=console\n',
        encoding="utf-8",
    )

    result = parse_load_generator_samples(log)

    assert result == [
        {
            "timestamp": "2026-08-24T00:00:01+00:00",
            "elapsed_seconds": 15.25,
            "phase_id": "01_OVERLOAD",
            "profile": "OVERLOAD",
            "vus_active": 387,
            "vus_initialized": 400,
            "iterations_completed": 5000,
            "iterations_interrupted": 0,
        }
    ]


def test_k6_contract_materializes_phase_diagnostics_and_granular_output() -> None:
    source = (Path(__file__).resolve().parents[2] / "scripts" / "recovery_arrival_rate_k6.js").read_text(
        encoding="utf-8"
    )
    runner = (
        Path(__file__).resolve().parents[2] / "scripts" / "worker_recovery_calibration.py"
    ).read_text(encoding="utf-8")

    assert "dropped_iterations{scenario:${scenarioName}}" in source
    assert "iteration_duration_ms" in source
    assert "http_req_duration_ms" in source
    assert 'f"json={metrics_path.resolve().as_posix()}"' in runner
