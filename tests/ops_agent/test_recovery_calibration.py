from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.worker_recovery_calibration as recovery_script
from ops_agent.models import EvidenceBundle
from ops_agent.recovery_calibration import (
    SUPPORTED_SCENARIOS,
    analyze_recovery_candidates,
    build_operating_envelope,
    build_scenario_plan,
    derive_rate_candidates,
    estimated_drain_context,
    phase_for_elapsed,
    summarize_recovery_capture,
    validate_capture_artifacts,
    validate_ordered_capture_summaries,
    validate_scenario_plan,
)
from scripts.worker_recovery_calibration import (
    build_compact_run_summary,
    calibration_run_stages,
    evaluate_activation_windows,
    parser as recovery_calibration_parser,
    validate_initial_recovery_pair,
)


ROOT = Path(__file__).resolve().parents[2]
BASELINE = (
    ROOT
    / "results"
    / "ops-agent"
    / "live-baseline"
    / "no-backlog-20260812.json"
)
FALSE_RECOVERY = (
    ROOT
    / "ops_agent"
    / "fixtures"
    / "recovery"
    / "false_recovery_v1.json"
)
RATES = {
    "IDLE": 0,
    "LOW": 30,
    "MEDIUM": 75,
    "HIGH_SUSTAINABLE": 110,
    "OVERLOAD": 340,
}


def _bundle_with_offset_growth() -> EvidenceBundle:
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    metric_step = {
        "kafka_topic_partition_current_offset": 5,
        "kafka_consumergroup_current_offset": 3,
        "kafka_consumergroup_lag": 2,
    }
    for item in payload["evidence"]:
        step = metric_step.get(item["metric"]["name"])
        if step is None or not isinstance(item["metric"].get("value"), list):
            continue
        samples = item["metric"]["value"]
        base = int(float(samples[0]["value"]))
        for index, sample in enumerate(samples):
            sample["value"] = str(base + step * index)
    return EvidenceBundle.model_validate(payload)


def _plan(scenario: str = "A") -> dict:
    return build_scenario_plan(
        scenario=scenario,
        rates=RATES,
        durations_seconds=[90] * len(SUPPORTED_SCENARIOS[scenario]),
    )


def _synthetic_sample(
    *,
    index: int,
    scenario: str,
    profile: str,
    produce: float,
    committed: float,
    lag: float,
    slope: float,
    phase_id: str | None = None,
    usable: bool = True,
    ready: bool = True,
) -> dict:
    return {
        "scenario": scenario,
        "sequence_index": index,
        "collection_timestamp": (
            datetime(2026, 8, 16, tzinfo=timezone.utc) + timedelta(seconds=15 * index)
        ).isoformat(),
        "rate_window_settled": True,
        "phase": {
            "phase_id": phase_id or f"00_{profile}",
            "profile": profile,
        },
        "kafka": {
            "produce_rate_records_per_second": produce,
            "committed_offset_rate_records_per_second": committed,
            "total_lag": lag,
            "lag_slope_records_per_second": slope,
            "maximum_partition_lag_share": 0.2 if lag else 0.0,
        },
        "worker": {"desired_replicas": 2, "available_replicas": 2},
        "keda": {"conditions": {"Ready": "True", "Active": "False"}},
        "worker_db_persist_stage_latency": {
            "mean_seconds": 0.01,
            "p95_finite_bucket_upper_bound_seconds": 0.025,
        },
        "postgres": {
            "readiness_body_status": "ready" if ready else "degraded",
            "values": {
                "primary_reachable": ready,
                "standby_count": 2,
                "sync_standby_count": 2,
                "max_replication_delay_bytes": 0,
            },
        },
        "evidence_quality": {"usable": usable},
    }


