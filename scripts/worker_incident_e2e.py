from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.diagnosis_agent import (  # noqa: E402
    DiagnosisOutputContractFailure,
    OpenAIResponsesClient,
    load_openai_configuration,
    run_diagnosis,
)
from ops_agent.diagnosis_models import DiagnosisPolicy, DiagnosisRun  # noqa: E402
from ops_agent.evaluation_models import (  # noqa: E402
    ConditionName,
    ConditionState,
    canonical_sha256,
)
from ops_agent.incident_lifecycle import (  # noqa: E402
    attach_diagnosis,
    attach_recovery_evaluation,
    create_incident,
)
from ops_agent.incident_models import (  # noqa: E402
    IncidentLifecycleState,
    IncidentProvenance,
    IncidentRecord,
)
from ops_agent.models import EvidenceBundle  # noqa: E402
from ops_agent.recovery_calibration import (  # noqa: E402
    build_scenario_plan,
    validate_capture_artifacts,
)
from ops_agent.recovery_evaluator import evaluate_recovery  # noqa: E402
from ops_agent.recovery_models import RecoveryEvaluation, RecoveryState  # noqa: E402
from ops_agent.recovery_policies import load_recovery_policy  # noqa: E402
from ops_agent.sequence_evaluator import evaluate_bundle_sequence  # noqa: E402
from ops_agent.sequence_models import SequenceConditionEvaluation  # noqa: E402
from scripts.worker_recovery_calibration import (  # noqa: E402
    atomic_json,
    baseline_ready,
    collect_bundle,
    k6_path,
    k6_version,
    run_scenario,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git_text(arguments: list[str]) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git provenance command failed: {' '.join(arguments)}")
    return result.stdout.strip()


def source_tree_sha256() -> str:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("source tree inventory failed")
    paths = sorted(item for item in result.stdout.split(b"\0") if item)
    digest = hashlib.sha256()
    for raw_path in paths:
        relative = raw_path.decode("utf-8")
        path = (ROOT / relative).resolve()
        if ROOT.resolve() not in path.parents or not path.is_file():
            raise RuntimeError("source tree inventory escaped the repository")
        digest.update(relative.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("incident artifacts must remain inside the repository") from exc


def _write_model(path: Path, value: Any) -> None:
    atomic_json(path, value.model_dump(mode="json"))


def collect_clean_preflight(
    *,
    directory: Path,
    context: str,
    capture_interval_seconds: int,
) -> dict[str, Any]:
    bundles: list[EvidenceBundle] = []
    paths: list[Path] = []
    summaries: list[dict[str, Any]] = []
    for index in range(3):
        bundle, path, wall = collect_bundle(
            run_directory=directory,
            sample_name=f"sample-{index:03d}",
            incident_id=f"phase5-preflight-sample-{index:03d}",
            context=context,
        )
        ready, summary = baseline_ready(bundle)
        summary["ready"] = ready
        summary["collector_wall_seconds"] = wall
        summary["bundle_path"] = _relative(path)
        if not ready:
            raise RuntimeError("Phase 5.1 preflight did not observe a clean local-ha baseline")
        bundles.append(bundle)
        paths.append(path)
        summaries.append(summary)
        if index < 2:
            time.sleep(capture_interval_seconds)
    evaluation = evaluate_bundle_sequence(
        [bundle.model_dump(mode="json") for bundle in bundles]
    )
    core = evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
    if core.state == ConditionState.PRESENT:
        raise RuntimeError("Phase 5.1 preflight unexpectedly detected backlog pressure")
    evaluation_path = directory / "conditions.v2.json"
    _write_model(evaluation_path, evaluation)
    return {
        "status": "PASS",
        "bundle_paths": [_relative(path) for path in paths],
        "condition_evaluation_id": evaluation.evaluation_id,
        "core_backlog_pressure": core.state.value,
        "condition_path": _relative(evaluation_path),
        "summaries": summaries,
    }


def _safe_failure(exc: BaseException) -> dict[str, Any]:
    status = getattr(exc, "code", None)
    return {
        "classification": getattr(exc, "classification", type(exc).__name__),
        "http_status": status if isinstance(status, int) else None,
    }


def _diagnosis_worker(
    *,
    state: dict[str, Any],
    activation: SequenceConditionEvaluation,
    bundle_paths: tuple[Path, ...],
    output_path: Path,
    api_key: str,
    model: str,
) -> None:
    try:
        bundles = [
            EvidenceBundle.model_validate_json(path.read_bytes())
            for path in bundle_paths
        ]
        policy = DiagnosisPolicy(model=model)
        client = OpenAIResponsesClient(
            api_key=api_key,
            timeout_seconds=policy.request_timeout_seconds,
            max_retries=policy.max_retries,
        )
        diagnosis = run_diagnosis(
            bundles=bundles,
            condition_evaluation=activation,
            client=client,
            policy=policy,
        )
        _write_model(output_path, diagnosis)
        state["diagnosis"] = diagnosis
    except DiagnosisOutputContractFailure as exc:
        state["diagnosis_error"] = {
            **_safe_failure(exc),
            "initial_validation_error": exc.initial_error.get("code"),
            "final_validation_error": (
                exc.final_error.get("code") if exc.final_error is not None else None
            ),
        }
    except BaseException as exc:  # preserve a safe gate failure, never the credential
        state["diagnosis_error"] = _safe_failure(exc)


def _evaluate_recovery_prefixes(
    *,
    incident_id: str,
    activation: SequenceConditionEvaluation,
    result: dict[str, Any],
    run_directory: Path,
    activation_end_index: int,
) -> tuple[dict[RecoveryState, RecoveryEvaluation], RecoveryEvaluation | None]:
    post_activation = [
        sample
        for sample in result["samples"]
        if int(sample["sequence_index"]) > activation_end_index
    ]
    if not post_activation:
        raise RuntimeError("actual incident has no post-activation evidence")
    policy = load_recovery_policy("local-ha", "v2")
    first: dict[RecoveryState, RecoveryEvaluation] = {}
    final: RecoveryEvaluation | None = None
    candidate_counts = {1, len(post_activation)}
    for end in range(2, len(post_activation)):
        window = post_activation[end - 2 : end + 1]
        usable = all(
            item.get("evidence_quality", {}).get("usable") is True
            for item in window
        )
        kafka = [item.get("kafka", {}) for item in window]
        draining = usable and all(
            isinstance(item.get("lag_slope_records_per_second"), (int, float))
            and item["lag_slope_records_per_second"] < 0
            and isinstance(item.get("produce_rate_records_per_second"), (int, float))
            and isinstance(
                item.get("committed_offset_rate_records_per_second"),
                (int, float),
            )
            and item["committed_offset_rate_records_per_second"]
            >= item["produce_rate_records_per_second"]
            for item in kafka
        )
        if draining:
            candidate_counts.add(end + 1)
        within_envelope = usable and all(
            isinstance(item.get("total_lag"), (int, float))
            and policy.recovered_total_lag_maximum is not None
            and item["total_lag"] <= policy.recovered_total_lag_maximum
            and isinstance(item.get("lag_slope_records_per_second"), (int, float))
            and policy.recovered_lag_slope_maximum is not None
            and item["lag_slope_records_per_second"]
            <= policy.recovered_lag_slope_maximum
            and isinstance(item.get("produce_rate_records_per_second"), (int, float))
            and policy.recovered_actual_produce_rate_minimum is not None
            and policy.recovered_actual_produce_rate_maximum is not None
            and policy.recovered_actual_produce_rate_minimum
            <= item["produce_rate_records_per_second"]
            <= policy.recovered_actual_produce_rate_maximum
            and isinstance(
                item.get("committed_offset_rate_records_per_second"),
                (int, float),
            )
            and item["committed_offset_rate_records_per_second"]
            >= item["produce_rate_records_per_second"]
            for item in kafka
        )
        if within_envelope:
            candidate_counts.add(end + 1)
            if end + 3 <= len(post_activation):
                candidate_counts.add(end + 3)
    matched_count: int | None = None
    for count in sorted(candidate_counts):
        selected = post_activation[:count]
        evaluation = evaluate_recovery(
            incident_id=incident_id,
            activation_evaluation=activation,
            bundles=[
                (ROOT / sample["bundle_path"]).read_bytes()
                for sample in selected
            ],
            source_bundle_digests=[
                sample["source_bundle_sha256"] for sample in selected
            ],
            policy=policy,
        )
        final = evaluation
        if evaluation.state in {
            RecoveryState.WORKER_BACKLOG_ACTIVE,
            RecoveryState.WORKER_BACKLOG_RECOVERING,
            RecoveryState.WORKER_BACKLOG_RECOVERED,
        }:
            first.setdefault(evaluation.state, evaluation)
        if all(
            state in first
            for state in (
                RecoveryState.WORKER_BACKLOG_ACTIVE,
                RecoveryState.WORKER_BACKLOG_RECOVERING,
                RecoveryState.WORKER_BACKLOG_RECOVERED,
            )
        ):
            matched_count = count
            break
    if matched_count is not None and matched_count < len(post_activation):
        final = evaluate_recovery(
            incident_id=incident_id,
            activation_evaluation=activation,
            bundles=[
                (ROOT / sample["bundle_path"]).read_bytes()
                for sample in post_activation
            ],
            source_bundle_digests=[
                sample["source_bundle_sha256"] for sample in post_activation
            ],
            policy=policy,
        )
    names = {
        RecoveryState.WORKER_BACKLOG_ACTIVE: "recovery-active.json",
        RecoveryState.WORKER_BACKLOG_RECOVERING: "recovery-recovering.json",
        RecoveryState.WORKER_BACKLOG_RECOVERED: "recovery-recovered.json",
    }
    evaluation_directory = run_directory / "evaluations"
    for state, evaluation in first.items():
        _write_model(evaluation_directory / names[state], evaluation)
    if final is not None and final.recovery_evaluation_id not in {
        item.recovery_evaluation_id for item in first.values()
    }:
        _write_model(evaluation_directory / "recovery-post-closure.json", final)
    return first, final


def _observed_at(evaluation: RecoveryEvaluation) -> datetime:
    return evaluation.source_bundles[-1].collection_completed_at


def _assemble_incident(
    *,
    incident: IncidentRecord,
    diagnosis: DiagnosisRun,
    recovery: dict[RecoveryState, RecoveryEvaluation],
    final_recovery: RecoveryEvaluation | None,
    run_directory: Path,
) -> IncidentRecord:
    paths = {
        RecoveryState.WORKER_BACKLOG_ACTIVE: run_directory
        / "evaluations"
        / "recovery-active.json",
        RecoveryState.WORKER_BACKLOG_RECOVERING: run_directory
        / "evaluations"
        / "recovery-recovering.json",
        RecoveryState.WORKER_BACKLOG_RECOVERED: run_directory
        / "evaluations"
        / "recovery-recovered.json",
    }
    actions: list[tuple[datetime, str, Any, Path]] = [
        (diagnosis.completed_at, "diagnosis", diagnosis, run_directory / "diagnosis.json")
    ]
    actions.extend(
        (_observed_at(value), "recovery", value, paths[state])
        for state, value in recovery.items()
    )
    for _, kind, value, path in sorted(actions, key=lambda item: (item[0], item[1])):
        if kind == "diagnosis":
            incident = attach_diagnosis(
                incident,
                value,
                artifact_ref=_relative(path),
            )
        else:
            incident = attach_recovery_evaluation(
                incident,
                value,
                artifact_ref=_relative(path),
            )
    if incident.lifecycle_state != IncidentLifecycleState.CLOSED:
        raise RuntimeError("actual recovery did not close the incident")
    if (
        final_recovery is not None
        and final_recovery.recovery_evaluation_id
        not in {item.evaluation_id for item in incident.recovery.evaluations}
    ):
        post_path = run_directory / "evaluations" / "recovery-post-closure.json"
        if post_path.is_file() and _observed_at(final_recovery) >= incident.closed_at:
            incident = attach_recovery_evaluation(
                incident,
                final_recovery,
                artifact_ref=_relative(post_path),
            )
    return incident


def _canonical_summary(
    *,
    incident: IncidentRecord,
    activation: SequenceConditionEvaluation,
    diagnosis: DiagnosisRun,
    recovery: dict[RecoveryState, RecoveryEvaluation],
    run_result: dict[str, Any],
    artifact_validation: dict[str, Any],
) -> dict[str, Any]:
    usable = [
        sample
        for sample in run_result["samples"]
        if sample.get("evidence_quality", {}).get("usable") is True
    ]
    peak = max(
        usable,
        key=lambda sample: float(sample["kafka"]["total_lag"]),
    )
    core = activation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
    return {
        "schema_version": "ops.incident.summary.v1",
        "classification": "verified_local_ha_incident",
        "incident_id": incident.incident_id,
        "incident_type": incident.incident_type,
        "profile": incident.profile,
        "detected_at": incident.detected_at.isoformat(),
        "closed_at": incident.closed_at.isoformat() if incident.closed_at else None,
        "lifecycle_state": incident.lifecycle_state.value,
        "outcome": incident.outcome.value if incident.outcome else None,
        "detection": {
            "condition": "CORE_BACKLOG_PRESSURE",
            "state": "PRESENT",
            "evaluation_id": activation.evaluation_id,
            "policy_version": activation.policy.policy_version,
            "evaluator_version": activation.evaluator_version,
            "ruleset_version": activation.ruleset_version,
            "matched_activation_windows": core.facts.get("matched_activation_windows", []),
            "capture_measurements": core.facts.get("capture_measurements", []),
        },
        "workload": {
            "executor": "k6_constant_arrival_rate",
            "streams": run_result["plan"]["streams"],
            "phases": run_result["workload_attainment"]["phases"],
            "peak_total_lag_records": peak["kafka"]["total_lag"],
            "peak_sequence_index": peak["sequence_index"],
            "worker_desired_replicas_at_peak": peak["worker"]["desired_replicas"],
            "worker_available_replicas_at_peak": peak["worker"]["available_replicas"],
            "keda_active_at_peak": peak["keda"]["conditions"].get("Active"),
        },
        "diagnosis": {
            "diagnosis_id": diagnosis.diagnosis_id,
            "model": diagnosis.policy.model,
            "tool_calls": [item.tool_id for item in diagnosis.steps],
            "hypotheses": [item.model_dump(mode="json") for item in diagnosis.hypotheses],
            "stop_reason": diagnosis.stop_reason.value,
            "validation": diagnosis.validation.model_dump(mode="json"),
            "output_repairs_used": diagnosis.output_repairs_used,
            "api_requests": diagnosis.usage.api_requests,
        },
        "recovery": {
            "policy_version": incident.recovery.policy_version,
            "evaluations": {
                state.value: value.recovery_evaluation_id
                for state, value in recovery.items()
            },
            "recovery_ingress_records_per_second": 75,
            "completion_capture_count": recovery[
                RecoveryState.WORKER_BACKLOG_RECOVERED
            ].recovery_completion.model_dump(mode="json"),
        },
        "provenance": incident.provenance.model_dump(mode="json"),
        "artifact_validation": artifact_validation,
        "boundaries": {
            "ai_declared_incident": False,
            "ai_declared_recovery": False,
            "ai_performed_remediation": False,
            "runtime_control_plane_writes": False,
            "workload_event_writes": True,
        },
    }


def write_canonical_incident(
    *,
    incident: IncidentRecord,
    activation: SequenceConditionEvaluation,
    diagnosis: DiagnosisRun,
    recovery: dict[RecoveryState, RecoveryEvaluation],
    run_result: dict[str, Any],
    artifact_validation: dict[str, Any],
) -> Path:
    directory = ROOT / "results" / "ops-agent" / "incidents" / incident.incident_id
    directory.mkdir(parents=True, exist_ok=False)
    incident_path = directory / "incident.json"
    _write_model(incident_path, incident)
    timeline = {
        "schema_version": "ops.incident.timeline.v1",
        "incident_id": incident.incident_id,
        "events": [item.model_dump(mode="json") for item in incident.timeline],
    }
    atomic_json(directory / "timeline.json", timeline)
    summary = _canonical_summary(
        incident=incident,
        activation=activation,
        diagnosis=diagnosis,
        recovery=recovery,
        run_result=run_result,
        artifact_validation=artifact_validation,
    )
    atomic_json(directory / "summary.json", summary)
    references = {
        "schema_version": "ops.incident.references.v1",
        "incident_id": incident.incident_id,
        "condition": incident.detection.model_dump(mode="json"),
        "diagnosis": incident.diagnosis.model_dump(mode="json") if incident.diagnosis else None,
        "recovery": [item.model_dump(mode="json") for item in incident.recovery.evaluations],
    }
    atomic_json(directory / "references.json", references)
    markdown = [
        "# Verified Worker Backlog Incident",
        "",
        f"- Incident: `{incident.incident_id}`",
        f"- Profile: `{incident.profile}`",
        f"- Detection: `CORE_BACKLOG_PRESSURE=PRESENT` (`{activation.evaluation_id}`)",
        f"- Diagnosis: `{diagnosis.diagnosis_id}` using `{diagnosis.policy.model}`",
        f"- Recovery policy: `{incident.recovery.policy_version}`",
        f"- Outcome: `{incident.outcome.value if incident.outcome else None}`",
        f"- Closed at: `{incident.closed_at.isoformat() if incident.closed_at else None}`",
        "",
        "Incident detection and recovery are deterministic. The Diagnosis Agent used only",
        "normalized read-only evidence and did not change runtime state.",
        "",
    ]
    (directory / "incident.md").write_text("\n".join(markdown), encoding="utf-8")
    reread = IncidentRecord.model_validate_json(incident_path.read_bytes())
    reread.verify_integrity()
    return directory


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run one gated, actual local-ha Worker backlog incident E2E."
    )
    result.add_argument("--context", default="kind-messaging-ha")
    result.add_argument("--medium-rate", type=int, default=75)
    result.add_argument("--overload-rate", type=int, default=330)
    result.add_argument("--streams", type=int, default=64)
    result.add_argument("--normal-phase-seconds", type=int, default=90)
    result.add_argument("--overload-seconds", type=int, default=90)
    result.add_argument("--recovery-phase-seconds", type=int, default=1800)
    result.add_argument("--capture-interval-seconds", type=int, default=15)
    result.add_argument("--pre-allocated-vus", type=int, default=100)
    result.add_argument("--max-vus", type=int, default=400)
    result.add_argument("--baseline-timeout-seconds", type=int, default=900)
    result.add_argument("--setup-timeout-seconds", type=int, default=180)
    result.add_argument(
        "--output-root",
        type=Path,
        default=Path("results/ops-agent/incident-e2e"),
    )
    return result


def main() -> int:
    args = parser().parse_args()
    if _git_text(["branch", "--show-current"]) != "dev-kafka":
        raise RuntimeError("Phase 5.1 must run from dev-kafka")
    kubectl_context = subprocess.run(
        [str(ROOT / "tools" / "kubectl.exe"), "config", "current-context"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if kubectl_context.returncode != 0 or kubectl_context.stdout.strip() != args.context:
        raise RuntimeError("kubectl current-context does not match the requested context")
    executable = k6_path()
    api_key, model = load_openai_configuration(ROOT)
    rates = {
        "IDLE": 0,
        "LOW": 30,
        "MEDIUM": args.medium_rate,
        "HIGH_SUSTAINABLE": 110,
        "OVERLOAD": args.overload_rate,
    }
    plan = build_scenario_plan(
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
    experiment_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = args.output_root if args.output_root.is_absolute() else ROOT / args.output_root
    experiment_directory = output_root / experiment_id
    experiment_directory.mkdir(parents=True, exist_ok=False)
    run_directory = experiment_directory / "run-01"
    provenance = {
        "source_sha": _git_text(["rev-parse", "HEAD"]),
        "source_tree_sha256": source_tree_sha256(),
    }
    manifest: dict[str, Any] = {
        "schema_version": "ops.incident-e2e-manifest.v1",
        "experiment_id": experiment_id,
        "started_at": utc_now(),
        "status": "RUNNING",
        "context": args.context,
        "plan": plan,
        "k6": {"version": k6_version(executable), "executor": "constant-arrival-rate"},
        "source": provenance,
        "openai_diagnosis_runs_allowed": 1,
        "openai_diagnosis_runs_started": 0,
        "manual_keda_changes": False,
        "manual_replica_changes": False,
        "runtime_control_plane_writes": False,
        "workload_event_writes": True,
    }
    atomic_json(experiment_directory / "manifest.json", manifest)
    state: dict[str, Any] = {}
    try:
        manifest["preflight"] = collect_clean_preflight(
            directory=experiment_directory / "preflight",
            context=args.context,
            capture_interval_seconds=args.capture_interval_seconds,
        )
        atomic_json(experiment_directory / "manifest.json", manifest)

        source_incident_id = f"phase5-{experiment_id}-sample"

        def incident_id_factory(sample_index: int) -> str:
            return f"{source_incident_id}-{sample_index:03d}"

        def on_capture(
            bundle_paths: tuple[Path, ...],
            samples: tuple[dict[str, Any], ...],
        ) -> None:
            if state.get("diagnosis_started") or len(bundle_paths) < 3:
                return
            activation = evaluate_bundle_sequence(
                [path.read_bytes() for path in bundle_paths[-3:]]
            )
            core = activation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
            if core.state != ConditionState.PRESENT:
                return
            activation_path = run_directory / "conditions.v2.activation.live.json"
            _write_model(activation_path, activation)
            last_bundle = EvidenceBundle.model_validate_json(bundle_paths[-1].read_bytes())
            if not last_bundle.context.desired_image or not last_bundle.context.argocd_revision:
                raise RuntimeError("activation bundle lacks runtime image or Argo provenance")
            incident = create_incident(
                activation=activation,
                provenance=IncidentProvenance(
                    source_sha=provenance["source_sha"],
                    source_tree_sha256=provenance["source_tree_sha256"],
                    runtime_image=last_bundle.context.desired_image,
                    argocd_revision=last_bundle.context.argocd_revision,
                ),
                activation_artifact_ref=_relative(activation_path),
            )
            state.update(
                {
                    "activation": activation,
                    "activation_path": activation_path,
                    "activation_indexes": [len(bundle_paths) - 3, len(bundle_paths) - 2, len(bundle_paths) - 1],
                    "incident": incident,
                    "diagnosis_started": True,
                }
            )
            _write_model(run_directory / "incident.active.json", incident)
            manifest["openai_diagnosis_runs_started"] = 1
            atomic_json(experiment_directory / "manifest.json", manifest)
            thread = threading.Thread(
                target=_diagnosis_worker,
                kwargs={
                    "state": state,
                    "activation": activation,
                    "bundle_paths": bundle_paths[-3:],
                    "output_path": run_directory / "diagnosis.json",
                    "api_key": api_key,
                    "model": model,
                },
                name="phase5-live-diagnosis",
                daemon=False,
            )
            state["diagnosis_thread"] = thread
            thread.start()

        run_result = run_scenario(
            experiment_directory=experiment_directory,
            run_name="run-01",
            plan=plan,
            context=args.context,
            executable=executable,
            baseline_timeout_seconds=args.baseline_timeout_seconds,
            setup_timeout_seconds=args.setup_timeout_seconds,
            on_capture=on_capture,
            incident_id_factory=incident_id_factory,
        )
        thread = state.get("diagnosis_thread")
        if thread is not None:
            thread.join()
        if not state.get("diagnosis_started"):
            raise RuntimeError("actual workload did not start the diagnosis activation")
        if state.get("diagnosis_error") is not None:
            manifest["diagnosis_failure"] = state["diagnosis_error"]
            raise RuntimeError("live diagnosis failed with a safely classified error")
        diagnosis = state.get("diagnosis")
        if not isinstance(diagnosis, DiagnosisRun):
            raise RuntimeError("live diagnosis did not produce a valid artifact")
        incident = state["incident"]
        activation = state["activation"]
        recovery_incident_id = incident.detection.source_incident_id
        if recovery_incident_id is None:
            raise RuntimeError("incident detection lacks logical source incident identity")
        recovery, final_recovery = _evaluate_recovery_prefixes(
            incident_id=recovery_incident_id,
            activation=activation,
            result=run_result,
            run_directory=run_directory,
            activation_end_index=state["activation_indexes"][-1],
        )
        required_states = {
            RecoveryState.WORKER_BACKLOG_ACTIVE,
            RecoveryState.WORKER_BACKLOG_RECOVERING,
            RecoveryState.WORKER_BACKLOG_RECOVERED,
        }
        if set(recovery) != required_states:
            missing = sorted(state.value for state in required_states - set(recovery))
            raise RuntimeError(f"actual recovery sequence is incomplete: {missing}")
        incident = _assemble_incident(
            incident=incident,
            diagnosis=diagnosis,
            recovery=recovery,
            final_recovery=final_recovery,
            run_directory=run_directory,
        )
        artifact_validation = validate_capture_artifacts(
            run_result["samples"],
            repository_root=ROOT,
        )
        if artifact_validation["status"] != "PASS":
            raise RuntimeError("live E2E bundle/raw artifact integrity validation failed")
        canonical_directory = write_canonical_incident(
            incident=incident,
            activation=activation,
            diagnosis=diagnosis,
            recovery=recovery,
            run_result=run_result,
            artifact_validation=artifact_validation,
        )
        manifest.update(
            {
                "completed_at": utc_now(),
                "status": "COMPLETE",
                "incident_id": incident.incident_id,
                "condition_evaluation_id": activation.evaluation_id,
                "diagnosis_id": diagnosis.diagnosis_id,
                "recovery_evaluation_ids": {
                    state.value: value.recovery_evaluation_id
                    for state, value in recovery.items()
                },
                "incident_record_sha256": incident.incident_record_sha256,
                "canonical_directory": _relative(canonical_directory),
                "artifact_validation": artifact_validation,
            }
        )
        atomic_json(experiment_directory / "manifest.json", manifest)
        print(_relative(canonical_directory))
        return 0
    except BaseException:
        manifest["completed_at"] = utc_now()
        manifest["status"] = "FAILED"
        atomic_json(experiment_directory / "manifest.json", manifest)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
