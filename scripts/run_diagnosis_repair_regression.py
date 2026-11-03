"""Preregistered 16-trial regression, retaining first-pass and final quality separately."""

from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.diagnosis_agent import OpenAIResponsesClient, load_openai_configuration  # noqa: E402
from ops_agent.diagnosis_quality_v3 import score_v3  # noqa: E402
from ops_agent.diagnosis_repair_v3 import enforce_repair_scope  # noqa: E402
from ops_agent.diagnosis_scenarios import load_scenario_catalog  # noqa: E402
from ops_agent.diagnosis_v2_models import DiagnosisEvidenceV2  # noqa: E402
from ops_agent.scenario_agent_v3 import execution_contract_v3  # noqa: E402
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG, write_json  # noqa: E402
from scripts.run_diagnosis_v3_pilot import DEFAULT_RUBRIC_V3, export_compact_v3, run_pilot_v3  # noqa: E402

GROUPS = [
    ("development", DEFAULT_CATALOG, DEFAULT_RUBRIC_V3),
    ("novel-now-regression", ROOT / "ops_agent/fixtures/diagnosis/novel_v1/catalog.json",
     ROOT / "ops_agent/fixtures/diagnosis/novel_v1/rubric.json"),
]


def initial_score_v3(run, *, catalog, rubric):
    initial = deepcopy(run)
    decisions = run.get("decisions", [])
    first = decisions[0] if decisions else {}
    initial.update(decision=first.get("raw"), contract_pass=bool(first.get("contract_pass")),
                   first_pass_contract=bool(first.get("contract_pass")), repair_used=0,
                   failure_code=first.get("failure_code", run.get("failure_code")))
    return score_v3(initial, catalog=catalog, rubric=rubric)


def run_regression(*, output_dir, model, client_factory):
    output_dir.mkdir(parents=True, exist_ok=False)
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"schema_version": "ops.diagnosis.repair-regression.v1", "status": "running",
        "classification": "PAID_FIXED_REGRESSION_NOT_HOLDOUT", "started_at": datetime.now(timezone.utc).isoformat(),
        "model": model, "execution_identity": execution_contract_v3()["identity"],
        "scheduled_attempt_count": 16, "max_api_requests": 96,
        "protocol": "Two fixed 4x2 groups, no adaptive tuning or paid retries. Preserve first-pass contract and joint scores independently of final scores. Existing novel cases are now regression cases.",
        "groups": [{"name": name, "catalog": str(catalog.relative_to(ROOT)), "rubric": str(rubric.relative_to(ROOT)),
                    "catalog_file_sha256": digest(catalog), "rubric_file_sha256": digest(rubric)}
                   for name, catalog, rubric in GROUPS],
        "driver_sha256": digest(Path(__file__))}
    write_json(output_dir / "manifest.json", manifest)
    write_json(output_dir / "execution-contract.json", execution_contract_v3())
    attempts, groups, aborted = [], [], False
    for name, catalog_path, rubric_path in GROUPS:
        group_dir = output_dir / name
        code = run_pilot_v3(output_dir=group_dir, model=model, client_factory=client_factory,
                            catalog_path=catalog_path, rubric_path=rubric_path)
        export_compact_v3(source_dir=group_dir, output_path=group_dir / "compact.json")
        group_summary = json.loads((group_dir / "summary.json").read_bytes())
        catalog = load_scenario_catalog(catalog_path)
        rubric = json.loads(rubric_path.read_bytes())
        group_manifest = json.loads((group_dir / "manifest.json").read_bytes())
        for planned in group_manifest["schedule"][:group_summary["executed_attempt_count"]]:
            state_path = group_dir / planned["attempt_id"] / "state.json"
            state = json.loads(state_path.read_bytes())
            run = state["run"]
            if run["repair_used"] and run["contract_pass"]:
                audit = enforce_repair_scope(run["decisions"][0]["raw"], run["decision"],
                    acquired_evidence=[DiagnosisEvidenceV2.model_validate(item) for item in run["acquired_evidence"]])
                if audit != run["repair_scope_audit"]:
                    raise ValueError("saved repair scope audit mismatch")
            attempts.append({"group": name, **planned, "state_sha256": digest(state_path),
                "first_pass_score": initial_score_v3(run, catalog=catalog, rubric=rubric),
                "final_score": state["live_score"], "fixed_runbook_score": state["fixed_runbook_score"],
                "repair_scope_audit": run.get("repair_scope_audit"),
                "initial_failure_code": run["decisions"][0]["failure_code"] if run.get("decisions") else run["failure_code"],
                "initial_decision": run["decisions"][0]["raw"] if run.get("decisions") else None,
                "final_decision": run.get("decision")})
        groups.append({"name": name, "exit_code": code, "summary": group_summary,
                       "compact_sha256": digest(group_dir / "compact.json"),
                       "manifest_sha256": digest(group_dir / "manifest.json")})
        if code == 2:
            aborted = True
            break
    summary = {"schema_version": "ops.diagnosis.repair-regression-summary.v1",
        "scheduled_attempt_count": 16, "executed_attempt_count": len(attempts), "unrun_attempt_count": 16 - len(attempts),
        "first_pass_contract_pass": sum(item["first_pass_score"]["contract_pass"] for item in attempts),
        "first_pass_joint_pass": sum(item["first_pass_score"]["joint_pass"] for item in attempts),
        "final_contract_pass": sum(item["final_score"]["contract_pass"] for item in attempts),
        "final_joint_pass": sum(item["final_score"]["joint_pass"] for item in attempts),
        "fixed_runbook_joint_pass": sum(item["fixed_runbook_score"]["joint_pass"] for item in attempts),
        "repair_used": sum(item["final_score"]["repair_used"] for item in attempts),
        "repaired_final_pass": sum(not item["first_pass_score"]["joint_pass"] and item["final_score"]["joint_pass"] for item in attempts),
        "observation_removal_attempt_count": sum(bool((item["repair_scope_audit"] or {}).get("removed_observations")) for item in attempts),
        "api_request_count": sum(group["summary"]["api_request_count"] for group in groups),
        "input_tokens": sum(group["summary"]["input_tokens"] for group in groups),
        "output_tokens": sum(group["summary"]["output_tokens"] for group in groups),
        "elapsed_seconds": sum(group["summary"]["elapsed_seconds"] for group in groups),
        "usage_coverage_complete": all(group["summary"]["usage_coverage_complete"] for group in groups)}
    write_json(output_dir / "summary.json", summary)
    write_json(output_dir / "attempts.json", {"groups": groups, "attempts": attempts})
    manifest.update(status="aborted" if aborted else "completed", finished_at=datetime.now(timezone.utc).isoformat(),
                    api_request_count=summary["api_request_count"])
    write_json(output_dir / "manifest.json", manifest)
    print(json.dumps(summary), flush=True)
    return 2 if aborted else (0 if summary["final_joint_pass"] == 16 else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("explicit --live required; 16 trials incur API costs")
    key, model = load_openai_configuration(ROOT)
    return run_regression(output_dir=args.output_dir, model=model,
        client_factory=lambda: OpenAIResponsesClient(api_key=key, timeout_seconds=30, max_retries=0))


if __name__ == "__main__":
    raise SystemExit(main())
