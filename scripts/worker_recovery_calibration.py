from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.evaluation_models import ConditionName, canonical_sha256  # noqa: E402
from ops_agent.models import EvidenceBundle  # noqa: E402
from ops_agent.recovery_calibration import (  # noqa: E402
    RECOVERY_ANALYSIS_SCHEMA,
    analyze_recovery_candidates,
    build_operating_envelope,
    build_scenario_plan,
    derive_rate_candidates,
    summarize_recovery_capture,
    validate_capture_artifacts,
    validate_ordered_capture_summaries,
)
from ops_agent.sequence_evaluator import evaluate_bundle_sequence  # noqa: E402


SETUP_MARKER = re.compile(r"PHASE4_SETUP_COMPLETE=(\d+)")
SUMMARY_MARKER = re.compile(r"PHASE4_K6_SUMMARY=(\{.*\})")
HISTORICAL_SUSTAINABLE = (
    ROOT
    / "results"
    / "ops-agent"
    / "negative-control"
    / "20260816T040746Z"
    / "sustainable-high"
    / "summary.json"
)
HISTORICAL_POSITIVE_ROOT = (
    ROOT / "results" / "ops-agent" / "calibration" / "20260816T032411Z"
)
CALIBRATION_SOURCE_PATHS = (
    Path("ops_agent/calibration.py"),
    Path("ops_agent/recovery_calibration.py"),
    Path("ops_agent/sequence_evaluator.py"),
    Path("scripts/recovery_arrival_rate_k6.js"),
    Path("scripts/worker_recovery_calibration.py"),
)


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


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            target.write(value)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _run_text(command: list[str], *, timeout: int = 30) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {command[0]}")
    return result.stdout.strip()


def current_context() -> str:
    return _run_text(["kubectl", "config", "current-context"])


def git_provenance() -> dict[str, Any]:
    file_hashes = {
        path.as_posix(): hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in CALIBRATION_SOURCE_PATHS
    }
    return {
        "source_sha": _run_text(["git", "rev-parse", "HEAD"]),
        "source_dirty": bool(_run_text(["git", "status", "--porcelain"])),
        "source_files_sha256": file_hashes,
        "source_tree_sha256": canonical_sha256(file_hashes),
    }


def k6_path() -> str:
    resolved = shutil.which("k6")
    if not resolved:
        raise RuntimeError("local k6 executable is unavailable")
    return resolved


def k6_version(executable: str) -> str:
    return _run_text([executable, "version"])


def historical_rate_proposal() -> dict[str, Any]:
    sustainable = json.loads(HISTORICAL_SUSTAINABLE.read_text(encoding="utf-8"))
    sustainable_rate = float(
        sustainable["measurements"]["max_produce_rate_records_per_second"]
    )
    positive_rates: list[float] = []
    committed_rates: list[float] = []
    source_paths = [HISTORICAL_SUSTAINABLE.relative_to(ROOT).as_posix()]
    for path in sorted(HISTORICAL_POSITIVE_ROOT.glob("run-*/summary.json")):
        source_paths.append(path.relative_to(ROOT).as_posix())
        value = json.loads(path.read_text(encoding="utf-8"))
        samples = value.get("samples", [])
        positive_rates.append(
            max(float(item["kafka"]["produce_rate_records_per_second"]) for item in samples)
        )
        committed_rates.append(
            max(
                float(item["kafka"]["committed_offset_rate_records_per_second"])
                for item in samples
            )
        )
    proposal = derive_rate_candidates(
        observed_sustainable_rate=sustainable_rate,
        observed_overload_rate=min(positive_rates),
        observed_committed_capacity=sum(committed_rates) / len(committed_rates),
    )
    proposal["provenance"]["source_paths"] = source_paths
    proposal["provenance"]["source_sha256"] = {
        path: canonical_sha256(json.loads((ROOT / path).read_text(encoding="utf-8")))
        for path in source_paths
    }
    return proposal


def collect_bundle(
    *,
    run_directory: Path,
    sample_name: str,
    incident_id: str,
    context: str,
) -> tuple[EvidenceBundle, Path, float]:
    bundle_path = run_directory / "bundles" / f"{sample_name}.json"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    raw_directory = run_directory / "raw"
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
        "--artifact-dir",
        str(raw_directory),
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
    wall = time.monotonic() - started
    if result.returncode != 0 or not bundle_path.is_file():
        raise RuntimeError(
            "ops_agent collect failed: "
            f"returncode={result.returncode};stderr={result.stderr[-500:]}"
        )
    return EvidenceBundle.model_validate_json(bundle_path.read_bytes()), bundle_path, wall


def baseline_ready(bundle: EvidenceBundle) -> tuple[bool, dict[str, Any]]:
    from ops_agent.calibration import summarize_bundle

    summary = summarize_bundle(bundle)
    kafka = summary["kafka"]
    worker = summary["worker"]
    keda = summary["keda"]
    postgres = summary["postgres"]
    values = postgres.get("values") or {}
    ready = bool(
        kafka["anomalies"] == []
        and kafka["total_lag"] == 0
        and worker["desired_replicas"] == keda["min_replicas"]
        and worker["available_replicas"] == worker["desired_replicas"]
        and keda["conditions"].get("Ready") == "True"
        and postgres["readiness_body_status"] == "ready"
        and values.get("ha_mode") is True
        and values.get("primary_reachable") is True
        and int(values.get("standby_count", 0)) >= 1
        and int(values.get("sync_standby_count", 0)) >= 1
    )
    return ready, summary


