from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Sequence

from ops_agent.controller import collect_bundle
from ops_agent.diagnosis_agent import (
    DiagnosisOutputContractFailure,
    OpenAIResponsesClient,
    load_openai_configuration,
    run_diagnosis,
)
from ops_agent.diagnosis_models import DiagnosisPolicy
from ops_agent.evaluator import evaluate_bundle
from ops_agent.incident_lifecycle import (
    attach_diagnosis,
    attach_recovery_evaluation,
    create_incident,
)
from ops_agent.incident_models import IncidentProvenance, IncidentRecord
from ops_agent.models import EvidenceBundle
from ops_agent.policies import load_policy
from ops_agent.recovery_evaluator import evaluate_recovery
from ops_agent.recovery_policies import load_recovery_policy
from ops_agent.sequence_evaluator import evaluate_bundle_sequence
from ops_agent.sequence_models import SequenceConditionEvaluation


_MAX_EVIDENCE_INPUT_BYTES = 16 * 1024 * 1024
_MAX_SEQUENCE_INPUT_BYTES = 64 * 1024 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ops_agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect = subparsers.add_parser("collect", help="collect normalized read-only evidence")
    collect.add_argument("--profile", default="local-ha")
    collect.add_argument("--incident-id")
    collect.add_argument("--output", type=Path)
    collect.add_argument("--artifact-dir", type=Path)
    collect.add_argument("--application-url")
    collect.add_argument("--prometheus-url")
    collect.add_argument("--context")
    collect.add_argument("--kubectl")
    evaluate = subparsers.add_parser(
        "evaluate",
        help="evaluate deterministic conditions from an evidence bundle",
    )
    evaluate.add_argument("--input", type=Path, required=True)
    evaluate.add_argument("--output", type=Path)
    evaluate_sequence = subparsers.add_parser(
        "evaluate-sequence",
        help="evaluate deterministic conditions from ordered evidence bundles",
    )
    evaluate_sequence.add_argument(
        "--input",
        type=Path,
        nargs="+",
        required=True,
        help="ordered ops.evidence.v1 bundle paths",
    )
    evaluate_sequence.add_argument("--output", type=Path)
    evaluate_recovery_parser = subparsers.add_parser(
        "evaluate-recovery",
        help="evaluate deterministic Worker backlog recovery from frozen evidence",
    )
    evaluate_recovery_parser.add_argument(
        "--activation",
        type=Path,
        required=True,
        help="CORE_BACKLOG_PRESSURE=PRESENT ops.conditions.v2 artifact",
    )
    evaluate_recovery_parser.add_argument(
        "--input",
        type=Path,
        nargs="+",
        required=True,
        help="ordered post-activation ops.evidence.v1 bundle paths",
    )
    evaluate_recovery_parser.add_argument(
        "--source-digest",
        nargs="+",
        required=True,
        help="ordered canonical SHA-256 values matching --input",
    )
    evaluate_recovery_parser.add_argument("--incident-id", required=True)
    evaluate_recovery_parser.add_argument("--profile", default="local-ha")
    evaluate_recovery_parser.add_argument(
        "--policy-version",
        choices=("v1", "v2"),
        default="v1",
        help="versioned recovery policy; v1 remains the compatibility default",
    )
    evaluate_recovery_parser.add_argument("--output", type=Path)
    diagnose = subparsers.add_parser(
        "diagnose",
        help="run the bounded Evidence-grounded Diagnosis Agent",
    )
    diagnose.add_argument(
        "--conditions",
        type=Path,
        required=True,
        help="ops.conditions.v2 input",
    )
    diagnose.add_argument(
        "--input",
        type=Path,
        nargs="+",
        required=True,
        help="ordered ops.evidence.v1 bundle paths",
    )
    diagnose.add_argument("--output", type=Path, required=True)
    diagnose.add_argument(
        "--live",
        action="store_true",
        help="explicitly opt in to one bounded OpenAI Responses API diagnosis",
    )
    build_incident = subparsers.add_parser(
        "build-incident",
        help="build an ops.incident.v1 artifact from a frozen PRESENT activation",
    )
    build_incident.add_argument("--activation", type=Path, required=True)
    build_incident.add_argument("--source-sha", required=True)
    build_incident.add_argument("--source-tree-sha256", required=True)
    build_incident.add_argument("--runtime-image", required=True)
    build_incident.add_argument("--argocd-revision", required=True)
    build_incident.add_argument("--output", type=Path, required=True)
    update_incident = subparsers.add_parser(
        "update-incident",
        help="attach immutable diagnosis/recovery artifacts to an incident",
    )
    update_incident.add_argument("--input", type=Path, required=True)
    update_incident.add_argument("--diagnosis", type=Path)
    update_incident.add_argument(
        "--recovery",
        type=Path,
        action="append",
        default=[],
    )
    update_incident.add_argument("--output", type=Path, required=True)
    return parser


