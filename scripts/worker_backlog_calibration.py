from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.calibration import summarize_bundle  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            json.dump(value, target, indent=2, ensure_ascii=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def current_context() -> str:
    result = subprocess.run(
        ["kubectl", "config", "current-context"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("kubectl current-context failed")
    return result.stdout.strip()


def collect_sample(
    *,
    run_directory: Path,
    run_number: int,
    sample_number: int,
    phase: str,
    context: str,
    incident_prefix: str = "phase2-5",
) -> dict[str, Any]:
    bundle_path = run_directory / "bundles" / f"sample-{sample_number:03d}.json"
    incident_id = (
        f"{incident_prefix}-run{run_number:02d}-sample{sample_number:03d}"
    )
    command = [
        sys.executable,
        "-m",
        "ops_agent",
        "collect",
        "--profile",
        "local-ha",
        "--incident-id",
        incident_id,
        "--context",
        context,
        "--output",
        str(bundle_path),
    ]
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    if result.returncode != 0 or not bundle_path.is_file():
        raise RuntimeError(
            "ops_agent collect failed: "
            f"returncode={result.returncode};stderr={result.stderr[-500:]}"
        )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    summary = summarize_bundle(bundle)
    summary.update(
        {
            "sample_number": sample_number,
            "phase": phase,
            "bundle_path": bundle_path.relative_to(ROOT).as_posix(),
            "collector_wall_seconds": round(time.monotonic() - started, 3),
        }
    )
    return summary


def baseline_ready(sample: dict[str, Any]) -> bool:
    kafka = sample["kafka"]
    worker = sample["worker"]
    keda = sample["keda"]
    postgres = sample["postgres"]
    postgres_values = postgres.get("values") or {}
    return bool(
        not kafka["anomalies"]
        and kafka["total_lag"] == 0
        and worker["desired_replicas"] == keda["min_replicas"]
        and worker["available_replicas"] == worker["desired_replicas"]
        and keda["conditions"].get("Ready") == "True"
        and keda["conditions"].get("Active") == "False"
        and postgres["readiness_body_status"] == "ready"
        and postgres_values.get("ha_mode") is True
        and postgres_values.get("primary_reachable") is True
    )


def load_command(args: argparse.Namespace) -> list[str]:
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "test_k6_load.ps1"),
        "-BaseUrl",
        "http://localhost",
        "-Namespace",
        "messaging-app",
        "-K6Profile",
        "single500",
        "-K6SingleVus",
        str(args.vus),
        "-K6StreamCount",
        str(args.streams),
        "-StageDuration",
        args.duration,
        "-ThinkTime",
        str(args.think_time),
        "-TimeoutSec",
        str(args.load_timeout_seconds),
        "-AllowThresholdFailure",
        "-SkipReset",
    ]


def run_once(
    args: argparse.Namespace,
    experiment_directory: Path,
    run_number: int,
) -> dict[str, Any]:
    run_directory = experiment_directory / f"run-{run_number:02d}"
    run_directory.mkdir(parents=True, exist_ok=False)
    samples: list[dict[str, Any]] = []
    started_at = utc_now()

    baseline = collect_sample(
        run_directory=run_directory,
        run_number=run_number,
        sample_number=0,
        phase="baseline",
        context=args.context,
    )
    samples.append(baseline)
    if not baseline_ready(baseline):
        raise RuntimeError(f"run {run_number} baseline is not steady")
    baseline_desired = baseline["worker"]["desired_replicas"]
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

    log_path = run_directory / "k6.log"
    with log_path.open("w", encoding="utf-8", newline="\n") as load_log:
        load_process = subprocess.Popen(
            load_command(args),
            cwd=ROOT,
            stdout=load_log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        load_started_at = utc_now()
        saw_positive_lag = False
        saw_scale_out = False
        sample_number = 1
        next_sample_at = time.monotonic() + args.sample_interval_seconds
        deadline = time.monotonic() + args.recovery_timeout_seconds
        completed = False
        while time.monotonic() < deadline:
            sleep_seconds = next_sample_at - time.monotonic()
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            load_done = load_process.poll() is not None
            phase = "pressure" if not load_done else "drain"
            sample = collect_sample(
                run_directory=run_directory,
                run_number=run_number,
                sample_number=sample_number,
                phase=phase,
                context=args.context,
            )
            samples.append(sample)
            total_lag = sample["kafka"]["total_lag"]
            desired = sample["worker"]["desired_replicas"]
            if isinstance(total_lag, (int, float)) and total_lag > 0:
                saw_positive_lag = True
            if isinstance(desired, int) and desired > baseline_desired:
                saw_scale_out = True
            returned_to_baseline = bool(
                load_done
                and saw_positive_lag
                and saw_scale_out
                and total_lag == 0
                and desired == baseline_desired
                and sample["worker"]["available_replicas"] == baseline_desired
                and sample["keda"]["conditions"].get("Active") == "False"
            )
            atomic_json(
                run_directory / "summary.json",
                {
                    "run": run_number,
                    "started_at": started_at,
                    "load_started_at": load_started_at,
                    "status": "RUNNING",
                    "samples": samples,
                },
            )
            if returned_to_baseline:
                completed = True
                break
            sample_number += 1
            next_sample_at += args.sample_interval_seconds

        if load_process.poll() is None:
            load_process.wait(timeout=args.load_timeout_seconds + 120)
        load_returncode = load_process.returncode

    final_keda_contract = {
        key: samples[-1]["keda"][key] for key in initial_keda_contract
    }
    result = {
        "run": run_number,
        "started_at": started_at,
        "completed_at": utc_now(),
        "status": "COMPLETE" if completed and load_returncode == 0 else "INCOMPLETE",
        "load_returncode": load_returncode,
        "saw_positive_lag": saw_positive_lag,
        "saw_scale_out": saw_scale_out,
        "initial_keda_contract": initial_keda_contract,
        "final_keda_contract": final_keda_contract,
        "keda_contract_unchanged": initial_keda_contract == final_keda_contract,
        "samples": samples,
    }
    atomic_json(run_directory / "summary.json", result)
    if result["status"] != "COMPLETE" or not result["keda_contract_unchanged"]:
        raise RuntimeError(f"run {run_number} did not complete its required flow")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run distributed-stream Worker backlog calibration without scaling writes."
    )
    result.add_argument("--runs", type=int, default=3)
    result.add_argument("--streams", type=int, default=64)
    result.add_argument("--vus", type=int, default=100)
    result.add_argument("--duration", default="30s")
    result.add_argument("--think-time", type=float, default=0.05)
    result.add_argument("--sample-interval-seconds", type=int, default=15)
    result.add_argument("--load-timeout-seconds", type=int, default=420)
    result.add_argument("--recovery-timeout-seconds", type=int, default=1200)
    result.add_argument("--context", default="kind-messaging-ha")
    result.add_argument("--output-root", type=Path, default=Path("results/ops-agent/calibration"))
    return result


def main() -> int:
    args = parser().parse_args()
    if args.runs < 3:
        raise ValueError("calibration requires at least three runs")
    if args.streams < 2:
        raise ValueError("calibration workload must use multiple streams")
    if current_context() != args.context:
        raise RuntimeError("kubectl current-context does not match calibration context")

    experiment_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = args.output_root if args.output_root.is_absolute() else ROOT / args.output_root
    experiment_directory = output_root / experiment_id
    experiment_directory.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "schema_version": "ops.backlog-calibration.v1",
        "experiment_id": experiment_id,
        "started_at": utc_now(),
        "status": "RUNNING",
        "context": args.context,
        "workload": {
            "runs": args.runs,
            "streams": args.streams,
            "vus": args.vus,
            "duration": args.duration,
            "think_time_seconds": args.think_time,
        },
        "sampling": {
            "interval_seconds": args.sample_interval_seconds,
            "policy_thresholds_preselected": False,
            "consecutive_window_policy_preselected": False,
        },
        "scaling": {
            "manual_keda_changes": False,
            "manual_replica_changes": False,
        },
        "runs": [],
    }
    atomic_json(experiment_directory / "manifest.json", manifest)
    try:
        for run_number in range(1, args.runs + 1):
            result = run_once(args, experiment_directory, run_number)
            manifest["runs"].append(
                {
                    "run": run_number,
                    "status": result["status"],
                    "summary_path": (
                        experiment_directory
                        / f"run-{run_number:02d}"
                        / "summary.json"
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
