"""Explicit opt-in, blinded 4 x 2 live pilot with durable traces and a fixed baseline."""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.diagnosis_agent import OpenAIResponsesClient, load_openai_configuration  # noqa: E402
from ops_agent.diagnosis_quality import (  # noqa: E402
    QualityAttempt, QualityRubric, evaluation_identity, score_attempt, summarize_scores,
)
from ops_agent.diagnosis_scenarios import ControlledScenarioRegistry, load_scenario_catalog  # noqa: E402
from ops_agent.diagnosis_v2_models import DiagnosisPolicyV2  # noqa: E402
from ops_agent.fixed_runbook import run_fixed_runbook  # noqa: E402
from ops_agent.scenario_agent import (  # noqa: E402
    SCENARIO_SYSTEM_INSTRUCTIONS, run_scenario_diagnosis, scenario_prompt_identity,
)
from ops_agent.scenario_contract import OUTPUT_CONTRACT, REPAIR_INSTRUCTIONS  # noqa: E402
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG, DEFAULT_RUBRIC, evaluate, write_json  # noqa: E402


BLINDING_VERSION = "opaque-scenario-id.v1"


def blind_payload(payload, *, fixture_id, alias):
    """Keep canonical evidence IDs, values and schemas; hide the cause-bearing label."""
    def visit(value):
        if isinstance(value, dict):
            if {"expected_primary_hypothesis", "branch_expectations"} & value.keys():
                raise ValueError("answer metadata is forbidden in model requests")
            return {key: visit(item) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, str):
            # History contains JSON-encoded user messages and function outputs.
            return value.replace(fixture_id, alias)
        return value
    result = visit(deepcopy(payload))
    # Inspect encoded history too; it must never carry rubric/branch answers.
    serialized = json.dumps(result)
    if "expected_primary_hypothesis" in serialized or "branch_expectations" in serialized:
        raise ValueError("encoded answer metadata is forbidden")
    return result


class TracedBlindClient:
    def __init__(self, *, delegate, fixture_id, trace_dir, batch_budget):
        self.delegate = delegate
        self.fixture_id = fixture_id
        self.alias = "case-" + uuid4().hex
        self.trace_dir = trace_dir
        self.batch_budget = batch_budget
        self.records = []
        self.repair_used = 0

    def create(self, payload):
        if len(self.records) >= 6 or self.batch_budget["used"] >= 48:
            raise ValueError("pilot request budget exhausted")
        visible = blind_payload(payload, fixture_id=self.fixture_id, alias=self.alias)
        if "repair_ops_diagnosis_v2_output_contract_only" in json.dumps(visible["input"]):
            self.repair_used = 1
        self.batch_budget["used"] += 1
        record = {"request": visible, "status": "running", "repair_turn": bool(self.repair_used)}
        self.records.append(record)
        path = self.trace_dir / f"turn-{len(self.records):02d}.json"
        write_json(path, record)
        try:
            response = self.delegate.create(visible)
        except Exception as exc:
            # Only type is persisted: arbitrary exception text may contain credentials.
            record.update(status="failed", error_type=type(exc).__name__)
            write_json(path, record)
            raise
        record.update(status="completed", response=asdict(response))
        write_json(path, record)
        return response

    def usage(self):
        responses = [record["response"] for record in self.records if "response" in record]
        return {
            "input_tokens": sum(item["input_tokens"] for item in responses) if responses else None,
            "output_tokens": sum(item["output_tokens"] for item in responses) if responses else None,
            "response_count": len(responses),
            "request_count": len(self.records),
            "usage_coverage_complete": all("response" in item for item in self.records),
        }


