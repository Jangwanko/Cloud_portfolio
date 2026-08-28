"""Build the deterministic local Scenario Lab replay artifact."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.diagnosis_scenarios import load_scenario_catalog  # noqa: E402
from ops_agent.diagnosis_v2_models import DiagnosisPolicyV2  # noqa: E402
from ops_agent.evaluation_models import canonical_sha256  # noqa: E402
from ops_agent.scenario_agent import (  # noqa: E402
    RecordedBranchModelClient,
    run_scenario_diagnosis,
)


CATALOG_PATH = ROOT / "ops_agent" / "fixtures" / "diagnosis" / "scenario_lab_v1.json"
OUTPUT_PATH = ROOT / "scenario_lab" / "scenario-lab-replay.json"
STARTED_AT = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)


def build_artifact() -> dict:
    catalog = load_scenario_catalog(CATALOG_PATH)
    policy = DiagnosisPolicyV2(
        model="recorded-branch-policy-v1",
        model_mode="recorded",
        max_retries=0,
        max_output_repairs=0,
    )
    scenarios = []
    for index, scenario in enumerate(catalog.scenarios):
        started_at = STARTED_AT + timedelta(minutes=index)
        run = run_scenario_diagnosis(
            catalog=catalog,
            fixture_id=scenario.fixture_id,
            client=RecordedBranchModelClient(),
            policy=policy,
            started_at=started_at,
            completed_at=started_at + timedelta(seconds=1),
        )
        scenarios.append(
            {
                "fixture_id": scenario.fixture_id,
                "title": scenario.title,
                "expected_primary_hypothesis": scenario.expected_primary_hypothesis,
                "run": run.model_dump(mode="json"),
            }
        )
    return {
        "schema_version": "demo.scenario-lab-replay.v1",
        "classification": "CONTROLLED_SCENARIO_REPLAY",
        "catalog_digest": canonical_sha256(catalog.model_dump(mode="json")),
        "generated_at": STARTED_AT.isoformat().replace("+00:00", "Z"),
        "activation": catalog.activation,
        "scenarios": scenarios,
    }


def main() -> int:
    payload = json.dumps(build_artifact(), indent=2, ensure_ascii=True) + "\n"
    OUTPUT_PATH.write_text(payload, encoding="utf-8")
    print(OUTPUT_PATH.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