def wait_for_clean_baseline(
    *,
    run_directory: Path,
    context: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    attempt = 0
    latest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        bundle, path, wall = collect_bundle(
            run_directory=run_directory / "baseline-wait",
            sample_name=f"sample-{attempt:03d}",
            incident_id=f"phase4-baseline-{run_directory.name}-{attempt:03d}",
            context=context,
        )
        ready, latest = baseline_ready(bundle)
        latest.update(
            {
                "bundle_path": path.relative_to(ROOT).as_posix(),
                "collector_wall_seconds": wall,
                "ready": ready,
            }
        )
        if ready:
            return latest
        attempt += 1
        time.sleep(15)
    raise RuntimeError(f"clean baseline unavailable: {latest}")


def _k6_phase_payload(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "phase_id": item["phase_id"],
            "profile": item["profile"],
            "target_rate": item["target_arrival_rate_records_per_second"],
            "duration_seconds": item["duration_seconds"],
        }
        for item in plan["phases"]
    ]


def start_k6(
    *,
    executable: str,
    plan: dict[str, Any],
    log_path: Path,
) -> tuple[subprocess.Popen[str], Any]:
    env = os.environ.copy()
    env.update(
        {
            "BASE_URL": "http://127.0.0.1",
            "HOST_HEADER": "localhost",
            "K6_STREAM_COUNT": str(plan["streams"]),
            "K6_PRE_ALLOCATED_VUS": str(plan["pre_allocated_vus"]),
            "K6_MAX_VUS": str(plan["max_vus"]),
            "K6_PHASES_JSON": json.dumps(
                _k6_phase_payload(plan), separators=(",", ":")
            ),
        }
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8", newline="\n")
    process = subprocess.Popen(
        [
            executable,
            "run",
            "--quiet",
            str(ROOT / "scripts" / "recovery_arrival_rate_k6.js"),
        ],
        cwd=ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return process, log_handle


def wait_for_k6_setup(
    process: subprocess.Popen[str],
    log_path: Path,
    *,
    timeout_seconds: int,
) -> float:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        match = SETUP_MARKER.search(text)
        if match:
            setup_epoch = int(match.group(1)) / 1000.0
            return time.monotonic() - max(0.0, time.time() - setup_epoch)
        if process.poll() is not None:
            raise RuntimeError("k6 exited before setup completed")
        time.sleep(1)
    raise RuntimeError("k6 setup marker was not observed")


def parse_k6_summary(log_path: Path) -> dict[str, Any]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    matches = SUMMARY_MARKER.findall(text)
    if not matches:
        raise RuntimeError("k6 summary marker is missing")
    return json.loads(matches[-1])


def validate_workload_attainment(summary: dict[str, Any]) -> dict[str, Any]:
    phases = []
    all_attained = True
    for phase in summary["phases"]:
        target = float(phase["target_rate"])
        actual = float(phase["http_accepted_rate_per_second"])
        ratio = 1.0 if target == 0 and actual == 0 else (actual / target if target > 0 else 0.0)
        attained = bool(
            (target == 0 and actual == 0)
            or (target > 0 and ratio >= 0.90 and phase["failed"] == 0)
        )
        all_attained = all_attained and attained
        phases.append({**phase, "attainment_ratio": ratio, "attained": attained})
    dropped = int(summary.get("dropped_iterations", 0))
    all_attained = all_attained and dropped == 0
    return {
        "all_targets_attained": all_attained,
        "dropped_iterations": dropped,
        "phases": phases,
    }


def evaluate_activation_windows(
    bundle_payloads: list[bytes],
) -> tuple[list[list[int]], Any | None]:
    matched_windows: list[list[int]] = []
    first_present = None
    for start_index in range(0, len(bundle_payloads) - 2):
        evaluation = evaluate_bundle_sequence(bundle_payloads[start_index : start_index + 3])
        core = evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
        if core.state.value != "PRESENT":
            continue
        matched_windows.append([start_index, start_index + 1, start_index + 2])
        if first_present is None:
            first_present = evaluation
    return matched_windows, first_present


def run_scenario(
    *,
    experiment_directory: Path,
    run_name: str,
    plan: dict[str, Any],
    context: str,
    executable: str,
    baseline_timeout_seconds: int,
    setup_timeout_seconds: int,
    on_capture: Callable[[tuple[Path, ...], tuple[dict[str, Any], ...]], None]
    | None = None,
    incident_id_factory: Callable[[int], str] | None = None,
) -> dict[str, Any]:
    run_directory = experiment_directory / run_name
    run_directory.mkdir(parents=True, exist_ok=False)
    baseline = wait_for_clean_baseline(
        run_directory=run_directory,
        context=context,
        timeout_seconds=baseline_timeout_seconds,
    )
    log_path = run_directory / "k6.log"
    process, log_handle = start_k6(
        executable=executable,
        plan=plan,
        log_path=log_path,
    )
    samples: list[dict[str, Any]] = []
    bundle_paths: list[Path] = []
    started_at = utc_now()
    try:
        phase_zero = wait_for_k6_setup(
            process,
            log_path,
            timeout_seconds=setup_timeout_seconds,
        )
        sample_index = 0
        next_sample_at = phase_zero
        deadline = phase_zero + plan["total_duration_seconds"] + 60
        while time.monotonic() < deadline:
            wait = next_sample_at - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            if process.poll() is not None and time.monotonic() >= phase_zero + plan["total_duration_seconds"]:
                break
            bundle, bundle_path, wall = collect_bundle(
                run_directory=run_directory,
                sample_name=f"sample-{sample_index:03d}",
                incident_id=(
                    incident_id_factory(sample_index)
                    if incident_id_factory is not None
                    else f"phase4-{run_name}-sample-{sample_index:03d}"
                ),
                context=context,
            )
            elapsed = max(0.0, time.monotonic() - phase_zero)
            sample = summarize_recovery_capture(
                bundle,
                plan=plan,
                sequence_index=sample_index,
                elapsed_seconds=elapsed,
            )
            sample["bundle_path"] = bundle_path.relative_to(ROOT).as_posix()
            sample["collector_command_wall_seconds"] = wall
            samples.append(sample)
            bundle_paths.append(bundle_path)
            if on_capture is not None:
                on_capture(tuple(bundle_paths), tuple(samples))
            atomic_json(
                run_directory / "summary.json",
                {
                    "schema_version": "ops.recovery-calibration-run.v1",
                    "run_name": run_name,
                    "status": "RUNNING",
                    "started_at": started_at,
                    "plan": plan,
                    "baseline": baseline,
                    "samples": samples,
                },
            )
            sample_index += 1
            next_sample_at = phase_zero + sample_index * plan["capture_interval_seconds"]
        process.wait(timeout=120)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=30)
        log_handle.close()
    workload = parse_k6_summary(log_path)
    attainment = validate_workload_attainment(workload)
    validate_ordered_capture_summaries(samples, plan=plan)
    bundle_payloads = [path.read_bytes() for path in bundle_paths]
    full_evaluation = evaluate_bundle_sequence(bundle_payloads)
    full_evaluation_path = run_directory / "conditions.v2.full.json"
    atomic_json(full_evaluation_path, full_evaluation.model_dump(mode="json"))
    matched_activation_windows, activation_evaluation = evaluate_activation_windows(
        bundle_payloads
    )
    full_core = full_evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
    if activation_evaluation is not None:
        activation_path = run_directory / "conditions.v2.activation.json"
        atomic_json(activation_path, activation_evaluation.model_dump(mode="json"))
    else:
        activation_path = None
    if plan["scenario"] in {"A", "B", "C"} and matched_activation_windows:
        raise RuntimeError(f"baseline scenario {plan['scenario']} triggered pressure")
    if plan["scenario"] in {"E", "F"} and activation_evaluation is None:
        raise RuntimeError(f"recovery scenario {plan['scenario']} did not activate pressure")
    if process.returncode != 0 or not attainment["all_targets_attained"]:
        raise RuntimeError(f"arrival-rate workload did not attain its plan: {attainment}")
    result = {
        "schema_version": "ops.recovery-calibration-run.v1",
        "run_name": run_name,
        "started_at": started_at,
        "completed_at": utc_now(),
        "status": "COMPLETE",
        "plan": plan,
        "baseline": baseline,
        "workload": workload,
        "workload_attainment": attainment,
        "condition_evaluation": {
            "schema_version": full_evaluation.schema_version,
            "evaluation_id": (
                activation_evaluation.evaluation_id
                if activation_evaluation is not None
                else full_evaluation.evaluation_id
            ),
            "core_backlog_pressure": (
                "PRESENT" if activation_evaluation is not None else full_core.state.value
            ),
            "matched_activation_windows": matched_activation_windows,
            "activation_path": (
                activation_path.relative_to(ROOT).as_posix()
                if activation_path is not None
                else None
            ),
            "full_sequence": {
                "evaluation_id": full_evaluation.evaluation_id,
                "evaluation_status": full_evaluation.evaluation_status.value,
                "core_backlog_pressure": full_core.state.value,
                "reason_codes": list(full_core.reason_codes),
                "path": full_evaluation_path.relative_to(ROOT).as_posix(),
            },
        },
        "samples": samples,
    }
    atomic_json(run_directory / "summary.json", result)
    return result


def render_analysis_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        "# Phase 4 Recovery Calibration Analysis",
        "",
        f"- Experiment: `{analysis['experiment_id']}`",
        "- Scope: Worker backlog recovery calibration only; no recovery state emitted",
        "- Workload: host-local k6 constant-arrival-rate, 64 streams",
        "",
        "## Baseline Operating Envelope",
        "",
        "| Profile | Samples | Produce median | Lag median / p95 / max | Slope median |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, value in analysis["operating_envelope"]["profiles"].items():
        produce = value["actual_produce_rate_records_per_second"]
        lag = value["total_lag_records"]
        slope = value["lag_slope_records_per_second"]
        lines.append(
            f"| {name} | {value['sample_count']} | {produce['median']} | "
            f"{lag['median']} / {lag['p95_nearest_rank']} / {lag['max']} | {slope['median']} |"
        )
    lines.extend(["", "## Recovery Candidates", ""])
    for name, value in analysis["recovery_runs"].items():
        lines.append(
            f"- `{name}`: peak lag `{value.get('peak_lag_records')}`, first negative slope index "
            f"`{value.get('first_negative_lag_slope_index')}`, stable re-entry candidates "
            f"`{value.get('stable_reentry_window_candidates')}`"
        )
    policy = analysis["policy_candidates"]
    recovering = policy["recovering_candidate"]["three_capture_candidate"]
    recovered = policy["recovered_candidate"]["three_capture_candidate"]
    cadence = policy["capture_interval_candidate"]
    artifacts = analysis["artifact_validation"]
    lines.extend(
        [
            "",
            "## Policy Candidates",
            "",
            f"- RECOVERING three-capture candidate: `{recovering['validation_status']}`; not promoted",
            f"- RECOVERED three-capture candidate: `{recovered['validation_status']}`; {recovered['limitation']}",
            f"- Capture cadence: configured `{cadence['configured_seconds']}s`, observed "
            f"`{cadence['observed_min_seconds']:.3f}~{cadence['observed_max_seconds']:.3f}s`; tolerance not promoted",
            "- Fixed lag recovery floor: not selected; match the current ingress profile's observed envelope",
            "",
            "## Artifact Validation",
            "",
            f"- Status: `{artifacts['status']}`",
            f"- Bundles: `{artifacts['verified_bundle_count']}/{artifacts['sample_count']}`",
            f"- Raw projections: `{artifacts['verified_raw_artifact_count']}`",
            "",
            "## Boundary",
            "",
            "These values are calibration candidates. No `ops.recovery.v1` evaluator,",
            "RECOVERING/RECOVERED production threshold, LLM recovery decision, or remediation",
            "was implemented.",
            "",
        ]
    )
    return "\n".join(lines)


def _compact_numeric_stats(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "min": None, "median": None, "max": None}
    middle = len(ordered) // 2
    median_value = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": median_value,
        "max": ordered[-1],
    }


