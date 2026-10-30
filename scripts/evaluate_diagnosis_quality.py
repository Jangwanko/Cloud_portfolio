"""Score saved diagnosis artifacts offline; never load credentials or call a model."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.diagnosis_quality import (  # noqa: E402
    QualityAttempt, QualityRubric, evaluation_identity, score_attempt, summarize_scores,
)
from ops_agent.diagnosis_scenarios import load_scenario_catalog  # noqa: E402

DEFAULT_CATALOG = ROOT / "ops_agent/fixtures/diagnosis/scenario_lab_v1.json"
DEFAULT_RUBRIC = ROOT / "ops_agent/fixtures/diagnosis/quality_rubric_v1.json"
DEFAULT_REPLAY = ROOT / "scenario_lab/scenario-lab-replay.json"


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_attempts(source: dict, *, replay: bool, identity: dict) -> list[QualityAttempt]:
    if replay:
        if (
            source.get("schema_version") != "demo.scenario-lab-replay.v1"
            or source.get("classification") != "CONTROLLED_SCENARIO_REPLAY"
            or source.get("catalog_digest") != identity["catalog_sha256"]
        ):
            raise ValueError("expected a recorded replay bound to the current catalog")
        return [
            QualityAttempt(
                attempt_id=f"replay-{index + 1:03d}",
                fixture_id=item["fixture_id"], execution_kind="scripted", run=item["run"],
            )
            for index, item in enumerate(source["scenarios"])
        ]
    if source.get("schema_version") != "ops.diagnosis.quality-attempts.v1":
        raise ValueError("unknown attempt batch schema")
    if source.get("evaluation_identity") != identity:
        raise ValueError("attempt batch does not match the catalog/rubric/scorer version")
    return [QualityAttempt.model_validate(item) for item in source["attempts"]]


def evaluate(*, source_path: Path, replay: bool, catalog_path: Path,
             rubric_path: Path, output_dir: Path) -> int:
    catalog = load_scenario_catalog(catalog_path)
    rubric = QualityRubric.model_validate_json(rubric_path.read_bytes())
    rubric.verify_catalog(catalog)
    identity = evaluation_identity(catalog, rubric)
    raw = source_path.read_bytes()
    attempts = load_attempts(json.loads(raw), replay=replay, identity=identity)
    if not attempts or len({item.attempt_id for item in attempts}) != len(attempts):
        raise ValueError("batch must contain unique, nonempty attempt IDs")
    if len({item.execution_kind for item in attempts}) != 1:
        raise ValueError("scripted and live attempts require separate batches")
    if any(item.fixture_id not in rubric.cases for item in attempts):
        raise ValueError("attempt references an unknown fixture")
    if replay and (
        len(attempts) != len(rubric.cases)
        or {item.fixture_id for item in attempts} != set(rubric.cases)
    ):
        raise ValueError("replay must cover each catalog fixture exactly once")

    # A new directory prevents replacing an earlier evaluation's evidence.
    output_dir.mkdir(parents=True, exist_ok=False)
    provenance = {}
    for label, args in (
        ("source_commit", ["git", "rev-parse", "HEAD"]),
        ("source_status", ["git", "status", "--porcelain"]),
    ):
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)
        provenance[label] = result.stdout.strip() if result.returncode == 0 else None
    scorer_paths = [Path(__file__), ROOT / "ops_agent/diagnosis_quality.py",
                    ROOT / "ops_agent/diagnosis_scenarios.py",
                    ROOT / "ops_agent/diagnosis_v2_models.py",
                    ROOT / "ops_agent/diagnosis_v2_validator.py",
                    ROOT / "ops_agent/diagnosis_models.py",
                    ROOT / "ops_agent/evaluation_models.py",
                    ROOT / "ops_agent/diagnosis_tools.py",
                    ROOT / "ops_agent/diagnosis_validator.py",
                    ROOT / "ops_agent/models.py"]
    provenance["file_sha256"] = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in scorer_paths
    }
    manifest = {
        "schema_version": "ops.diagnosis.quality-evaluation.v1",
        "status": "running",
        "classification": "SCRIPTED_SCORER_CHECK" if attempts[0].execution_kind == "scripted" else "SAVED_LIVE_ARTIFACT_EVALUATION",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_identity": identity,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "scheduled_attempt_count": len(attempts),
        "provenance": provenance,
    }
    write_json(output_dir / "manifest.json", manifest)
    (output_dir / "source.json").write_bytes(raw)
    write_json(output_dir / "catalog.json", catalog.model_dump(mode="json"))
    write_json(output_dir / "rubric.json", rubric.model_dump(mode="json"))
    scores = []
    for index, attempt in enumerate(attempts, 1):
        score = score_attempt(attempt, catalog=catalog, rubric=rubric)
        write_json(output_dir / f"attempt-{index:03d}.json", {
            "attempt": attempt.model_dump(mode="json"), "score": score,
        })
        scores.append(score)
    summary = summarize_scores(scores)
    write_json(output_dir / "summary.json", summary)
    report = [
        "# Offline diagnosis quality evaluation", "",
        f"Classification: `{manifest['classification']}`", "",
        "Rates include every scheduled attempt. Missing failure telemetry stays unknown.",
        "Scripted results validate the scorer; model accuracy and productivity remain unmeasured.",
        "Replay timestamps are synthetic and are excluded from latency measurements.", "",
        "| Attempt | Fixture | Contract | Quality | Repair |",
        "| --- | --- | --- | --- | --- |",
    ]
    report.extend(
        f"| {item['attempt_id']} | {item['fixture_id']} | {item['contract_pass']} | {item['quality_pass']} | {item['repair_used']} |"
        for item in scores
    )
    (output_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    manifest.update(status="completed", completed_at=datetime.now(timezone.utc).isoformat())
    write_json(output_dir / "manifest.json", manifest)
    print(json.dumps({"output_dir": str(output_dir), **summary}, ensure_ascii=True))
    return 0 if all(item["quality_pass"] for item in scores) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--replay", type=Path)
    source.add_argument("--attempts", type=Path)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    return evaluate(source_path=args.attempts or args.replay or DEFAULT_REPLAY,
                    replay=args.attempts is None, catalog_path=args.catalog,
                    rubric_path=args.rubric, output_dir=args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