def _complete_envelope() -> dict:
    samples = []
    profile_values = {
        "IDLE": (0.0, 0.0, 0.0, 0.0),
        "LOW": (30.0, 30.0, 10.0, 0.0),
        "MEDIUM": (75.0, 75.0, 30.0, 0.0),
        "HIGH_SUSTAINABLE": (110.0, 108.0, 100.0, 2.0),
    }
    scenario_for = {"IDLE": "A", "LOW": "A", "MEDIUM": "B", "HIGH_SUSTAINABLE": "C"}
    for index, (profile, values) in enumerate(profile_values.items()):
        samples.append(
            _synthetic_sample(
                index=index,
                scenario=scenario_for[profile],
                profile=profile,
                produce=values[0],
                committed=values[1],
                lag=values[2],
                slope=values[3],
            )
        )
    return build_operating_envelope(samples)


def _initial_recovery_run_results() -> dict:
    profile_values = {
        "IDLE": ("A", 0.0, 0.0, 0.0, 0.0),
        "LOW": ("A", 30.0, 30.0, 10.0, 0.0),
        "MEDIUM": ("B", 75.0, 75.0, 30.0, 0.0),
        "HIGH_SUSTAINABLE": ("C", 110.0, 108.0, 100.0, 2.0),
    }
    results: dict[str, dict] = {}
    for index, (profile, values) in enumerate(profile_values.items()):
        scenario, produce, committed, lag, slope = values
        results.setdefault(f"{scenario}-run-01", {"samples": []})["samples"].append(
            _synthetic_sample(
                index=index,
                scenario=scenario,
                profile=profile,
                produce=produce,
                committed=committed,
                lag=lag,
                slope=slope,
            )
        )

    for scenario, final_profile, final_rate in (("E", "MEDIUM", 75.0), ("F", "IDLE", 0.0)):
        samples = [
            _synthetic_sample(index=0, scenario=scenario, profile="OVERLOAD", produce=340, committed=100, lag=8000, slope=240),
            _synthetic_sample(index=1, scenario=scenario, profile="OVERLOAD", produce=340, committed=110, lag=12000, slope=230),
            _synthetic_sample(index=2, scenario=scenario, profile="OVERLOAD", produce=340, committed=120, lag=16000, slope=220),
            _synthetic_sample(index=3, scenario=scenario, profile=final_profile, produce=final_rate, committed=130, lag=8000, slope=-80),
            _synthetic_sample(index=4, scenario=scenario, profile=final_profile, produce=final_rate, committed=final_rate, lag=0, slope=0),
        ]
        results[f"{scenario}-run-01"] = {
            "samples": samples,
            "workload_attainment": {"all_targets_attained": True},
            "condition_evaluation": {
                "core_backlog_pressure": "PRESENT",
                "matched_activation_windows": [[0, 1, 2]],
            },
        }
    return results


def test_historical_rate_candidates_are_derived_and_ordered() -> None:
    proposal = derive_rate_candidates(
        observed_sustainable_rate=123.7,
        observed_overload_rate=334.1,
        observed_committed_capacity=135.0,
    )

    rates = proposal["rates"]
    assert rates["IDLE"] == 0
    assert 0 < rates["LOW"] < rates["MEDIUM"] < rates["HIGH_SUSTAINABLE"] < rates["OVERLOAD"]
    assert proposal["provenance"]["production_capacity_constant"] is False


def test_calibration_run_order_gates_repeats_after_initial_e_and_f() -> None:
    initial, additional = calibration_run_stages(3)

    assert initial == [
        ("A-run-01", "A"),
        ("B-run-01", "B"),
        ("C-run-01", "C"),
        ("E-run-01", "E"),
        ("F-run-01", "F"),
    ]
    assert additional == [("E-run-02", "E"), ("E-run-03", "E")]
    assert recovery_calibration_parser().get_default("recovery_phase_seconds") == 600