def build_compact_run_summary(
    result: dict[str, Any],
    *,
    detailed_summary_path: Path,
    recovery_candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    samples = result["samples"]
    phase_summaries = []
    for phase in result["plan"]["phases"]:
        items = [
            sample
            for sample in samples
            if sample["phase"]["phase_id"] == phase["phase_id"]
        ]
        usable = [
            sample
            for sample in items
            if sample.get("evidence_quality", {}).get("usable") is True
        ]
        phase_summaries.append(
            {
                "phase": phase,
                "sample_count": len(items),
                "usable_sample_count": len(usable),
                "produce_rate_records_per_second": _compact_numeric_stats(
                    [
                        float(value)
                        for sample in usable
                        if (value := sample["kafka"].get("produce_rate_records_per_second"))
                        is not None
                    ]
                ),
                "committed_offset_rate_records_per_second": _compact_numeric_stats(
                    [
                        float(value)
                        for sample in usable
                        if (
                            value := sample["kafka"].get(
                                "committed_offset_rate_records_per_second"
                            )
                        )
                        is not None
                    ]
                ),
                "total_lag_records": _compact_numeric_stats(
                    [
                        float(value)
                        for sample in usable
                        if (value := sample["kafka"].get("total_lag")) is not None
                    ]
                ),
                "lag_slope_records_per_second": _compact_numeric_stats(
                    [
                        float(value)
                        for sample in usable
                        if (
                            value := sample["kafka"].get(
                                "lag_slope_records_per_second"
                            )
                        )
                        is not None
                    ]
                ),
                "worker_desired_replicas": sorted(
                    {
                        sample["worker"].get("desired_replicas")
                        for sample in items
                        if sample["worker"].get("desired_replicas") is not None
                    }
                ),
                "keda_active_values": sorted(
                    {
                        sample["keda"].get("conditions", {}).get("Active")
                        for sample in items
                        if sample["keda"].get("conditions", {}).get("Active")
                        is not None
                    }
                ),
                "postgres_readiness_values": sorted(
                    {
                        sample["postgres"].get("readiness_body_status")
                        for sample in items
                    }
                ),
            }
        )
    excluded = [
        {
            "sequence_index": sample["sequence_index"],
            "phase": sample["phase"]["profile"],
            "anomalies": sample.get("evidence_quality", {}).get(
                "kafka_anomalies", []
            ),
        }
        for sample in samples
        if sample.get("evidence_quality", {}).get("usable") is not True
    ]
    candidate_summary = None
    if recovery_candidate is not None:
        candidate_summary = {
            "status": recovery_candidate.get("status"),
            "matched_activation_windows": recovery_candidate.get(
                "matched_activation_windows", []
            ),
            "peak_lag_records": recovery_candidate.get("peak_lag_records"),
            "peak_lag_index": recovery_candidate.get("peak_lag_index"),
            "first_negative_lag_slope_index": recovery_candidate.get(
                "first_negative_lag_slope_index"
            ),
            "negative_lag_slope_run_candidates": recovery_candidate.get(
                "negative_lag_slope_run_candidates", []
            ),
            "produce_committed_balance_reversal_index": recovery_candidate.get(
                "produce_committed_balance_reversal_index"
            ),
            "stable_reentry_window_candidates": recovery_candidate.get(
                "stable_reentry_window_candidates", []
            ),
            "scale_timing_context": recovery_candidate.get(
                "scale_timing_context", []
            ),
        }
    return {
        "schema_version": "ops.recovery-calibration-run-compact.v1",
        "run_name": result["run_name"],
        "status": result["status"],
        "started_at": result["started_at"],
        "completed_at": result["completed_at"],
        "plan": result["plan"],
        "workload_attainment": result["workload_attainment"],
        "condition_evaluation": result["condition_evaluation"],
        "sample_count": len(samples),
        "quality_excluded_sample_count": len(excluded),
        "quality_excluded_samples": excluded,
        "phase_summaries": phase_summaries,
        "recovery_candidate": candidate_summary,
        "first_source_bundle_sha256": samples[0]["source_bundle_sha256"],
        "last_source_bundle_sha256": samples[-1]["source_bundle_sha256"],
        "raw_artifact_count": sum(len(sample["raw_artifacts"]) for sample in samples),
        "detailed_summary_local_only": True,
        "detailed_summary_sha256": hashlib.sha256(
            detailed_summary_path.read_bytes()
        ).hexdigest(),
    }


def write_compact_run_summaries(
    *,
    experiment_directory: Path,
    run_results: dict[str, dict[str, Any]],
    analysis: dict[str, Any],
) -> dict[str, str]:
    paths: dict[str, str] = {}
    for name, result in run_results.items():
        detailed_path = experiment_directory / name / "summary.json"
        compact_path = experiment_directory / name / "summary.compact.json"
        compact = build_compact_run_summary(
            result,
            detailed_summary_path=detailed_path,
            recovery_candidate=analysis.get("recovery_runs", {}).get(name),
        )
        atomic_json(compact_path, compact)
        paths[name] = compact_path.relative_to(ROOT).as_posix()
    return paths


def analyze_experiment(
    *,
    experiment_id: str,
    run_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    baseline_samples = [
        sample
        for name, result in run_results.items()
        if name.startswith(("A-", "B-", "C-"))
        for sample in result["samples"]
    ]
    envelope = build_operating_envelope(baseline_samples)
    if not envelope["complete"]:
        raise RuntimeError("baseline scenarios did not produce a complete operating envelope")
    recovery_runs: dict[str, Any] = {}
    for name, result in run_results.items():
        if not name.startswith(("E-", "F-")):
            continue
        recovery_runs[name] = analyze_recovery_candidates(
            result["samples"],
            envelope=envelope,
            matched_activation_windows=result["condition_evaluation"][
                "matched_activation_windows"
            ],
        )
    recovery_names = sorted(recovery_runs)
    cadence_seconds: list[float] = []
    for name in recovery_names:
        timestamps = [
            datetime.fromisoformat(sample["collection_timestamp"].replace("Z", "+00:00"))
            for sample in run_results[name]["samples"]
        ]
        cadence_seconds.extend(
            (later - earlier).total_seconds()
            for earlier, later in zip(timestamps, timestamps[1:])
        )
    negative_run_counts = {
        name: [
            item["capture_count"]
            for item in recovery_runs[name].get(
                "negative_lag_slope_run_candidates", []
            )
        ]
        for name in recovery_names
    }
    stable_reentry_counts = {
        name: [
            item["capture_count"]
            for item in recovery_runs[name].get(
                "stable_reentry_window_candidates", []
            )
        ]
        for name in recovery_names
    }
    policy_candidates = {
        "schema_version": "ops.recovery-policy-candidates.v1",
        "promoted_to_evaluator": False,
        "recovering_candidate": {
            "predicate": [
                "existing CORE_BACKLOG_PRESSURE activation is preserved",
                "backlog remains above the matched load-aware envelope",
                "lag slope is negative on consecutive fresh usable captures",
                "committed-offset rate is greater than or equal to produce rate",
                "PostgreSQL readiness remains acceptable",
            ],
            "observed_negative_run_capture_counts": negative_run_counts,
            "three_capture_candidate": {
                "capture_count": 3,
                "approximate_transition_span_seconds": 30,
                "validation_status": "SUPPORTED_BY_ALL_LIVE_RECOVERY_RUNS_NOT_PROMOTED",
            },
        },
        "recovered_candidate": {
            "predicate": [
                "lag returns to the matched load-aware operating envelope",
                "no growth exceeds the observed envelope slope maximum",
                "current ingress is continuously processable",
                "required Kafka evidence is fresh complete and usable",
                "PostgreSQL readiness remains acceptable",
            ],
            "load_aware_envelope_profiles": {
                name: {
                    "total_lag_records": envelope["profiles"][name][
                        "total_lag_records"
                    ],
                    "lag_slope_records_per_second": envelope["profiles"][name][
                        "lag_slope_records_per_second"
                    ],
                }
                for name in ("IDLE", "LOW", "MEDIUM", "HIGH_SUSTAINABLE")
            },
            "observed_stable_reentry_capture_counts": stable_reentry_counts,
            "three_capture_candidate": {
                "capture_count": 3,
                "approximate_transition_span_seconds": 30,
                "validation_status": "NOT_UNIVERSALLY_VALIDATED",
                "limitation": "E-run-02 retained only one usable re-entry candidate because later exporter-negative samples were excluded",
            },
            "fixed_lag_floor_selected": False,
        },
        "capture_interval_candidate": {
            "configured_seconds": 15,
            "observed_interval_count": len(cadence_seconds),
            "observed_min_seconds": min(cadence_seconds) if cadence_seconds else None,
            "observed_median_seconds": (
                sorted(cadence_seconds)[len(cadence_seconds) // 2]
                if cadence_seconds
                else None
            ),
            "observed_max_seconds": max(cadence_seconds) if cadence_seconds else None,
            "tolerance_not_promoted": True,
        },
    }
    artifact_validation = validate_capture_artifacts(
        [
            sample
            for result in run_results.values()
            for sample in result["samples"]
        ],
        repository_root=ROOT,
    )
    if artifact_validation["status"] != "PASS":
        raise RuntimeError("recovery calibration artifact integrity validation failed")
    return {
        "schema_version": RECOVERY_ANALYSIS_SCHEMA,
        "experiment_id": experiment_id,
        "generated_at": utc_now(),
        "operating_envelope": envelope,
        "recovery_runs": recovery_runs,
        "policy_candidates": policy_candidates,
        "artifact_validation": artifact_validation,
        "policy_candidates_only": True,
        "recovery_evaluator_implemented": False,
        "phase3_changed": False,
    }


def validate_initial_recovery_pair(
    run_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    baseline_samples = [
        sample
        for name, result in run_results.items()
        if name.startswith(("A-", "B-", "C-"))
        for sample in result["samples"]
    ]
    envelope = build_operating_envelope(baseline_samples)
    if not envelope["complete"]:
        raise RuntimeError("initial recovery gate requires a complete baseline envelope")

    checks: dict[str, Any] = {}
    for run_name in ("E-run-01", "F-run-01"):
        result = run_results.get(run_name)
        if result is None:
            raise RuntimeError(f"initial recovery gate is missing {run_name}")
        candidate = analyze_recovery_candidates(
            result["samples"],
            envelope=envelope,
            matched_activation_windows=result["condition_evaluation"][
                "matched_activation_windows"
            ],
        )
        unusable_sample_indexes = [
            int(sample["sequence_index"])
            for sample in result["samples"]
            if sample.get("evidence_quality", {}).get("usable") is not True
        ]
        all_postgres_ready = all(
            sample.get("postgres", {}).get("readiness_body_status") == "ready"
            for sample in result["samples"]
        )
        check = {
            "workload_targets_attained": result["workload_attainment"][
                "all_targets_attained"
            ],
            "core_backlog_pressure_present": result["condition_evaluation"][
                "core_backlog_pressure"
            ]
            == "PRESENT",
            "activation_window_required_evidence_usable": bool(
                result["condition_evaluation"]["matched_activation_windows"]
            ),
            "unusable_sample_indexes_outside_candidate_windows": unusable_sample_indexes,
            "all_postgresql_readiness_ready": all_postgres_ready,
            "negative_lag_slope_observed": candidate.get(
                "first_negative_lag_slope_index"
            )
            is not None,
            "operating_envelope_reentry_candidate_observed": bool(
                candidate.get("stable_reentry_window_candidates")
            ),
            "candidate_summary": {
                "peak_lag_records": candidate.get("peak_lag_records"),
                "first_negative_lag_slope_index": candidate.get(
                    "first_negative_lag_slope_index"
                ),
                "negative_lag_slope_run_candidates": candidate.get(
                    "negative_lag_slope_run_candidates", []
                ),
                "stable_reentry_window_candidates": candidate.get(
                    "stable_reentry_window_candidates", []
                ),
            },
        }
        check["eligible"] = all(
            value is True
            for key, value in check.items()
            if key
            not in {
                "candidate_summary",
                "unusable_sample_indexes_outside_candidate_windows",
            }
        )
        checks[run_name] = check

    eligible = all(check["eligible"] for check in checks.values())
    gate = {
        "schema_version": "ops.recovery-repetition-gate.v1",
        "eligible_for_additional_e_repetitions": eligible,
        "policy_applied": False,
        "stable_capture_count_selected": False,
        "checks": checks,
    }
    if not eligible:
        raise RuntimeError(
            "initial E/F recovery pair did not satisfy the evidence-based repetition gate"
        )
    return gate


def scenario_durations(args: argparse.Namespace, scenario: str) -> list[int]:
    if scenario == "PREFLIGHT":
        return [args.preflight_normal_seconds] * 3 + [args.preflight_overload_seconds]
    if scenario == "A":
        return [args.idle_seconds, args.normal_phase_seconds, args.idle_seconds]
    if scenario in {"B", "C"}:
        return [args.normal_phase_seconds] * 3
    if scenario == "D":
        return [args.normal_phase_seconds, args.short_burst_seconds, args.recovery_phase_seconds]
    if scenario in {"E", "F"}:
        return [args.normal_phase_seconds, args.overload_seconds, args.recovery_phase_seconds]
    raise AssertionError(scenario)


def rates_from_args(args: argparse.Namespace) -> dict[str, int]:
    return {
        "IDLE": 0,
        "LOW": args.low_rate,
        "MEDIUM": args.medium_rate,
        "HIGH_SUSTAINABLE": args.high_rate,
        "OVERLOAD": args.overload_rate,
    }


def calibration_run_stages(e_repeats: int) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    if e_repeats < 1:
        raise ValueError("e-repeats must be at least one")
    initial = [
        ("A-run-01", "A"),
        ("B-run-01", "B"),
        ("C-run-01", "C"),
        ("E-run-01", "E"),
        ("F-run-01", "F"),
    ]
    additional = [(f"E-run-{index:02d}", "E") for index in range(2, e_repeats + 1)]
    return initial, additional


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Calibrate load-aware Worker backlog recovery without recovery state rules."
    )
    result.add_argument("--mode", choices=("propose", "preflight", "calibrate"), required=True)
    result.add_argument("--context", default="kind-messaging-ha")
    result.add_argument("--output-root", type=Path, default=Path("results/ops-agent/recovery-calibration"))
    result.add_argument("--low-rate", type=int)
    result.add_argument("--medium-rate", type=int)
    result.add_argument("--high-rate", type=int)
    result.add_argument("--overload-rate", type=int)
    result.add_argument("--streams", type=int, default=64)
    result.add_argument("--capture-interval-seconds", type=int, default=15)
    result.add_argument("--pre-allocated-vus", type=int, default=100)
    result.add_argument("--max-vus", type=int, default=400)
    result.add_argument("--preflight-normal-seconds", type=int, default=60)
    result.add_argument("--preflight-overload-seconds", type=int, default=30)
    result.add_argument("--normal-phase-seconds", type=int, default=90)
    result.add_argument("--idle-seconds", type=int, default=90)
    result.add_argument("--overload-seconds", type=int, default=120)
    result.add_argument("--short-burst-seconds", type=int, default=15)
    result.add_argument("--recovery-phase-seconds", type=int, default=600)
    result.add_argument("--baseline-timeout-seconds", type=int, default=900)
    result.add_argument("--setup-timeout-seconds", type=int, default=180)
    result.add_argument("--e-repeats", type=int, default=3)
    return result


def main() -> int:
    args = parser().parse_args()
    proposal = historical_rate_proposal()
    if args.mode == "propose":
        print(json.dumps(proposal, indent=2, ensure_ascii=True))
        return 0
    if current_context() != args.context:
        raise RuntimeError("kubectl current-context does not match the requested context")
    executable = k6_path()
    version = k6_version(executable)
    if any(value is None for value in (args.low_rate, args.medium_rate, args.high_rate, args.overload_rate)):
        suggested = proposal["rates"]
        args.low_rate = suggested["LOW"]
        args.medium_rate = suggested["MEDIUM"]
        args.high_rate = suggested["HIGH_SUSTAINABLE"]
        args.overload_rate = suggested["OVERLOAD"]
    rates = rates_from_args(args)
    if not 0 < rates["LOW"] < rates["MEDIUM"] < rates["HIGH_SUSTAINABLE"] < rates["OVERLOAD"]:
        raise ValueError("arrival rates must be strictly ordered")
    if args.e_repeats < 1:
        raise ValueError("e-repeats must be at least one")
    experiment_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = args.output_root if args.output_root.is_absolute() else ROOT / args.output_root
    experiment_directory = output_root / experiment_id
    experiment_directory.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "schema_version": "ops.recovery-calibration-manifest.v1",
        "experiment_id": experiment_id,
        "started_at": utc_now(),
        "status": "RUNNING",
        "mode": args.mode,
        "context": args.context,
        "k6": {"path": executable, "version": version, "executor": "constant-arrival-rate"},
        "streams": args.streams,
        "rates": rates,
        "rate_provenance": proposal["provenance"],
        "phase_duration_seconds": {
            "normal": args.normal_phase_seconds,
            "idle": args.idle_seconds,
            "overload": args.overload_seconds,
            "recovery_observation": args.recovery_phase_seconds,
        },
        "recovery_observation_selection": {
            "role": "calibration observation horizon; not a recovery policy threshold",
            "basis": "live E attempts retained negative lag slope after shorter observation windows; the explicit manifest duration is selected to capture a later envelope re-entry candidate",
        },
        "overload_duration_selection": {
            "role": "workload backlog size control; not a condition or recovery threshold",
            "basis": "30 seconds nearly reached the existing three-capture activation boundary while 120 seconds produced backlog beyond the selected recovery horizon; the actual duration remains explicit in this manifest",
        },
        "calibration_order": [
            "A-run-01",
            "B-run-01",
            "C-run-01",
            "E-run-01",
            "F-run-01",
            "initial-recovery-gate",
            "additional-E-repetitions",
        ],
        "requested_e_repetitions": args.e_repeats,
        "capture_interval_seconds": args.capture_interval_seconds,
        "rate_window_seconds": 60,
        "openai_api_called": False,
        "ops_agent_read_only": True,
        "calibration_workload_writes_events": True,
        "manual_keda_changes": False,
        "manual_replica_changes": False,
        "kubernetes_workload_objects_created": False,
        "source": git_provenance(),
        "runs": [],
    }
    atomic_json(experiment_directory / "manifest.json", manifest)
    run_results: dict[str, dict[str, Any]] = {}
    try:
        if args.mode == "preflight":
            run_specs = [("PREFLIGHT-run-01", "PREFLIGHT")]
            additional_run_specs: list[tuple[str, str]] = []
        else:
            run_specs, additional_run_specs = calibration_run_stages(args.e_repeats)
        for run_name, scenario in run_specs:
            plan = build_scenario_plan(
                scenario=scenario,
                rates=rates,
                durations_seconds=scenario_durations(args, scenario),
                streams=args.streams,
                capture_interval_seconds=args.capture_interval_seconds,
                pre_allocated_vus=args.pre_allocated_vus,
                max_vus=args.max_vus,
            )
            result = run_scenario(
                experiment_directory=experiment_directory,
                run_name=run_name,
                plan=plan,
                context=args.context,
                executable=executable,
                baseline_timeout_seconds=args.baseline_timeout_seconds,
                setup_timeout_seconds=args.setup_timeout_seconds,
            )
            run_results[run_name] = result
            manifest["runs"].append(
                {
                    "run_name": run_name,
                    "scenario": scenario,
                    "status": result["status"],
                    "summary_path": (
                        experiment_directory / run_name / "summary.json"
                    ).relative_to(ROOT).as_posix(),
                }
            )
            atomic_json(experiment_directory / "manifest.json", manifest)
        if args.mode == "calibrate":
            repetition_gate = validate_initial_recovery_pair(run_results)
            manifest["initial_recovery_gate"] = repetition_gate
            atomic_json(experiment_directory / "manifest.json", manifest)
            for run_name, scenario in additional_run_specs:
                plan = build_scenario_plan(
                    scenario=scenario,
                    rates=rates,
                    durations_seconds=scenario_durations(args, scenario),
                    streams=args.streams,
                    capture_interval_seconds=args.capture_interval_seconds,
                    pre_allocated_vus=args.pre_allocated_vus,
                    max_vus=args.max_vus,
                )
                result = run_scenario(
                    experiment_directory=experiment_directory,
                    run_name=run_name,
                    plan=plan,
                    context=args.context,
                    executable=executable,
                    baseline_timeout_seconds=args.baseline_timeout_seconds,
                    setup_timeout_seconds=args.setup_timeout_seconds,
                )
                run_results[run_name] = result
                manifest["runs"].append(
                    {
                        "run_name": run_name,
                        "scenario": scenario,
                        "status": result["status"],
                        "summary_path": (
                            experiment_directory / run_name / "summary.json"
                        ).relative_to(ROOT).as_posix(),
                    }
                )
                atomic_json(experiment_directory / "manifest.json", manifest)
        if args.mode == "calibrate":
            analysis = analyze_experiment(
                experiment_id=experiment_id,
                run_results=run_results,
            )
            compact_paths = write_compact_run_summaries(
                experiment_directory=experiment_directory,
                run_results=run_results,
                analysis=analysis,
            )
            for entry in manifest["runs"]:
                entry["detailed_summary_path_local_only"] = entry["summary_path"]
                entry["summary_path"] = compact_paths[entry["run_name"]]
            atomic_json(experiment_directory / "analysis.json", analysis)
            atomic_text(
                experiment_directory / "analysis.md",
                render_analysis_markdown(analysis),
            )
        else:
            preflight = run_results["PREFLIGHT-run-01"]
            analysis = {
                "schema_version": "ops.recovery-rate-preflight.v1",
                "experiment_id": experiment_id,
                "historical_proposal": proposal,
                "workload_attainment": preflight["workload_attainment"],
                "samples": preflight["samples"],
                "operator_selection_required_before_calibration": True,
            }
            atomic_json(experiment_directory / "analysis.json", analysis)
        manifest["status"] = "COMPLETE"
        manifest["completed_at"] = utc_now()
        atomic_json(experiment_directory / "manifest.json", manifest)
    except Exception as exc:
        manifest["status"] = "FAILED"
        manifest["completed_at"] = utc_now()
        manifest["error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
        atomic_json(experiment_directory / "manifest.json", manifest)
        raise
    print(experiment_directory.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