def run_pilot(*, output_dir, model, client_factory, catalog_path=DEFAULT_CATALOG,
              rubric_path=DEFAULT_RUBRIC):
    catalog = load_scenario_catalog(catalog_path)
    rubric = QualityRubric.model_validate_json(rubric_path.read_bytes())
    rubric.verify_catalog(catalog)
    identity = evaluation_identity(catalog, rubric)
    if len(catalog.scenarios) != 4:
        raise ValueError("pilot requires exactly four scenarios")
    policy = DiagnosisPolicyV2(model=model, model_mode="live", max_retries=0,
                               max_output_repairs=1, max_steps=4, max_tool_calls=4)
    output_dir.mkdir(parents=True, exist_ok=False)
    schedule = [
        {"attempt_id": f"pilot-{repeat + 1}-{index + 1}", "fixture_id": case.fixture_id}
        for repeat in range(2) for index, case in enumerate(catalog.scenarios)
    ]
    source = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    paths = [Path(__file__), ROOT / "ops_agent/scenario_agent.py",
             ROOT / "ops_agent/scenario_contract.py",
             ROOT / "ops_agent/diagnosis_agent.py", ROOT / "ops_agent/fixed_runbook.py"]
    manifest = {
        "schema_version": "ops.diagnosis.pilot.v1", "classification": "LIVE_CONTROLLED_PILOT",
        "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
        "policy": policy.model_dump(mode="json"), "schedule": schedule,
        "blinding_version": BLINDING_VERSION, "evaluation_identity": identity,
        "prompt_identity": scenario_prompt_identity(),
        "max_api_requests": 48, "max_requests_per_attempt": 6,
        "source_commit": source.stdout.strip(), "source_status": dirty.stdout.strip(),
        "file_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
    }
    write_json(output_dir / "manifest.json", manifest)
    write_json(output_dir / "prompt-contract.json", {
        "identity": scenario_prompt_identity(),
        "system_instructions": SCENARIO_SYSTEM_INSTRUCTIONS,
        "output_contract": OUTPUT_CONTRACT,
        "repair_instructions": REPAIR_INSTRUCTIONS,
    })
    write_json(output_dir / "catalog.json", catalog.model_dump(mode="json"))
    write_json(output_dir / "rubric.json", rubric.model_dump(mode="json"))
    live_attempts, baseline_attempts, records = [], [], []
    batch_budget = {"used": 0}
    aborted = False
    for planned in schedule:
        attempt_id, fixture_id = planned["attempt_id"], planned["fixture_id"]
        trace_dir = output_dir / attempt_id
        trace_dir.mkdir()
        state = {**planned, "status": "running"}
        write_json(trace_dir / "state.json", state)
        client = TracedBlindClient(delegate=client_factory(), fixture_id=fixture_id,
                                  trace_dir=trace_dir, batch_budget=batch_budget)
        started = perf_counter()
        try:
            run = run_scenario_diagnosis(catalog=catalog, fixture_id=fixture_id,
                                        client=client, policy=policy)
            live = QualityAttempt(**planned, execution_kind="live", run=run.model_dump(mode="json"),
                                  elapsed_seconds=perf_counter() - started,
                                  responses=client.records)
        except (ValueError, RuntimeError, TimeoutError) as exc:
            usage = client.usage()
            # Runtime/transport errors stop this batch; remaining scheduled trials stay unrun.
            aborted = isinstance(exc, (RuntimeError, TimeoutError))
            live = QualityAttempt(
                **planned, execution_kind="live", failure_code=getattr(exc, "code", type(exc).__name__),
                elapsed_seconds=perf_counter() - started,
                input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                first_pass_contract=False if client.repair_used or isinstance(exc, ValueError) else None,
                repair_used=client.repair_used, responses=client.records,
            )
        live_attempts.append(live)
        baseline_started = perf_counter()
        baseline = run_fixed_runbook(activation=catalog.activation,
            registry=ControlledScenarioRegistry(catalog=catalog, fixture_id=fixture_id))
        fixed = QualityAttempt(**planned, execution_kind="scripted", run=baseline.model_dump(mode="json"),
                               elapsed_seconds=perf_counter() - baseline_started)
        baseline_attempts.append(fixed)
        score = score_attempt(live, catalog=catalog, rubric=rubric)
        fixed_score = score_attempt(fixed, catalog=catalog, rubric=rubric)
        state.update(status="completed", live_attempt=live.model_dump(mode="json"),
                     live_score=score, fixed_runbook_score=fixed_score, trace_usage=client.usage())
        write_json(trace_dir / "state.json", state)
        records.append(state)
        print(f"{attempt_id}: contract={score['contract_pass']} quality={score['quality_pass']} requests={len(client.records)} repair={client.repair_used}", flush=True)
        if aborted:
            break
    for name, attempts in (("live", live_attempts), ("fixed-runbook", baseline_attempts)):
        path = output_dir / f"{name}-attempts.json"
        write_json(path, {"schema_version": "ops.diagnosis.quality-attempts.v1",
                          "evaluation_identity": identity,
                          "attempts": [item.model_dump(mode="json") for item in attempts]})
        evaluate(source_path=path, replay=False, catalog_path=catalog_path,
                 rubric_path=rubric_path, output_dir=output_dir / name)
    summary = {
        "scheduled_attempt_count": len(schedule), "executed_attempt_count": len(live_attempts),
        "unrun_attempt_count": len(schedule) - len(live_attempts),
        "api_request_count": batch_budget["used"],
        "live": summarize_scores([item["live_score"] for item in records]),
        "fixed_runbook": summarize_scores([item["fixed_runbook_score"] for item in records]),
        "usage_coverage_complete": all(item["trace_usage"]["usage_coverage_complete"] for item in records),
    }
    write_json(output_dir / "summary.json", summary)
    lines = ["# Live controlled pilot", "", f"Model: `{model}`; blinding: `{BLINDING_VERSION}`.", "",
             "Four development scenarios, two repetitions. These results do not establish generalization.",
             "Unrun trials are reported separately. Failures remain in executed-attempt denominators.",
             "Baseline elapsed is local execution; LLM elapsed includes API/network. No time speedup is claimed.", "",
             "| Attempt | Fixture | LLM contract | LLM quality | LLM calls | Runbook calls | Repair |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for item in records:
        score, baseline = item["live_score"], item["fixed_runbook_score"]
        lines.append(f"| {item['attempt_id']} | {item['fixture_id']} | {score['contract_pass']} | {score['quality_pass']} | {score.get('tool_call_count', 'unknown')} | {baseline['tool_call_count']} | {score['repair_used']} |")
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest.update(status="aborted" if aborted else "completed", api_request_count=batch_budget["used"],
                    finished_at=datetime.now(timezone.utc).isoformat())
    write_json(output_dir / "manifest.json", manifest)
    return 2 if aborted else (0 if all(item["live_score"]["quality_pass"] for item in records) else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("explicit --live is required; eight trials can incur API costs")
    key, model = load_openai_configuration(ROOT)
    return run_pilot(output_dir=args.output_dir, model=model,
        client_factory=lambda: OpenAIResponsesClient(api_key=key, timeout_seconds=30, max_retries=0))


if __name__ == "__main__":
    raise SystemExit(main())
