from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.calibration import (  # noqa: E402
    PRESSURE_CANDIDATE_CAPTURE_COUNT,
    PRESSURE_CANDIDATE_LAG_FLOOR,
    PRESSURE_CANDIDATE_SLOPE_FLOOR,
    PRESSURE_CANDIDATE_VERSION,
    evaluate_pressure_activation_candidate,
)
from scripts.worker_backlog_calibration import (  # noqa: E402
    atomic_json,
    baseline_ready,
    collect_sample,
    current_context,
    load_command,
    utc_now,
)


@dataclass(frozen=True)
class Scenario:
    name: str
    purpose: str
    vus: int
    streams: int
    duration: str
    think_time: float


SCENARIOS = (
    Scenario(
        name="short-burst",
        purpose="brief distributed burst below the calibrated persistence envelope",
        vus=100,
        streams=64,
        duration="5s",
        think_time=0.05,
    ),
    Scenario(
        name="sustainable-high",
        purpose="long distributed load near the observed two-Worker drain rate",
        vus=8,
        streams=64,
        duration="180s",
        think_time=0.05,
    ),
    Scenario(
        name="single-transient-spike",
        purpose="one distributed spike intended to cross the lag floor and then drain",
        vus=100,
        streams=64,
        duration="10s",
        think_time=0.05,
    ),
)


_LOG_PATTERNS = {
    "total_http_requests": re.compile(r"^Total HTTP requests:\s*([0-9,]+)\s*$", re.M),
    "error_rate_percent": re.compile(r"^Error rate\s*:\s*([0-9.]+)%\s*$", re.M),
    "event_status_202": re.compile(r"^Event status 202\s*:\s*([0-9,]+)\s*$", re.M),
}


def parse_load_log(path: Path) -> dict[str, int | float | None]:
    text = path.read_text(encoding="utf-8", errors="replace")
    values: dict[str, int | float | None] = {}
    for name, pattern in _LOG_PATTERNS.items():
        match = pattern.search(text)
        if match is None:
            values[name] = None
        elif name == "error_rate_percent":
            values[name] = float(match.group(1))
        else:
            values[name] = int(match.group(1).replace(",", ""))
    return values


def scenario_load_command(
    scenario: Scenario,
    *,
    load_timeout_seconds: int,
) -> list[str]:
    return load_command(
        SimpleNamespace(
            vus=scenario.vus,
            streams=scenario.streams,
            duration=scenario.duration,
            think_time=scenario.think_time,
            load_timeout_seconds=load_timeout_seconds,
        )
    )


