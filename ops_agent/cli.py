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
from ops_agent.models import EvidenceBundle
from ops_agent.policies import load_policy
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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "collect":
        return run_collect(args)
    if args.command == "evaluate":
        return run_evaluate(args)
    if args.command == "evaluate-sequence":
        return run_evaluate_sequence(args)
    if args.command == "diagnose":
        return run_diagnose(args)
    raise AssertionError(f"unsupported command: {args.command}")