def test_activation_window_replay_preserves_earlier_present_when_later_capture_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_evaluate(payloads: list[bytes]) -> SimpleNamespace:
        state = "PRESENT" if payloads == [b"1", b"2", b"3"] else "UNKNOWN"
        return SimpleNamespace(
            evaluation_id=f"evaluation-{payloads[0].decode()}",
            conditions={
                recovery_script.ConditionName.CORE_BACKLOG_PRESSURE: SimpleNamespace(
                    state=SimpleNamespace(value=state)
                )
            },
        )

    monkeypatch.setattr(recovery_script, "evaluate_bundle_sequence", fake_evaluate)

    windows, evaluation = evaluate_activation_windows([b"0", b"1", b"2", b"3", b"bad"])

    assert windows == [[1, 2, 3]]
    assert evaluation.evaluation_id == "evaluation-1"


def test_initial_recovery_pair_authorizes_repetition_only_after_reentry_candidates() -> None:
    gate = validate_initial_recovery_pair(_initial_recovery_run_results())

    assert gate["eligible_for_additional_e_repetitions"] is True
    assert gate["policy_applied"] is False
    assert gate["stable_capture_count_selected"] is False


def test_initial_recovery_pair_rejects_missing_continuous_reentry_candidate() -> None:
    results = _initial_recovery_run_results()
    results["E-run-01"]["samples"][-1]["kafka"]["total_lag"] = 1000

    with pytest.raises(RuntimeError, match="repetition gate"):
        validate_initial_recovery_pair(results)


@pytest.mark.parametrize("scenario", sorted(SUPPORTED_SCENARIOS))
def test_scenario_matrix_preserves_rate_transitions_and_identity(scenario: str) -> None:
    plan = _plan(scenario)

    validate_scenario_plan(plan)
    assert [item["profile"] for item in plan["phases"]] == list(
        SUPPORTED_SCENARIOS[scenario]
    )
    assert len({item["phase_id"] for item in plan["phases"]}) == len(plan["phases"])


def test_zero_arrival_rate_is_a_valid_idle_phase() -> None:
    plan = _plan("A")

    phase = phase_for_elapsed(plan, 0)

    assert phase["profile"] == "IDLE"
    assert phase["target_arrival_rate_records_per_second"] == 0


def test_capture_extracts_offset_rates_lag_slope_and_quality_without_mutation() -> None:
    bundle = _bundle_with_offset_growth()
    original = bundle.model_dump(mode="json")
    plan = _plan("B")

    summary = summarize_recovery_capture(
        bundle,
        plan=plan,
        sequence_index=0,
        elapsed_seconds=75,
    )

    assert summary["kafka"]["produce_rate_records_per_second"] == pytest.approx(8.0)
    assert summary["kafka"]["committed_offset_rate_records_per_second"] == pytest.approx(4.8)
    assert summary["kafka"]["lag_slope_records_per_second"] == pytest.approx(3.2)
    assert len(summary["kafka"]["per_partition"]) == 8
    assert summary["evidence_quality"]["required_kafka_usable"] is True
    assert summary["evidence_quality"]["usable"] is True
    assert summary["rate_window_settled"] is True
    assert summary["raw_artifacts"]
    assert all(item["raw_ref"] and item["raw_sha256"] for item in summary["raw_artifacts"])
    assert bundle.model_dump(mode="json") == original


def test_capture_quality_propagates_intermediate_negative_lag_and_arithmetic_mismatch() -> None:
    payload = _bundle_with_offset_growth().model_dump(mode="json")
    for item in payload["evidence"]:
        if (
            item["metric"]["name"] == "kafka_consumergroup_lag"
            and item["labels"].get("partition") == "0"
        ):
            item["metric"]["value"][3]["value"] = "-2"
            break

    summary = summarize_recovery_capture(
        EvidenceBundle.model_validate(payload),
        plan=_plan("B"),
        sequence_index=0,
        elapsed_seconds=75,
    )

    assert summary["evidence_quality"]["usable"] is False
    assert "partition_0_negative_lag" in summary["kafka"]["anomalies"]
    assert "partition_0_offset_lag_arithmetic_mismatch" in summary["kafka"]["anomalies"]


