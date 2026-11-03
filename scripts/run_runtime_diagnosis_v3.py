"""Prepare or explicitly run v3.5 on frozen operational evidence, never on fixtures."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.diagnosis_quality_v3 import fixed_decision_v3  # noqa: E402
from ops_agent.diagnosis_v3_validator import validate_decision_v3  # noqa: E402
from ops_agent.evaluation_models import ConditionName, ConditionState, canonical_sha256  # noqa: E402
from ops_agent.runtime_agent_v3 import run_runtime_v3  # noqa: E402
from ops_agent.runtime_evidence_v3 import FrozenRuntimeRegistry, RUNTIME_TOOLS, verify_runtime_inputs  # noqa: E402
from ops_agent.scenario_agent_v3 import execution_contract_v3  # noqa: E402
from scripts.observe_worker_backlog import write_new  # noqa: E402


class RuntimeTraceClient:
    def __init__(self, delegate, directory):
        self.delegate, self.directory, self.count = delegate, directory, 0

    def create(self, payload):
        if self.count >= 6:
            raise RuntimeError("runtime six-request budget exhausted")
        self.count += 1
        write_new(self.directory / f"request-{self.count:02d}.json", payload)
        response = self.delegate.create(payload)
        write_new(self.directory / f"response-{self.count:02d}.json", asdict(response))
        return response


def run_runtime_pair(*, bundles, evaluation, output_dir, live=False, client=None, model=None):
    parsed, replay = verify_runtime_inputs(bundles, evaluation)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    paths = sorted((ROOT / "ops_agent").glob("*.py")) + [Path(__file__)]
    write_new(output_dir / "plan.json", {"schema_version": "ops.diagnosis.runtime-plan.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "execution_identity": execution_contract_v3()["identity"],
        "max_api_requests": 6 if live else 0, "max_tools": 4, "max_repairs": 1,
        "requested_live": live, "sequence_evaluation_id": replay.evaluation_id,
        "source_file_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}})
    write_new(output_dir / "conditions.json", replay.model_dump(mode="json"))
    for index, bundle in enumerate(parsed):
        write_new(output_dir / "bundles" / f"sample-{index + 1:03d}.json", bundle.model_dump(mode="json"))
    core = replay.conditions[ConditionName.CORE_BACKLOG_PRESSURE]
    report = {"schema_version": "ops.diagnosis.runtime-pair.v1", "acquisition_mode": "FROZEN_PROJECTED",
        "activation_state": core.state.value, "sequence_evaluation_id": replay.evaluation_id,
        "source_bundle_digests": [item.source_bundle_sha256 for item in replay.source_bundles],
        "adapter_status": "IMPLEMENTED", "model_status": "NOT_RUN", "model": None,
        "client_request_count": 0, "fixed_runbook_status": "NOT_RUN", "comparison": "NOT_EVALUATED",
        "causal_truth_verified": False, "freshness_basis": "FROZEN_COLLECTION_TIME"}
    if core.state != ConditionState.PRESENT:
        report["status"] = "SKIPPED_NO_ACTIVATION"
        write_new(output_dir / "report.json", report)
        return report
    registry = FrozenRuntimeRegistry(bundles=parsed, evaluation=replay)
    activation = registry.activation
    evidence = [registry.execute(tool) for tool in RUNTIME_TOOLS]
    fixed = fixed_decision_v3(activation=activation, evidence=evidence)
    validate_decision_v3(fixed, initial_evidence_ids=activation["evidence_ids"], acquired_evidence=evidence)
    write_new(output_dir / "activation.json", activation)
    write_new(output_dir / "projection.json", [item.model_dump(mode="json") for item in evidence])
    write_new(output_dir / "fixed-runbook.json", fixed.model_dump(mode="json"))
    report.update(status="PREPARED", fixed_runbook_status="CONTRACT_VALID",
        projection_bundle_sha256=activation["projection_bundle_sha256"],
        projection_sequence_index=activation["projection_sequence_index"],
        evidence_gaps=["RUNTIME_STAGE_BASELINE_UNAVAILABLE"])
    if not live and client is None:
        write_new(output_dir / "report.json", report)
        return report
    trace = None
    try:
        if client is None:
            from ops_agent.diagnosis_agent import OpenAIResponsesClient, load_openai_configuration
            key, model = load_openai_configuration(ROOT)
            client = OpenAIResponsesClient(api_key=key, timeout_seconds=30, max_retries=0)
        report.update(model=model, model_status="RUNNING", execution_mode="LIVE_API" if live else "SCRIPTED_TEST")
        write_new(output_dir / "model-plan.json", {"model": model, "execution_mode": report["execution_mode"],
            "activation_sha256": canonical_sha256(activation), "transport_retries": 0})
        trace = RuntimeTraceClient(client, output_dir / "wire")
        run = run_runtime_v3(registry=FrozenRuntimeRegistry(bundles=parsed, evaluation=replay), client=trace, model=model)
        write_new(output_dir / "model-run.json", run)
        # Re-acquire canonically before accepting saved citations from the model arm.
        verification = FrozenRuntimeRegistry(bundles=parsed, evaluation=replay)
        canonical = [verification.execute(tool).model_dump(mode="json") for tool in run["tool_calls"]]
        if canonical != run["acquired_evidence"]:
            raise ValueError("runtime canonical acquisition mismatch")
        report.update(status="COMPLETED" if run["contract_pass"] else "VALIDATION_FAILED",
            client_request_count=trace.count, model_status="CONTRACT_VALID" if run["contract_pass"] else "INVALID",
            first_pass_contract=run["first_pass_contract"], repair_used=run["repair_used"])
        if run["contract_pass"]:
            def dimensions(value):
                return {"observations": sorted(item["signal"] for item in value["observations"]),
                    "supported_candidates": sorted(item["hypothesis"] for item in value["hypotheses"]
                        if item["support_status"] == "SUPPORTED"), "conclusion": value["conclusion"]}
            report["comparison"] = {"model": dimensions(run["decision"]),
                "fixed_runbook": dimensions(fixed.model_dump(mode="json")), "interpretation": "AGREEMENT_ONLY"}
    except Exception as exc:
        report.update(status="FAILED", model_status="FAILED", error_type=type(exc).__name__,
            client_request_count=trace.count if trace else 0)
        if getattr(exc, "partial_run", None) is not None:
            write_new(output_dir / "partial-run.json", exc.partial_run)
        write_new(output_dir / "report.json", report)
        raise
    write_new(output_dir / "report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, nargs="+", required=True)
    parser.add_argument("--conditions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="explicit API opt-in; requires verified PRESENT")
    args = parser.parse_args(argv)
    def read(path):
        if path.is_symlink() or not path.is_file():
            raise ValueError("runtime input must be a regular non-symlink file")
        with path.open("rb") as handle:
            raw = handle.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("runtime input exceeds 16 MiB")
        return json.loads(raw)
    if len(args.input) > 256 or sum(path.stat().st_size for path in args.input) > 64 * 1024 * 1024:
        raise ValueError("runtime sequence exceeds input budget")
    report = run_runtime_pair(bundles=[read(path) for path in args.input], evaluation=read(args.conditions),
        output_dir=args.output_dir, live=args.live)
    print(json.dumps({key: report[key] for key in ("status", "activation_state", "model_status", "client_request_count")}))
    return 0 if report["status"] not in {"FAILED", "VALIDATION_FAILED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
