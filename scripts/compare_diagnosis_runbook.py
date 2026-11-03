"""Offline comparison of a fixed checklist with stored scripted investigation runs."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.diagnosis_quality import (  # noqa: E402
    QualityAttempt, QualityRubric, evaluation_identity, score_attempt, summarize_scores,
)
from ops_agent.diagnosis_scenarios import ControlledScenarioRegistry, load_scenario_catalog  # noqa: E402
from ops_agent.fixed_runbook import RUNBOOK_VERSION, TOOL_ORDER, run_fixed_runbook  # noqa: E402
from scripts.evaluate_diagnosis_quality import (  # noqa: E402
    DEFAULT_CATALOG, DEFAULT_REPLAY, DEFAULT_RUBRIC, evaluate, load_attempts, write_json,
)


def compare(*, replay_path: Path, catalog_path: Path, rubric_path: Path, output_dir: Path) -> int:
    catalog = load_scenario_catalog(catalog_path)
    rubric = QualityRubric.model_validate_json(rubric_path.read_bytes())
    rubric.verify_catalog(catalog)
    identity = evaluation_identity(catalog, rubric)
    source_bytes = replay_path.read_bytes()
    attempts = load_attempts(json.loads(source_bytes), replay=True, identity=identity)
    if (len(attempts) != len(rubric.cases)
        or {item.fixture_id for item in attempts} != set(rubric.cases)):
        raise ValueError("comparison requires exactly one replay for each fixture")
    for attempt in attempts:
        policy = attempt.run.get("policy", {})
        if policy.get("max_steps") != 4 or policy.get("max_tool_calls") != 4:
            raise ValueError("both arms require the same four-step/four-call budget")
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": "ops.diagnosis.runbook-comparison.v1", "status": "running",
        "classification": "SCRIPTED_RUNBOOK_COMPARISON",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_identity": identity, "scheduled_pair_count": len(attempts),
        "runbook_version": RUNBOOK_VERSION, "fixed_tool_order": list(TOOL_ORDER),
        "max_steps": 4, "max_tool_calls": 4,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "implementation_sha256": {
            str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), ROOT / "ops_agent/fixed_runbook.py")
        },
    }
    write_json(output_dir / "manifest.json", manifest)
    (output_dir / "replay-source.json").write_bytes(source_bytes)
    baseline_attempts, pairs = [], []
    for attempt in attempts:
        started = perf_counter()
        try:
            run = run_fixed_runbook(activation=catalog.activation,
                registry=ControlledScenarioRegistry(catalog=catalog, fixture_id=attempt.fixture_id))
            baseline = QualityAttempt(
                attempt_id=attempt.attempt_id, fixture_id=attempt.fixture_id,
                execution_kind="scripted", run=run.model_dump(mode="json"),
                elapsed_seconds=perf_counter() - started,
            )
        except ValueError as exc:
            baseline = QualityAttempt(
                attempt_id=attempt.attempt_id, fixture_id=attempt.fixture_id,
                execution_kind="scripted", failure_code=type(exc).__name__,
                elapsed_seconds=perf_counter() - started,
            )
        baseline_attempts.append(baseline)
        pairs.append({
            "fixture_id": attempt.fixture_id,
            "fixed_runbook": score_attempt(baseline, catalog=catalog, rubric=rubric),
            "scripted_investigation": score_attempt(attempt, catalog=catalog, rubric=rubric),
        })
    baseline_path = output_dir / "runbook-attempts.json"
    write_json(baseline_path, {
        "schema_version": "ops.diagnosis.quality-attempts.v1", "evaluation_identity": identity,
        "attempts": [item.model_dump(mode="json") for item in baseline_attempts],
    })
    # Same importer/scorer and provenance handling for both arms; each retains all failures.
    evaluate(source_path=baseline_path, replay=False, catalog_path=catalog_path,
             rubric_path=rubric_path, output_dir=output_dir / "fixed-runbook")
    evaluate(source_path=replay_path, replay=True, catalog_path=catalog_path,
             rubric_path=rubric_path, output_dir=output_dir / "scripted-investigation")
    arms = {}
    for arm in ("fixed_runbook", "scripted_investigation"):
        scores = [pair[arm] for pair in pairs]
        arms[arm] = {
            **summarize_scores(scores),
            "tool_call_count_known_attempts": sum("tool_call_count" in item for item in scores),
            "tool_call_count_total_known": sum(item.get("tool_call_count", 0) for item in scores),
            "extra_tool_call_count_total_known": sum(item.get("extra_tool_call_count", 0) for item in scores),
        }
    write_json(output_dir / "comparison.json", {"arms": arms, "pairs": pairs})
    report = [
        "# Fixed Runbook / Scripted Investigation", "",
        "Classification: `SCRIPTED_RUNBOOK_COMPARISON`", "",
        "Both arms use the same frozen observations, normalizer, allowlist, four-call limit and scorer.",
        "The checklist always requests stage -> PostgreSQL -> replica -> KEDA.",
        "The stored investigation is scripted. Live model accuracy and AI benefit remain unmeasured.",
        "Checklist elapsed time is local execution only; replay latency is unknown. No time speedup is calculated.", "",
        "| Fixture | Runbook quality | Scripted quality | Runbook calls | Scripted calls |",
        "| --- | --- | --- | --- | --- |",
    ]
    report.extend(
        f"| {pair['fixture_id']} | {pair['fixed_runbook']['quality_pass']} | {pair['scripted_investigation']['quality_pass']} | {pair['fixed_runbook'].get('tool_call_count', 'unknown')} | {pair['scripted_investigation'].get('tool_call_count', 'unknown')} |"
        for pair in pairs
    )
    (output_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    manifest.update(status="completed", completed_at=datetime.now(timezone.utc).isoformat())
    write_json(output_dir / "manifest.json", manifest)
    print(json.dumps({"output_dir": str(output_dir), "arms": arms}))
    return 0 if all(score["quality_pass"] for pair in pairs for key, score in pair.items() if key != "fixture_id") else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    return compare(replay_path=args.replay, catalog_path=args.catalog,
                   rubric_path=args.rubric, output_dir=args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
