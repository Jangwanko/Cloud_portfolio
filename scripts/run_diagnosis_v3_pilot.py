"""Explicit live v3 pilot: preregistered 4 x 2 trials, max48 requests, separate rubric."""

from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.diagnosis_agent import OpenAIResponsesClient, load_openai_configuration  # noqa: E402
from ops_agent.diagnosis_quality_v3 import fixed_run_v3, score_v3, summarize_v3  # noqa: E402
from ops_agent.diagnosis_scenarios import load_scenario_catalog  # noqa: E402
from ops_agent.evaluation_models import canonical_sha256  # noqa: E402
from ops_agent.scenario_agent_v3 import REPAIR_TASK, execution_contract_v3, run_scenario_v3  # noqa: E402
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG, write_json  # noqa: E402
from scripts.run_diagnosis_pilot import BLINDING_VERSION, TracedBlindClient  # noqa: E402

DEFAULT_RUBRIC_V3 = ROOT / "ops_agent/fixtures/diagnosis/quality_rubric_v3.json"
DEVELOPMENT_LIMIT = "New v3 task and joint score; not comparable to v2 quality scores. Four reused development fixtures; no generalization claim."
TRANSFER_LIMIT = "Frozen v3 execution on four author-created synthetic combinations. Expected sets fixed before model calls; no result-driven tuning or population generalization claim."


class TracedBlindClientV3(TracedBlindClient):
    def create(self, payload):
        if REPAIR_TASK in json.dumps(payload["input"]):
            self.repair_used = 1
        return super().create(payload)


def summarize_records_v3(records, request_count, comparison_limit=DEVELOPMENT_LIMIT):
    return {"schema_version": "ops.diagnosis.pilot-summary.v3.1",
        "scheduled_attempt_count": 8, "executed_attempt_count": len(records), "unrun_attempt_count": 8 - len(records),
        "api_request_count": request_count, "live": summarize_v3([item["live_score"] for item in records]),
        "fixed_runbook": summarize_v3([item["fixed_runbook_score"] for item in records]),
        "elapsed_seconds": sum(item["elapsed_seconds"] for item in records),
        "input_tokens": sum(item["trace_usage"]["input_tokens"] or 0 for item in records),
        "output_tokens": sum(item["trace_usage"]["output_tokens"] or 0 for item in records),
        "usage_coverage_complete": all(item["trace_usage"]["usage_coverage_complete"] for item in records),
        "comparison_limit": comparison_limit}


def export_compact_v3(*, source_dir, output_path):
    """Revalidate saved scoring and source identity, then export a new compact record."""
    if output_path.exists():
        raise FileExistsError(output_path)
    def read(name):
        return json.loads((source_dir / name).read_bytes())
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    manifest, summary = read("manifest.json"), read("summary.json")
    if manifest["status"] not in {"completed", "aborted"}:
        raise ValueError("incomplete v3 pilot")
    for path, expected in manifest["file_sha256"].items():
        if digest(ROOT / path) != expected:
            raise ValueError("v3 source identity changed")
    from ops_agent.diagnosis_scenarios import ScenarioCatalog
    catalog = ScenarioCatalog.model_validate(read("catalog.json"))
    rubric = read("rubric.json")
    if (canonical_sha256(catalog.model_dump(mode="json")) != manifest["catalog_sha256"]
            or canonical_sha256(rubric) != manifest["rubric_sha256"]
            or read("execution-contract.json") != execution_contract_v3()
            or manifest["execution_identity"] != execution_contract_v3()["identity"]):
        raise ValueError("v3 snapshot identity mismatch")
    records, compact = [], []
    for planned in manifest["schedule"][:summary["executed_attempt_count"]]:
        state_path = source_dir / planned["attempt_id"] / "state.json"
        state = json.loads(state_path.read_bytes())
        if any(state[key] != value for key, value in planned.items()):
            raise ValueError("v3 schedule mismatch")
        for run_key, score_key in (("run", "live_score"), ("fixed_runbook_run", "fixed_runbook_score")):
            if score_v3(state[run_key], catalog=catalog, rubric=rubric) != state[score_key]:
                raise ValueError("v3 saved score mismatch")
        records.append(state)
        compact.append({**planned, "state_sha256": digest(state_path),
            "live_score": state["live_score"], "fixed_runbook_score": state["fixed_runbook_score"],
            "tool_calls": state["run"].get("tool_calls", []), "decision": state["run"].get("decision"),
            "trace_usage": state["trace_usage"], "elapsed_seconds": state["elapsed_seconds"],
            "turn_sha256": {path.name: digest(path) for path in sorted(state_path.parent.glob("turn-*.json"))}})
    if (sum(item["trace_usage"]["request_count"] for item in records) != manifest["api_request_count"]
            or summarize_records_v3(records, manifest["api_request_count"], manifest["comparison_limit"]) != summary):
        raise ValueError("v3 summary mismatch")
    write_json(output_path, {"classification": "LIVE_CONTROLLED_DEVELOPMENT_PILOT",
        "schema_version": "ops.diagnosis.compact.v3.1", "source_directory": str(source_dir.relative_to(ROOT)) if source_dir.is_relative_to(ROOT) else str(source_dir),
        "source_sha256": {name: digest(source_dir / name) for name in ("manifest.json", "summary.json", "rubric.json", "catalog.json", "execution-contract.json")},
        "model": manifest["model"], "execution_identity": manifest["execution_identity"],
        "rubric_sha256": manifest["rubric_sha256"], "summary": summary, "attempts": compact})