def _default_incident_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"worker-backlog-{timestamp}"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_collect(args: argparse.Namespace) -> int:
    policy = deepcopy(load_policy(args.profile))
    if args.application_url:
        policy["application"]["base_url"] = args.application_url
    if args.prometheus_url:
        policy["prometheus"]["base_url"] = args.prometheus_url
    artifact_root = args.artifact_dir
    if artifact_root is None:
        artifact_root = (
            args.output.parent / "raw"
            if args.output is not None
            else Path("results/ops-agent/raw")
        )
    bundle = collect_bundle(
        policy=policy,
        incident_id=args.incident_id or _default_incident_id(),
        artifact_root=artifact_root,
        application_url=args.application_url,
        prometheus_url=args.prometheus_url,
        cluster_context=args.context,
        kubectl_path=args.kubectl,
    )
    payload = json.dumps(bundle.model_dump(mode="json"), indent=2, ensure_ascii=True) + "\n"
    if args.output is not None:
        _atomic_write(args.output, payload)
        sys.stdout.write(f"{args.output.as_posix()}\n")
    else:
        sys.stdout.write(payload)
    return 0


def _read_evidence_input(path: Path) -> bytes:
    with path.open("rb") as source:
        payload = source.read(_MAX_EVIDENCE_INPUT_BYTES + 1)
    if len(payload) > _MAX_EVIDENCE_INPUT_BYTES:
        raise ValueError(
            "evidence bundle exceeds the 16 MiB local CLI input limit"
        )
    return payload


def run_evaluate(args: argparse.Namespace) -> int:
    if (
        args.output is not None
        and args.input.resolve() == args.output.resolve()
    ):
        raise ValueError("evaluation output must not overwrite its evidence input")
    evaluation = evaluate_bundle(_read_evidence_input(args.input))
    payload = (
        json.dumps(
            evaluation.model_dump(mode="json"),
            indent=2,
            ensure_ascii=True,
        )
        + "\n"
    )
    if args.output is not None:
        _atomic_write(args.output, payload)
        sys.stdout.write(f"{args.output.as_posix()}\n")
    else:
        sys.stdout.write(payload)
    return 0


def run_evaluate_sequence(args: argparse.Namespace) -> int:
    if len(args.input) > 256:
        raise ValueError("sequence evaluation accepts at most 256 inputs")
    if args.output is not None:
        output = args.output.resolve()
        if any(path.resolve() == output for path in args.input):
            raise ValueError(
                "sequence evaluation output must not overwrite an evidence input"
            )
    payloads: list[bytes] = []
    total_bytes = 0
    for path in args.input:
        payload = _read_evidence_input(path)
        total_bytes += len(payload)
        if total_bytes > _MAX_SEQUENCE_INPUT_BYTES:
            raise ValueError(
                "evidence sequence exceeds the 64 MiB local CLI input limit"
            )
        payloads.append(payload)
    evaluation = evaluate_bundle_sequence(payloads)
    payload = (
        json.dumps(
            evaluation.model_dump(mode="json"),
            indent=2,
            ensure_ascii=True,
        )
        + "\n"
    )
    if args.output is not None:
        _atomic_write(args.output, payload)
        sys.stdout.write(f"{args.output.as_posix()}\n")
    else:
        sys.stdout.write(payload)
    return 0


def run_evaluate_recovery(args: argparse.Namespace) -> int:
    if len(args.input) > 256:
        raise ValueError("recovery evaluation accepts at most 256 inputs")
    if len(args.source_digest) != len(args.input):
        raise ValueError("one --source-digest is required per --input")
    if args.output is not None:
        output = args.output.resolve()
        if args.activation.resolve() == output or any(
            path.resolve() == output for path in args.input
        ):
            raise ValueError("recovery output must not overwrite an input")
    activation_payload = _read_evidence_input(args.activation)
    payloads: list[bytes] = []
    total_bytes = len(activation_payload)
    for path in args.input:
        payload = _read_evidence_input(path)
        total_bytes += len(payload)
        if total_bytes > _MAX_SEQUENCE_INPUT_BYTES:
            raise ValueError("recovery inputs exceed the 64 MiB local CLI limit")
        payloads.append(payload)
    evaluation = evaluate_recovery(
        incident_id=args.incident_id,
        activation_evaluation=activation_payload,
        bundles=payloads,
        source_bundle_digests=args.source_digest,
        policy=load_recovery_policy(args.profile, args.policy_version),
    )
    payload = (
        json.dumps(
            evaluation.model_dump(mode="json"),
            indent=2,
            ensure_ascii=True,
        )
        + "\n"
    )
    if args.output is not None:
        _atomic_write(args.output, payload)
        sys.stdout.write(f"{args.output.as_posix()}\n")
    else:
        sys.stdout.write(payload)
    return 0


