"""Review saved pilot results offline without changing their scores or source files."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.diagnosis_quality import QualityRubric, evaluation_identity, summarize_scores  # noqa: E402
from ops_agent.diagnosis_review import REVIEW_VERSION, review_attempt, summarize_reviews  # noqa: E402
from ops_agent.diagnosis_scenarios import load_scenario_catalog  # noqa: E402
from scripts.evaluate_diagnosis_quality import load_attempts, write_json  # noqa: E402


def review_pilot(*, input_dir, output_dir):
    source_manifest = json.loads((input_dir / "manifest.json").read_bytes())
    if source_manifest.get("status") != "completed":
        raise ValueError("review requires a completed pilot")
    catalog = load_scenario_catalog(input_dir / "catalog.json")
    rubric = QualityRubric.model_validate_json((input_dir / "rubric.json").read_bytes())
    identity = evaluation_identity(catalog, rubric)
    if source_manifest["evaluation_identity"] != identity:
        raise ValueError("source evaluation identity mismatch")
    batch_bytes = (input_dir / "live-attempts.json").read_bytes()
    evaluation_manifest = json.loads((input_dir / "live/manifest.json").read_bytes())
    if evaluation_manifest["source_sha256"] != hashlib.sha256(batch_bytes).hexdigest():
        raise ValueError("source batch does not match its recorded evaluation")
    attempts = load_attempts(json.loads(batch_bytes), replay=False, identity=identity)
    if [(item.attempt_id, item.fixture_id) for item in attempts] != [
        (item["attempt_id"], item["fixture_id"]) for item in source_manifest["schedule"]
    ]:
        raise ValueError("review batch must preserve the complete scheduled attempts")
    reviews = [review_attempt(item, catalog=catalog, rubric=rubric) for item in attempts]
    original_summary = json.loads((input_dir / "summary.json").read_bytes())
    if summarize_scores([item["legacy_score"] for item in reviews]) != original_summary["live"]:
        raise ValueError("recomputed v1 scores differ from the saved pilot")
    output_dir.mkdir(parents=True, exist_ok=False)
    source_paths = ["manifest.json", "catalog.json", "rubric.json", "live-attempts.json", "summary.json", "live/manifest.json"]
    manifest = {
        "classification": "POST_HOC_REVIEW_NO_NEW_MODEL_RUN", "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(), "review_version": REVIEW_VERSION,
        "source_pilot": str(input_dir), "evaluation_identity": identity,
        "source_sha256": {name: hashlib.sha256((input_dir / name).read_bytes()).hexdigest() for name in source_paths},
        "implementation_sha256": {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), ROOT / "ops_agent/diagnosis_review.py", ROOT / "ops_agent/diagnosis_quality.py")
        },
    }
    write_json(output_dir / "manifest.json", manifest)
    write_json(output_dir / "review.json", {"summary": summarize_reviews(reviews), "attempts": reviews})
    lines = ["# Offline pilot criteria review", "",
             "The original v1 quality scores remain unchanged. This report defines no new integrated pass rate.",
             "REVIEW_REQUIRED is neither a pass nor a demonstrated false citation.",
             "Fixture label agreement does not establish causal truth. Dimensions overlap and are not additive.", "",
             "| Attempt | v1 quality | Label alignment | Investigation | Abstention | Citation relations |",
             "| --- | --- | --- | --- | --- | --- |"]
    for item in reviews:
        dims = item["dimensions"]
        lines.append(f"| {item['attempt_id']} | {item['legacy_score']['quality_pass']} | {dims['fixture_label_alignment']} | {dims['investigation_completeness']} | {dims['abstention']} | {dims['citation_relations']} |")
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest.update(status="completed", finished_at=datetime.now(timezone.utc).isoformat())
    write_json(output_dir / "manifest.json", manifest)
    print(json.dumps(summarize_reviews(reviews)))
    # Success means the review completed; quality failures remain in the report.
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    return review_pilot(input_dir=args.input_dir, output_dir=args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