def test_capture_artifact_validation_checks_bundle_and_raw_hashes_inside_repo_root(
    tmp_path: Path,
) -> None:
    bundle = _bundle_with_offset_growth()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
    raw_path = tmp_path / "raw.json"
    raw_path.write_text('{"safe":true}', encoding="utf-8")
    sample = {
        "sequence_index": 0,
        "bundle_path": "bundle.json",
        "source_bundle_sha256": recovery_script.canonical_sha256(
            bundle.model_dump(mode="json")
        ),
        "raw_artifacts": [
            {
                "raw_ref": str(raw_path),
                "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            }
        ],
    }

    result = validate_capture_artifacts([sample], repository_root=tmp_path)

    assert result == {
        "status": "PASS",
        "sample_count": 1,
        "verified_bundle_count": 1,
        "verified_raw_artifact_count": 1,
        "errors": [],
    }


def test_compact_run_summary_excludes_full_sample_timeline_and_binds_detailed_hash(
    tmp_path: Path,
) -> None:
    plan = _plan("E")
    samples = []
    for index, phase in enumerate(plan["phases"]):
        sample = _synthetic_sample(
            index=index,
            scenario="E",
            profile=phase["profile"],
            phase_id=phase["phase_id"],
            produce=float(phase["target_arrival_rate_records_per_second"]),
            committed=100.0,
            lag=float(index * 100),
            slope=float(index),
        )
        sample["source_bundle_sha256"] = f"{'a' if index == 0 else 'b'}" * 64
        sample["raw_artifacts"] = [{"raw_ref": "raw.json", "raw_sha256": "c" * 64}]
        sample["evidence_quality"]["kafka_anomalies"] = []
        samples.append(sample)
    detailed = tmp_path / "summary.json"
    detailed.write_text('{"detailed":true}', encoding="utf-8")
    result = {
        "run_name": "E-run-01",
        "status": "COMPLETE",
        "started_at": "2026-08-16T00:00:00Z",
        "completed_at": "2026-08-16T00:10:00Z",
        "plan": plan,
        "workload_attainment": {"all_targets_attained": True},
        "condition_evaluation": {"core_backlog_pressure": "PRESENT"},
        "samples": samples,
    }

    compact = build_compact_run_summary(
        result,
        detailed_summary_path=detailed,
        recovery_candidate={"status": "ANALYZED", "peak_lag_records": 200},
    )

    assert "samples" not in compact
    assert compact["sample_count"] == 3
    assert compact["raw_artifact_count"] == 3
    assert compact["detailed_summary_sha256"] == hashlib.sha256(
        detailed.read_bytes()
    ).hexdigest()


def test_operating_envelope_uses_settled_fresh_baseline_samples() -> None:
    envelope = _complete_envelope()

    assert envelope["complete"] is True
    assert envelope["profiles"]["MEDIUM"]["actual_produce_rate_records_per_second"]["median"] == 75.0
    assert envelope["profiles"]["HIGH_SUSTAINABLE"]["total_lag_records"]["max"] == 100.0
    assert envelope["profiles"]["LOW"]["postgres_guardrail"]["all_primary_reachable"] is True


@pytest.mark.parametrize(
    ("lag", "slope", "status"),
    [(0, 0, "NO_BACKLOG"), (1000, 0, "NOT_DRAINING"), (1000, 10, "NOT_DRAINING"), (1000, -20, "AVAILABLE")],
)
def test_estimated_drain_semantics_avoid_divide_by_zero(
    lag: int, slope: int, status: str
) -> None:
    result = estimated_drain_context(lag, slope)

    assert result["status"] == status
    if status == "AVAILABLE":
        assert result["estimated_drain_seconds"] == 50
    else:
        assert result["estimated_drain_seconds"] is None


def test_recovery_analysis_emits_candidates_without_state_or_replica_gate() -> None:
    envelope = _complete_envelope()
    samples = [
        _synthetic_sample(index=0, scenario="E", profile="OVERLOAD", produce=340, committed=100, lag=8000, slope=240),
        _synthetic_sample(index=1, scenario="E", profile="OVERLOAD", produce=340, committed=110, lag=12000, slope=230),
        _synthetic_sample(index=2, scenario="E", profile="OVERLOAD", produce=340, committed=120, lag=16000, slope=220),
        _synthetic_sample(index=3, scenario="E", profile="MEDIUM", produce=75, committed=130, lag=12000, slope=-55),
        _synthetic_sample(index=4, scenario="E", profile="MEDIUM", produce=75, committed=125, lag=6000, slope=-50),
        _synthetic_sample(index=5, scenario="E", profile="MEDIUM", produce=75, committed=75, lag=20, slope=0),
    ]
    samples[3]["worker"] = {}
    samples[3]["keda"] = {}

    result = analyze_recovery_candidates(
        samples,
        envelope=envelope,
        matched_activation_windows=[[0, 1, 2]],
    )

    assert result["status"] == "ANALYZED"
    assert result["recovery_state_emitted"] is False
    assert result["first_negative_lag_slope_index"] == 3
    assert result["produce_committed_balance_reversal_index"] == 3
    assert result["candidate_semantics"]["keda_and_replica_role"] == "optional timing context only"
    assert result["candidate_semantics"]["thresholds_promoted"] is False


def test_postgres_degradation_prevents_guardrail_candidate_acceptance() -> None:
    envelope = _complete_envelope()
    samples = [
        _synthetic_sample(index=0, scenario="F", profile="OVERLOAD", produce=340, committed=100, lag=8000, slope=240),
        _synthetic_sample(index=1, scenario="F", profile="OVERLOAD", produce=340, committed=110, lag=12000, slope=230),
        _synthetic_sample(index=2, scenario="F", profile="OVERLOAD", produce=340, committed=120, lag=16000, slope=220),
        _synthetic_sample(index=3, scenario="F", profile="IDLE", produce=0, committed=100, lag=6000, slope=-100, ready=False),
    ]

    result = analyze_recovery_candidates(
        samples,
        envelope=envelope,
        matched_activation_windows=[[0, 1, 2]],
    )

    degraded = next(
        item
        for item in result["operating_envelope_reentry_candidates"]
        if item["sequence_index"] == 3
    )
    assert degraded["postgres_guardrail_candidate_acceptable"] is False
    assert result["stable_reentry_window_candidates"] == []


def test_scenario_manifest_integrity_rejects_tampering() -> None:
    plan = _plan("E")
    altered = deepcopy(plan)
    altered["phases"][1]["target_arrival_rate_records_per_second"] += 1

    with pytest.raises(ValueError, match="plan ID"):
        validate_scenario_plan(altered)


def test_ordered_capture_validation_rejects_reordered_timestamps() -> None:
    plan = _plan("A")
    samples = []
    for index, elapsed in enumerate((0.0, 15.0)):
        phase = phase_for_elapsed(plan, elapsed)
        samples.append(
            {
                "sequence_index": index,
                "collection_timestamp": (
                    datetime(2026, 8, 16, tzinfo=timezone.utc)
                    + timedelta(seconds=(15 if index == 0 else 0))
                ).isoformat(),
                "elapsed_seconds": elapsed,
                "plan_id": plan["plan_id"],
                "phase": phase,
            }
        )

    with pytest.raises(ValueError, match="strictly increasing"):
        validate_ordered_capture_summaries(samples, plan=plan)


def test_false_recovery_fixture_covers_required_adversarial_cases() -> None:
    payload = json.loads(FALSE_RECOVERY.read_text(encoding="utf-8"))
    names = {item["name"] for item in payload["fixtures"]}

    assert names == {
        "decreasing_then_regrowing",
        "brief_envelope_entry_then_regrowing",
        "stale_recovery_window",
        "partition_coverage_7_of_8",
        "offset_decrease_or_reset",
        "identity_change_mid_sequence",
        "db_degraded_while_lag_drains",
        "zero_ingress_backlog_not_draining",
        "negative_exporter_lag_invalid",
        "timestamp_skew_beyond_coherence_window",
    }
    assert all("expected_semantic" in item for item in payload["fixtures"])