def run_diagnose(args: argparse.Namespace) -> int:
    if not args.live:
        raise ValueError("diagnose requires explicit --live API opt-in")
    output = args.output.resolve()
    inputs = [args.conditions, *args.input]
    if any(path.resolve() == output for path in inputs):
        raise ValueError("diagnosis output must not overwrite an input")
    if len(args.input) > 256:
        raise ValueError("diagnosis accepts at most 256 ordered evidence inputs")
    condition_payload = _read_evidence_input(args.conditions)
    condition_evaluation = SequenceConditionEvaluation.model_validate_json(
        condition_payload
    )
    bundles: list[EvidenceBundle] = []
    total_bytes = len(condition_payload)
    for path in args.input:
        payload = _read_evidence_input(path)
        total_bytes += len(payload)
        if total_bytes > _MAX_SEQUENCE_INPUT_BYTES:
            raise ValueError("diagnosis inputs exceed the 64 MiB local CLI limit")
        bundles.append(EvidenceBundle.model_validate_json(payload))
    api_key, model = load_openai_configuration(Path.cwd())
    policy = DiagnosisPolicy(model=model)
    client = OpenAIResponsesClient(
        api_key=api_key,
        timeout_seconds=policy.request_timeout_seconds,
        max_retries=policy.max_retries,
    )
    try:
        diagnosis = run_diagnosis(
            bundles=bundles,
            condition_evaluation=condition_evaluation,
            client=client,
            policy=policy,
        )
    except DiagnosisOutputContractFailure as exc:
        safe_error = {
            "status": "FAILED",
            "classification": exc.classification,
            "initial_validation_error": exc.initial_error["code"],
            "final_validation_error": (
                exc.final_error["code"] if exc.final_error is not None else None
            ),
        }
        sys.stderr.write(json.dumps(safe_error, ensure_ascii=True) + "\n")
        return 1
    payload = (
        json.dumps(diagnosis.model_dump(mode="json"), indent=2, ensure_ascii=True)
        + "\n"
    )
    _atomic_write(args.output, payload)
    sys.stdout.write(f"{args.output.as_posix()}\n")
    return 0


def _repository_relative_reference(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("incident artifact inputs must remain inside the repository") from exc


def _incident_payload(record: IncidentRecord) -> str:
    return json.dumps(
        record.model_dump(mode="json"),
        indent=2,
        ensure_ascii=True,
    ) + "\n"


def run_build_incident(args: argparse.Namespace) -> int:
    if args.activation.resolve() == args.output.resolve():
        raise ValueError("incident output must not overwrite its activation input")
    activation_payload = _read_evidence_input(args.activation)
    provenance = IncidentProvenance(
        source_sha=args.source_sha,
        source_tree_sha256=args.source_tree_sha256,
        runtime_image=args.runtime_image,
        argocd_revision=args.argocd_revision,
    )
    incident = create_incident(
        activation=activation_payload,
        provenance=provenance,
        activation_artifact_ref=_repository_relative_reference(args.activation),
    )
    _atomic_write(args.output, _incident_payload(incident))
    sys.stdout.write(f"{args.output.as_posix()}\n")
    return 0


def run_update_incident(args: argparse.Namespace) -> int:
    if args.diagnosis is None and not args.recovery:
        raise ValueError("incident update requires diagnosis or recovery input")
    output = args.output.resolve()
    inputs = [args.input]
    if args.diagnosis is not None:
        inputs.append(args.diagnosis)
    inputs.extend(args.recovery)
    if any(path.resolve() == output for path in inputs):
        raise ValueError("incident output must not overwrite an input artifact")
    incident = IncidentRecord.model_validate_json(_read_evidence_input(args.input))
    if args.diagnosis is not None:
        incident = attach_diagnosis(
            incident,
            _read_evidence_input(args.diagnosis),
            artifact_ref=_repository_relative_reference(args.diagnosis),
        )
    for recovery_path in args.recovery:
        incident = attach_recovery_evaluation(
            incident,
            _read_evidence_input(recovery_path),
            artifact_ref=_repository_relative_reference(recovery_path),
        )
    _atomic_write(args.output, _incident_payload(incident))
    sys.stdout.write(f"{args.output.as_posix()}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "collect":
        return run_collect(args)
    if args.command == "evaluate":
        return run_evaluate(args)
    if args.command == "evaluate-sequence":
        return run_evaluate_sequence(args)
    if args.command == "evaluate-recovery":
        return run_evaluate_recovery(args)
    if args.command == "diagnose":
        return run_diagnose(args)
    if args.command == "build-incident":
        return run_build_incident(args)
    if args.command == "update-incident":
        return run_update_incident(args)
    raise AssertionError(f"unsupported command: {args.command}")
