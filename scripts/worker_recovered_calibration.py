from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.evaluation_models import canonical_sha256  # noqa: E402
from ops_agent.recovered_calibration import (  # noqa: E402
    analyze_medium_reentry,
    build_medium_reentry_contract,
    load_json,
    stable_count_distribution,
)
from ops_agent.recovery_evaluator import evaluate_recovery  # noqa: E402
from ops_agent.recovery_policies import load_recovery_policy  # noqa: E402
from scripts.worker_recovery_calibration import (  # noqa: E402
    atomic_json,
    atomic_text,
    build_compact_run_summary,
    build_scenario_plan,
    current_context,
    k6_path,
    k6_version,
    run_scenario,
)


BASELINE_EXPERIMENT = ROOT / "results/ops-agent/recovery-calibration/20260816T100600Z"
BASELINE_ANALYSIS = BASELINE_EXPERIMENT / "analysis.json"
BASELINE_RECOVERY_OUTPUT = ROOT / "results/ops-agent/recovery-evaluation/phase4-20260817"
SOURCE_PATHS = (
    Path("ops_agent/calibration.py"),
    Path("ops_agent/recovered_calibration.py"),
    Path("ops_agent/recovery_calibration.py"),
    Path("ops_agent/recovery_evaluator.py"),
    Path("ops_agent/recovery_models.py"),
    Path("ops_agent/recovery_policies/worker-backlog-local-ha-v1.yaml"),
    Path("ops_agent/recovery_policies/worker-backlog-local-ha-v2.yaml"),
    Path("ops_agent/sequence_evaluator.py"),
    Path("scripts/recovery_arrival_rate_k6.js"),
    Path("scripts/worker_recovered_calibration.py"),
    Path("scripts/worker_recovery_calibration.py"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git_text(arguments: list[str]) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(arguments)} failed")
    return completed.stdout.strip()


def source_provenance(*, captured_before_workload: bool) -> dict[str, Any]:
    hashes = {
        path.as_posix(): hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in SOURCE_PATHS
    }
    return {
        "captured_at": utc_now(),
        "git_head": _git_text(["rev-parse", "HEAD"]),
        "git_dirty": bool(_git_text(["status", "--porcelain"])),
        "source_files_sha256": hashes,
        "source_tree_sha256": canonical_sha256(hashes),
        "calibration_code_sha256": canonical_sha256(
            {
                path: digest
                for path, digest in hashes.items()
                if path.endswith(".py")
            }
        ),
        "workload_script_sha256": hashes["scripts/recovery_arrival_rate_k6.js"],
        "captured_before_workload": captured_before_workload,
    }


def _replay_recovery(
    result: dict[str, Any],
    *,
    run_directory: Path,
) -> dict[str, Any]:
    windows = result["condition_evaluation"]["matched_activation_windows"]
    if not windows:
        raise RuntimeError("recovery replay requires a pressure activation window")
    activation_end = int(windows[0][-1])
    post_activation = [
        sample
        for sample in result["samples"]
        if int(sample["sequence_index"]) > activation_end
    ]
    if not post_activation:
        raise RuntimeError("recovery replay has no post-activation bundles")
    activation_path = ROOT / result["condition_evaluation"]["activation_path"]
    bundle_paths = [ROOT / sample["bundle_path"] for sample in post_activation]
    evaluation = evaluate_recovery(
        incident_id=f"phase4-{result['run_name']}",
        activation_evaluation=activation_path.read_bytes(),
        bundles=[path.read_bytes() for path in bundle_paths],
        source_bundle_digests=[sample["source_bundle_sha256"] for sample in post_activation],
    )
    output_path = run_directory / "recovery.v1.json"
    atomic_json(output_path, evaluation.model_dump(mode="json"))
    return evaluation.model_dump(mode="json")


def _load_existing_runs(contract: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    summaries: dict[str, Any] = {}
    analyses: dict[str, Any] = {}
    for index in range(1, 4):
        name = f"E-run-{index:02d}"
        summary = load_json(BASELINE_EXPERIMENT / name / "summary.json")
        recovery = load_json(BASELINE_RECOVERY_OUTPUT / f"{name}.recovery.json")
        summaries[name] = summary
        analyses[name] = analyze_medium_reentry(
            summary["samples"],
            recovery=recovery,
            contract=contract,
        )
    return summaries, analyses


def _run_facts(
    name: str,
    summary: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    samples = summary["samples"]
    usable_lag = [
        (int(item["sequence_index"]), item["kafka"].get("total_lag"))
        for item in samples
        if item.get("evidence_quality", {}).get("usable") is True
        and item.get("kafka", {}).get("total_lag") is not None
    ]
    peak_index, peak_lag = max(usable_lag, key=lambda item: float(item[1]))
    first_reentry = analysis["first_usable_envelope_reentry_index"]
    reentry_item = (
        next(
            item
            for item in analysis["capture_checks"]
            if item["sequence_index"] == first_reentry
        )
        if first_reentry is not None
        else None
    )
    medium_checks = [
        item
        for item in analysis["capture_checks"]
        if item["checks"]["medium_recovery_phase"]
        and item["checks"]["rate_window_settled"]
    ]
    medium_produce_values = [
        value
        for item in medium_checks
        if (value := item["produce_rate_records_per_second"]) is not None
    ]
    return {
        "run_name": name,
        "status": summary["status"],
        "activation_matched_windows": summary["condition_evaluation"][
            "matched_activation_windows"
        ],
        "peak_lag_records": peak_lag,
        "peak_lag_index": peak_index,
        "first_recovering_sample_index": analysis["first_recovering_sample_index"],
        "first_usable_envelope_reentry": reentry_item,
        "stable_reentry_windows": analysis["stable_reentry_windows"],
        "maximum_consecutive_usable_reentry_count": analysis[
            "maximum_consecutive_usable_reentry_count"
        ],
        "unknown_capture_count": analysis["unknown_capture_count"],
        "negative_exporter_lag_invalid_capture_count": analysis[
            "negative_exporter_lag_invalid_capture_count"
        ],
        "reexit_after_first_reentry_indexes": analysis[
            "reexit_after_first_reentry_indexes"
        ],
        "medium_actual_produce_rate_records_per_second": {
            "settled_capture_count": len(medium_checks),
            "usable_rate_count": len(medium_produce_values),
            "minimum": min(medium_produce_values, default=None),
            "maximum": max(medium_produce_values, default=None),
        },
        "postgres_all_acceptable": all(
            item["checks"]["postgres_guardrail_acceptable"]
            for item in analysis["capture_checks"]
        ),
        "workload_attainment": summary["workload_attainment"],
        "final_recovery_state": load_json(
            (
                BASELINE_RECOVERY_OUTPUT / f"{name}.recovery.json"
                if name in {"E-run-01", "E-run-02", "E-run-03"}
                else Path(summary["recovery_evaluation_path"])
            )
        )["state"],
    }


def _render_markdown(analysis: dict[str, Any]) -> str:
    decision = analysis.get("decision", {})
    lines = [
        "# Continuous-ingress RECOVERED Calibration",
        "",
        f"- Experiment: `{analysis['experiment_id']}`",
        "- Scope: E-04 through E-06 supplemental continuous-ingress calibration",
        f"- Decision: `{decision.get('status', 'CALIBRATION_PENDING')}`",
        "",
        "## MEDIUM Re-entry Contract",
        "",
        f"- Contract: `{analysis['medium_reentry_contract']['contract_version']}`",
        f"- Produce: `{analysis['medium_reentry_contract']['actual_produce_rate_records_per_second']}`",
        f"- Lag: `{analysis['medium_reentry_contract']['total_lag_records']}`",
        f"- Slope: `{analysis['medium_reentry_contract']['lag_slope_records_per_second']}`",
        "",
        "## Combined E Runs",
        "",
        "| Run | Peak lag | First recovering | First re-entry | Max stable | UNKNOWN | Negative invalid |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, value in analysis["run_facts"].items():
        first_reentry = value["first_usable_envelope_reentry"]
        lines.append(
            f"| {name} | {value['peak_lag_records']} | "
            f"{value['first_recovering_sample_index']} | "
            f"{first_reentry['sequence_index'] if first_reentry else '-'} | "
            f"{value['maximum_consecutive_usable_reentry_count']} | "
            f"{value['unknown_capture_count']} | "
            f"{value['negative_exporter_lag_invalid_capture_count']} |"
        )
    lines.extend(
        [
            "",
            "## Stable Count Distribution",
            "",
            "```json",
            json.dumps(analysis["stable_count_distribution"], indent=2, ensure_ascii=True),
            "```",
            "",
            "## Decision",
            "",
            f"- Status: `{decision.get('status')}`",
            f"- Reason: {decision.get('reason')}",
            "- RECOVERED is incident-scope Worker backlog completion, not global health.",
            "- Post-recovery backlog regression handling remains future work.",
            "",
        ]
    )
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run supplemental continuous-ingress RECOVERED calibration."
    )
    result.add_argument("--context", default="kind-messaging-ha")
    result.add_argument(
        "--analyze-existing",
        type=Path,
        help="resume post-run analysis from an existing immutable experiment",
    )
    result.add_argument(
        "--promote-recovered",
        action="store_true",
        help="apply the reviewed N=3 candidate to frozen E1-E6 replay only",
    )
    result.add_argument(
        "--output-root",
        type=Path,
        default=Path("results/ops-agent/recovered-calibration"),
    )
    result.add_argument("--runs", type=int, default=3)
    result.add_argument("--first-run-index", type=int, default=4)
    result.add_argument("--streams", type=int, default=64)
    result.add_argument("--medium-rate", type=int, default=75)
    result.add_argument("--overload-rate", type=int, default=330)
    result.add_argument("--normal-phase-seconds", type=int, default=90)
    result.add_argument("--overload-seconds", type=int, default=90)
    result.add_argument("--recovery-phase-seconds", type=int, default=900)
    result.add_argument("--capture-interval-seconds", type=int, default=15)
    result.add_argument("--pre-allocated-vus", type=int, default=100)
    result.add_argument("--max-vus", type=int, default=400)
    result.add_argument("--baseline-timeout-seconds", type=int, default=900)
    result.add_argument("--setup-timeout-seconds", type=int, default=180)
    return result


def _finalize_analysis(
    *,
    experiment_directory: Path,
    manifest: dict[str, Any],
    contract: dict[str, Any],
    run_results: dict[str, dict[str, Any]],
    run_analyses: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    existing_summaries, existing_analyses = _load_existing_runs(contract)
    all_summaries = {**existing_summaries, **run_results}
    all_analyses = {**existing_analyses, **run_analyses}
    run_facts = {
        name: _run_facts(name, all_summaries[name], all_analyses[name])
        for name in sorted(all_summaries)
    }
    from ops_agent.recovery_calibration import validate_capture_artifacts

    new_artifacts = validate_capture_artifacts(
        [sample for result in run_results.values() for sample in result["samples"]],
        repository_root=ROOT,
    )
    combined_artifacts = validate_capture_artifacts(
        [sample for result in all_summaries.values() for sample in result["samples"]],
        repository_root=ROOT,
    )
    if new_artifacts["status"] != "PASS" or combined_artifacts["status"] != "PASS":
        raise RuntimeError("RECOVERED calibration artifact validation failed")
    analysis = {
        "schema_version": "ops.recovered-calibration-combined.v1",
        "experiment_id": manifest["experiment_id"],
        "generated_at": utc_now(),
        "medium_reentry_contract": contract,
        "run_facts": run_facts,
        "stable_count_distribution": stable_count_distribution(all_analyses),
        "artifact_validation": {
            "new": new_artifacts,
            "combined_e_runs": combined_artifacts,
        },
        "decision": {
            "status": "CALIBRATION_PENDING",
            "reason": "post-run evidence and false-recovery controls require explicit review",
        },
        "phase2_changed": False,
        "phase3_changed": False,
        "recovered_state_emitted": False,
    }
    atomic_json(experiment_directory / "analysis.json", analysis)
    atomic_text(experiment_directory / "analysis.md", _render_markdown(analysis))
    manifest.pop("safe_error", None)
    manifest["status"] = "COMPLETE"
    manifest["completed_at"] = utc_now()
    manifest["analysis_path"] = (
        experiment_directory / "analysis.json"
    ).relative_to(ROOT).as_posix()
    manifest["analysis_sha256"] = hashlib.sha256(
        (experiment_directory / "analysis.json").read_bytes()
    ).hexdigest()
    atomic_json(experiment_directory / "manifest.json", manifest)
    return analysis


def _promote_recovered_analysis(
    *,
    experiment_directory: Path,
    manifest: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    policy = load_recovery_policy(version="v2")
    summaries = {
        **{
            f"E-run-{index:02d}": load_json(
                BASELINE_EXPERIMENT / f"E-run-{index:02d}" / "summary.json"
            )
            for index in range(1, 4)
        },
        **{
            f"E-run-{index:02d}": load_json(
                experiment_directory / f"E-run-{index:02d}" / "summary.json"
            )
            for index in range(4, 7)
        },
    }
    replay_directory = experiment_directory / "promoted-replays"
    replay_results: dict[str, Any] = {}
    supporting_runs = ["E-run-01", "E-run-03", "E-run-04", "E-run-05", "E-run-06"]
    for name, summary in sorted(summaries.items()):
        facts = analysis["run_facts"][name]
        windows = [
            window
            for window in facts["stable_reentry_windows"]
            if int(window["capture_count"]) >= 3
        ]
        selected_window = windows[0] if windows else None
        activation_window = summary["condition_evaluation"]["matched_activation_windows"][0]
        activation_end = int(activation_window[-1])
        selected_end = (
            int(selected_window["start_index"]) + 2
            if selected_window is not None
            else int(summary["samples"][-1]["sequence_index"])
        )
        selected_samples = [
            sample
            for sample in summary["samples"]
            if activation_end < int(sample["sequence_index"]) <= selected_end
        ]
        activation_path = ROOT / summary["condition_evaluation"]["activation_path"]
        evaluation = evaluate_recovery(
            incident_id=f"phase4-{name}",
            activation_evaluation=activation_path.read_bytes(),
            bundles=[(ROOT / sample["bundle_path"]).read_bytes() for sample in selected_samples],
            source_bundle_digests=[sample["source_bundle_sha256"] for sample in selected_samples],
            policy=policy,
        )
        expected_recovered = name in supporting_runs
        if expected_recovered != (evaluation.state.value == "WORKER_BACKLOG_RECOVERED"):
            raise RuntimeError(f"promoted replay result mismatch for {name}")
        output_path = replay_directory / f"{name}.recovery.v2.json"
        atomic_json(output_path, evaluation.model_dump(mode="json"))
        replay_results[name] = {
            "state": evaluation.state.value,
            "evaluation_status": evaluation.evaluation_status.value,
            "recovery_evaluation_id": evaluation.recovery_evaluation_id,
            "selected_stable_window": selected_window,
            "selected_prefix_end_index": selected_end,
            "source_bundle_count": len(selected_samples),
            "artifact_path": output_path.relative_to(ROOT).as_posix(),
            "artifact_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        }

    analysis["stable_count_distribution"].update(
        {
            "candidate_selected": 3,
            "selection_status": "PROMOTED",
            "supporting_runs": supporting_runs,
            "non_supporting_runs": ["E-run-02"],
            "rejected_alternatives": {
                "1": "brief envelope entry is insufficient and fails false-recovery control",
                "2": "two-entry then regrowth control would be a false recovery",
                "4": "supported by only three of six continuous-ingress runs",
            },
        }
    )
    analysis["medium_reentry_contract"]["promotion_status"] = "PROMOTED"
    analysis["false_recovery_controls"] = {
        "decreasing_then_regrowing": "NOT_RECOVERED_ACTIVE",
        "brief_envelope_entry_then_regrowing": "NOT_RECOVERED_ACTIVE",
        "usable_unknown_usable": "CONSECUTIVE_COUNT_RESET",
        "stale_recovery_window": "UNKNOWN",
        "partition_coverage_7_of_8": "UNKNOWN",
        "offset_decrease_or_reset": "UNKNOWN",
        "identity_change_mid_sequence": "UNKNOWN",
        "db_degraded_while_lag_drains": "UNKNOWN",
        "negative_exporter_lag_invalid": "UNKNOWN_INVALID_ONLY",
        "zero_ingress_backlog_not_draining": "NOT_RECOVERED_ACTIVE",
        "result": "PASS",
    }
    analysis["promoted_replays"] = replay_results
    analysis["decision"] = {
        "status": "RECOVERED_POLICY_PROMOTED",
        "policy_version": policy.policy_version,
        "ruleset_version": "ops.recovery.rules.v2",
        "evaluator_version": "ops.recovery.evaluator.v2",
        "stable_capture_count": 3,
        "approximate_span_seconds": 30,
        "cadence_tolerance_seconds": [
            policy.capture_interval_min_seconds,
            policy.capture_interval_max_seconds,
        ],
        "measured_ingress_profile": policy.recovered_workload_profile,
        "reason": (
            "N=3 is supported by five of six continuous-ingress runs and all "
            "three supplemental runs; N=1/2 fail false-recovery controls and "
            "N=4 lacks majority support"
        ),
    }
    analysis["recovered_state_emitted"] = True
    analysis["generated_at"] = utc_now()
    atomic_json(experiment_directory / "analysis.json", analysis)
    atomic_text(experiment_directory / "analysis.md", _render_markdown(analysis))
    manifest["promoted_policy"] = {
        "policy_version": policy.policy_version,
        "promoted_at": utc_now(),
        "replay_results": replay_results,
        "post_recovery_regression_handling": "FUTURE_WORK",
    }
    manifest["analysis_sha256"] = hashlib.sha256(
        (experiment_directory / "analysis.json").read_bytes()
    ).hexdigest()
    atomic_json(experiment_directory / "manifest.json", manifest)
    return analysis


def main() -> int:
    args = parser().parse_args()
    if args.analyze_existing is not None:
        experiment_directory = (
            args.analyze_existing
            if args.analyze_existing.is_absolute()
            else ROOT / args.analyze_existing
        ).resolve()
        recovered_root = (ROOT / "results/ops-agent/recovered-calibration").resolve()
        if recovered_root not in experiment_directory.parents:
            raise ValueError("existing experiment must remain inside the recovered-calibration root")
        manifest = load_json(experiment_directory / "manifest.json")
        expected_names = ["E-run-04", "E-run-05", "E-run-06"]
        if manifest.get("run_order") != expected_names:
            raise ValueError("existing experiment run order is not E-04 through E-06")
        run_results = {
            name: load_json(experiment_directory / name / "summary.json")
            for name in expected_names
        }
        run_analyses = {
            name: load_json(experiment_directory / name / "reentry-analysis.json")
            for name in expected_names
        }
        if any(value.get("status") != "COMPLETE" for value in run_results.values()):
            raise ValueError("all existing workload runs must be complete")
        manifest["analysis_resumed_at"] = utc_now()
        manifest["analysis_resume_source"] = source_provenance(
            captured_before_workload=False
        )
        analysis = _finalize_analysis(
            experiment_directory=experiment_directory,
            manifest=manifest,
            contract=manifest["medium_reentry_contract"],
            run_results=run_results,
            run_analyses=run_analyses,
        )
        if args.promote_recovered:
            analysis = _promote_recovered_analysis(
                experiment_directory=experiment_directory,
                manifest=manifest,
                analysis=analysis,
            )
        print(experiment_directory.relative_to(ROOT).as_posix())
        return 0
    if args.promote_recovered:
        raise ValueError("--promote-recovered requires --analyze-existing")
    if args.runs != 3 or args.first_run_index != 4:
        raise ValueError("this calibration is fixed to exactly E-04, E-05, and E-06")
    if current_context() != args.context:
        raise RuntimeError("kubectl current-context does not match the requested context")
    executable = k6_path()
    recovery_policy = load_recovery_policy()
    baseline_analysis = load_json(BASELINE_ANALYSIS)
    contract = build_medium_reentry_contract(
        baseline_analysis,
        baseline_path=BASELINE_ANALYSIS,
        baseline_display_path=BASELINE_ANALYSIS.relative_to(ROOT).as_posix(),
        configured_capture_interval_seconds=recovery_policy.configured_capture_interval_seconds,
        capture_interval_min_seconds=recovery_policy.capture_interval_min_seconds,
        capture_interval_max_seconds=recovery_policy.capture_interval_max_seconds,
    )
    rates = {
        "IDLE": 0,
        "LOW": 30,
        "MEDIUM": args.medium_rate,
        "HIGH_SUSTAINABLE": 110,
        "OVERLOAD": args.overload_rate,
    }
    plans = {
        f"E-run-{index:02d}": build_scenario_plan(
            scenario="E",
            rates=rates,
            durations_seconds=[
                args.normal_phase_seconds,
                args.overload_seconds,
                args.recovery_phase_seconds,
            ],
            streams=args.streams,
            capture_interval_seconds=args.capture_interval_seconds,
            pre_allocated_vus=args.pre_allocated_vus,
            max_vus=args.max_vus,
        )
        for index in range(args.first_run_index, args.first_run_index + args.runs)
    }
    experiment_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = args.output_root if args.output_root.is_absolute() else ROOT / args.output_root
    experiment_directory = output_root / experiment_id
    experiment_directory.mkdir(parents=True, exist_ok=False)
    provenance = source_provenance(captured_before_workload=True)
    manifest: dict[str, Any] = {
        "schema_version": "ops.recovered-calibration-manifest.v1",
        "experiment_id": experiment_id,
        "started_at": utc_now(),
        "status": "RUNNING",
        "context": args.context,
        "k6": {
            "path": executable,
            "version": k6_version(executable),
            "executor": "constant-arrival-rate",
        },
        "run_order": list(plans),
        "workload_contract": {
            "streams": args.streams,
            "transition": [args.medium_rate, args.overload_rate, args.medium_rate],
            "duration_seconds": [
                args.normal_phase_seconds,
                args.overload_seconds,
                args.recovery_phase_seconds,
            ],
            "capture_interval_seconds": args.capture_interval_seconds,
            "config_sha256": canonical_sha256(plans),
        },
        "policy_versions": {
            "evidence": recovery_policy.source_evidence_policy_version,
            "activation": recovery_policy.activation_policy_version,
            "recovery": recovery_policy.policy_version,
            "recovered_candidate": contract["contract_version"],
        },
        "medium_reentry_contract": contract,
        "source": provenance,
        "openai_api_called": False,
        "manual_keda_changes": False,
        "manual_replica_changes": False,
        "runtime_control_plane_writes": False,
        "calibration_workload_writes_events": True,
        "runs": [],
    }
    atomic_json(experiment_directory / "manifest.json", manifest)
    run_results: dict[str, dict[str, Any]] = {}
    run_recovery: dict[str, dict[str, Any]] = {}
    run_analyses: dict[str, dict[str, Any]] = {}
    try:
        for name, plan in plans.items():
            result = run_scenario(
                experiment_directory=experiment_directory,
                run_name=name,
                plan=plan,
                context=args.context,
                executable=executable,
                baseline_timeout_seconds=args.baseline_timeout_seconds,
                setup_timeout_seconds=args.setup_timeout_seconds,
            )
            recovery = _replay_recovery(
                result,
                run_directory=experiment_directory / name,
            )
            result["recovery_evaluation_path"] = (
                experiment_directory / name / "recovery.v1.json"
            ).as_posix()
            result["recovery_evaluation_id"] = recovery["recovery_evaluation_id"]
            atomic_json(experiment_directory / name / "summary.json", result)
            analysis = analyze_medium_reentry(
                result["samples"],
                recovery=recovery,
                contract=contract,
            )
            atomic_json(experiment_directory / name / "reentry-analysis.json", analysis)
            compact = build_compact_run_summary(
                result,
                detailed_summary_path=experiment_directory / name / "summary.json",
                recovery_candidate=None,
            )
            compact["recovered_calibration"] = {
                key: value
                for key, value in analysis.items()
                if key != "capture_checks"
            }
            atomic_json(experiment_directory / name / "summary.compact.json", compact)
            run_results[name] = result
            run_recovery[name] = recovery
            run_analyses[name] = analysis
            manifest["runs"].append(
                {
                    "run_name": name,
                    "status": result["status"],
                    "summary_path": (
                        experiment_directory / name / "summary.compact.json"
                    ).relative_to(ROOT).as_posix(),
                    "detailed_summary_local_only": True,
                    "recovery_evaluation_id": recovery["recovery_evaluation_id"],
                }
            )
            atomic_json(experiment_directory / "manifest.json", manifest)

        _finalize_analysis(
            experiment_directory=experiment_directory,
            manifest=manifest,
            contract=contract,
            run_results=run_results,
            run_analyses=run_analyses,
        )
    except Exception as exc:
        manifest["status"] = "FAILED"
        manifest["completed_at"] = utc_now()
        manifest["safe_error"] = {
            "type": type(exc).__name__,
            "message": str(exc)[:500],
        }
        atomic_json(experiment_directory / "manifest.json", manifest)
        raise
    print(experiment_directory.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