def _scenario_measurements(samples: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [
        sample
        for sample in samples
        if isinstance(sample.get("kafka", {}).get("total_lag"), (int, float))
    ]
    peak = max(usable, key=lambda sample: sample["kafka"]["total_lag"])
    load_samples = [sample for sample in usable if sample.get("phase") == "load"]
    positive = [sample for sample in usable if sample["kafka"]["total_lag"] > 0]
    return {
        "sample_count": len(samples),
        "load_phase_sample_count": len(load_samples),
        "max_total_lag": peak["kafka"]["total_lag"],
        "peak_sample_number": peak["sample_number"],
        "first_positive_lag": (
            positive[0]["kafka"]["total_lag"] if positive else None
        ),
        "max_produce_rate_records_per_second": max(
            sample["kafka"]["produce_rate_records_per_second"] for sample in usable
        ),
        "max_committed_offset_rate_records_per_second": max(
            sample["kafka"]["committed_offset_rate_records_per_second"]
            for sample in usable
        ),
        "max_lag_slope_records_per_second": max(
            sample["kafka"]["lag_slope_records_per_second"] for sample in usable
        ),
        "min_lag_slope_records_per_second": min(
            sample["kafka"]["lag_slope_records_per_second"] for sample in usable
        ),
        "max_worker_desired_replicas": max(
            sample["worker"]["desired_replicas"] for sample in usable
        ),
        "max_worker_available_replicas": max(
            sample["worker"]["available_replicas"] for sample in usable
        ),
        "postgres_ready_all_samples": all(
            sample["postgres"]["readiness_body_status"] == "ready"
            for sample in usable
        ),
    }


def run_scenario(
    args: argparse.Namespace,
    experiment_directory: Path,
    scenario: Scenario,
    scenario_number: int,
) -> dict[str, Any]:
    run_directory = experiment_directory / scenario.name
    run_directory.mkdir(parents=True, exist_ok=False)
    started_at = utc_now()
    samples: list[dict[str, Any]] = []
    incident_prefix = f"phase2-5-negative-{scenario.name}"
    baseline = collect_sample(
        run_directory=run_directory,
        run_number=scenario_number,
        sample_number=0,
        phase="baseline",
        context=args.context,
        incident_prefix=incident_prefix,
    )
    samples.append(baseline)
    if not baseline_ready(baseline):
        raise RuntimeError(f"{scenario.name} baseline is not steady")

    initial_keda_contract = {
        key: baseline["keda"][key]
        for key in (
            "scale_target_name",
            "min_replicas",
            "max_replicas",
            "polling_interval_seconds",
            "cooldown_period_seconds",
        )
    }
    baseline_desired = baseline["worker"]["desired_replicas"]
    log_path = run_directory / "k6.log"
    completed = False
    load_returncode: int | None = None
    post_load_steady_count = 0
    load_started_at = utc_now()
    with log_path.open("w", encoding="utf-8", newline="\n") as load_log:
        load_process = subprocess.Popen(
            scenario_load_command(
                scenario,
                load_timeout_seconds=args.load_timeout_seconds,
            ),
            cwd=ROOT,
            stdout=load_log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            sample_number = 1
            next_sample_at = time.monotonic() + args.sample_interval_seconds
            deadline = time.monotonic() + args.recovery_timeout_seconds
            while time.monotonic() < deadline:
                sleep_seconds = next_sample_at - time.monotonic()
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                load_done = load_process.poll() is not None
                sample = collect_sample(
                    run_directory=run_directory,
                    run_number=scenario_number,
                    sample_number=sample_number,
                    phase="recovery" if load_done else "load",
                    context=args.context,
                    incident_prefix=incident_prefix,
                )
                samples.append(sample)
                steady = bool(
                    load_done
                    and sample["kafka"]["total_lag"] == 0
                    and sample["worker"]["desired_replicas"] == baseline_desired
                    and sample["worker"]["available_replicas"] == baseline_desired
                    and sample["keda"]["conditions"].get("Active") == "False"
                    and sample["postgres"]["readiness_body_status"] == "ready"
                )
                post_load_steady_count = post_load_steady_count + 1 if steady else 0
                running = {
                    "scenario": asdict(scenario),
                    "started_at": started_at,
                    "load_started_at": load_started_at,
                    "status": "RUNNING",
                    "candidate_evaluation": evaluate_pressure_activation_candidate(
                        samples
                    ),
                    "samples": samples,
                }
                atomic_json(run_directory / "summary.json", running)
                if post_load_steady_count >= 2:
                    completed = True
                    break
                sample_number += 1
                next_sample_at += args.sample_interval_seconds
        finally:
            if load_process.poll() is None:
                try:
                    load_process.wait(timeout=args.load_timeout_seconds + 120)
                except subprocess.TimeoutExpired:
                    load_process.terminate()
                    load_process.wait(timeout=30)
            load_returncode = load_process.returncode

    final_keda_contract = {
        key: samples[-1]["keda"][key] for key in initial_keda_contract
    }
    candidate = evaluate_pressure_activation_candidate(samples)
    result = {
        "schema_version": "ops.backlog-negative-control-run.v1",
        "scenario": asdict(scenario),
        "started_at": started_at,
        "load_started_at": load_started_at,
        "completed_at": utc_now(),
        "status": (
            "COMPLETE" if completed and load_returncode == 0 else "INCOMPLETE"
        ),
        "load_returncode": load_returncode,
        "load_result": parse_load_log(log_path),
        "initial_keda_contract": initial_keda_contract,
        "final_keda_contract": final_keda_contract,
        "keda_contract_unchanged": initial_keda_contract == final_keda_contract,
        "manual_keda_changes": False,
        "manual_replica_changes": False,
        "returned_to_baseline_twice": post_load_steady_count >= 2,
        "measurements": _scenario_measurements(samples),
        "candidate_evaluation": candidate,
        "samples": samples,
    }
    atomic_json(run_directory / "summary.json", result)
    if result["status"] != "COMPLETE" or not result["keda_contract_unchanged"]:
        raise RuntimeError(f"{scenario.name} did not return to its baseline contract")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run fixed Phase 2.5 pressure-candidate negative controls."
    )
    result.add_argument("--sample-interval-seconds", type=int, default=15)
    result.add_argument("--load-timeout-seconds", type=int, default=420)
    result.add_argument("--recovery-timeout-seconds", type=int, default=1200)
    result.add_argument("--context", default="kind-messaging-ha")
    result.add_argument(
        "--output-root",
        type=Path,
        default=Path("results/ops-agent/negative-control"),
    )
    return result


def main() -> int:
    args = parser().parse_args()
    if current_context() != args.context:
        raise RuntimeError("kubectl current-context does not match negative-control context")
    experiment_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = (
        args.output_root if args.output_root.is_absolute() else ROOT / args.output_root
    )
    experiment_directory = output_root / experiment_id
    experiment_directory.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "schema_version": "ops.backlog-negative-control.v1",
        "experiment_id": experiment_id,
        "started_at": utc_now(),
        "status": "RUNNING",
        "context": args.context,
        "candidate": {
            "version": PRESSURE_CANDIDATE_VERSION,
            "total_lag_floor": PRESSURE_CANDIDATE_LAG_FLOOR,
            "lag_slope_floor_records_per_second": PRESSURE_CANDIDATE_SLOPE_FLOOR,
            "consecutive_capture_count": PRESSURE_CANDIDATE_CAPTURE_COUNT,
            "produce_minus_committed_role": "arithmetic_consistency_check",
            "modified_before_negative_control": False,
        },
        "sampling": {"interval_seconds": args.sample_interval_seconds},
        "scaling": {
            "manual_keda_changes": False,
            "manual_replica_changes": False,
        },
        "scenarios": [asdict(scenario) for scenario in SCENARIOS],
        "runs": [],
    }
    atomic_json(experiment_directory / "manifest.json", manifest)
    try:
        for scenario_number, scenario in enumerate(SCENARIOS, start=1):
            result = run_scenario(
                args,
                experiment_directory,
                scenario,
                scenario_number,
            )
            manifest["runs"].append(
                {
                    "scenario": scenario.name,
                    "status": result["status"],
                    "candidate_result": result["candidate_evaluation"]["result"],
                    "summary_path": (
                        experiment_directory / scenario.name / "summary.json"
                    ).relative_to(ROOT).as_posix(),
                }
            )
            atomic_json(experiment_directory / "manifest.json", manifest)
    except Exception as exc:
        manifest["status"] = "FAILED"
        manifest["completed_at"] = utc_now()
        manifest["error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
        atomic_json(experiment_directory / "manifest.json", manifest)
        raise
    manifest["status"] = "COMPLETE"
    manifest["completed_at"] = utc_now()
    atomic_json(experiment_directory / "manifest.json", manifest)
    print(experiment_directory.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