def run_pilot_v3(*, output_dir, model, client_factory, catalog_path=DEFAULT_CATALOG,
                 rubric_path=DEFAULT_RUBRIC_V3):
    catalog_path, rubric_path = catalog_path.resolve(), rubric_path.resolve()
    catalog = load_scenario_catalog(catalog_path)
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    if len(catalog.scenarios) != 4 or set(rubric["cases"]) != {item.fixture_id for item in catalog.scenarios}:
        raise ValueError("v3 pilot requires exactly four matched cases")
    output_dir.mkdir(parents=True, exist_ok=False)
    schedule = [{"attempt_id": f"pilot-{repeat + 1}-{index + 1}", "fixture_id": case.fixture_id}
                for repeat in range(2) for index, case in enumerate(catalog.scenarios)]
    paths = [Path(__file__), ROOT / "ops_agent/scenario_agent_v3.py", ROOT / "ops_agent/diagnosis_quality_v3.py",
        ROOT / "ops_agent/diagnosis_v3_models.py", ROOT / "ops_agent/diagnosis_v3_validator.py",
        ROOT / "ops_agent/diagnosis_repair_v3.py",
        ROOT / "ops_agent/investigation_gate_v3.py",
        ROOT / "ops_agent/citation_selection_v3.py",
        ROOT / "ops_agent/diagnosis_v3_contract.py", ROOT / "ops_agent/diagnosis_agent.py",
        ROOT / "ops_agent/diagnosis_scenarios.py", ROOT / "ops_agent/fixed_runbook.py",
        ROOT / "scripts/run_diagnosis_pilot.py", catalog_path, rubric_path]
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    manifest = {"schema_version": "ops.diagnosis.pilot.v3.1", "classification": "LIVE_CONTROLLED_DEVELOPMENT_PILOT",
        "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
        "model": model, "reasoning_effort": "low", "max_output_tokens": 1600,
        "max_tools": 4, "max_repairs": 1, "max_api_requests": 48, "max_requests_per_attempt": 6,
        "max_retries": 0, "timeout_seconds": 30, "schedule": schedule,
        "blinding_version": BLINDING_VERSION, "execution_identity": execution_contract_v3()["identity"],
        "catalog_sha256": canonical_sha256(catalog.model_dump(mode="json")),
        "rubric_sha256": canonical_sha256(rubric), "source_commit": git("rev-parse", "HEAD"),
        "source_status": git("status", "--porcelain"),
        "file_sha256": {(path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        "comparison_limit": DEVELOPMENT_LIMIT if catalog_path == DEFAULT_CATALOG.resolve() else TRANSFER_LIMIT}
    # Persist criteria and schedule before the first paid request.
    for name, value in (("manifest", manifest), ("execution-contract", execution_contract_v3()),
                        ("catalog", catalog.model_dump(mode="json")), ("rubric", rubric)):
        write_json(output_dir / f"{name}.json", value)
    records, batch_budget, aborted = [], {"used": 0}, False
    for planned in schedule:
        trace_dir = output_dir / planned["attempt_id"]
        trace_dir.mkdir()
        state = {**planned, "status": "running"}
        write_json(trace_dir / "state.json", state)
        client = TracedBlindClientV3(delegate=client_factory(), fixture_id=planned["fixture_id"],
            trace_dir=trace_dir, batch_budget=batch_budget)
        started = perf_counter()
        try:
            run = run_scenario_v3(catalog=catalog, fixture_id=planned["fixture_id"], client=client, model=model)
        except (RuntimeError, TimeoutError) as exc:
            aborted = True
            run = getattr(exc, "partial_run", {"fixture_id": planned["fixture_id"], "tool_calls": []})
            run.update(contract_pass=False, failure_code=type(exc).__name__)
        elapsed = perf_counter() - started
        fixed = fixed_run_v3(catalog=catalog, fixture_id=planned["fixture_id"])
        state.update(status="transport_failed" if aborted else "completed", run=run,
            live_score=score_v3(run, catalog=catalog, rubric=rubric),
            fixed_runbook_score=score_v3(fixed, catalog=catalog, rubric=rubric),
            fixed_runbook_run=fixed, elapsed_seconds=elapsed, trace_usage=client.usage())
        write_json(trace_dir / "state.json", state)
        records.append(state)
        score = state["live_score"]
        print(f"{planned['attempt_id']}: contract={score['contract_pass']} observations={score['observations_pass']} causes={score['causes_pass']} completeness={score['completeness_pass']} requests={len(client.records)} repair={client.repair_used}", flush=True)
        if aborted:
            break
    summary = summarize_records_v3(records, batch_budget["used"], manifest["comparison_limit"])
    write_json(output_dir / "summary.json", summary)
    manifest.update(status="aborted" if aborted else "completed", api_request_count=batch_budget["used"],
                    finished_at=datetime.now(timezone.utc).isoformat())
    write_json(output_dir / "manifest.json", manifest)
    return 2 if aborted else (0 if summary["live"]["joint_pass"] == len(records) else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC_V3)
    args = parser.parse_args()
    if not args.live:
        parser.error("explicit --live is required; eight trials incur API costs")
    key, model = load_openai_configuration(ROOT)
    return run_pilot_v3(output_dir=args.output_dir, model=model,
        catalog_path=args.catalog, rubric_path=args.rubric,
        client_factory=lambda: OpenAIResponsesClient(api_key=key, timeout_seconds=30, max_retries=0))


if __name__ == "__main__":
    raise SystemExit(main())
