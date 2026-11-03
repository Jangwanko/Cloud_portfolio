"""Author a second frozen-model synthetic transfer probe; never invoke the model."""

import argparse
from copy import deepcopy
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.diagnosis_scenarios import ScenarioCatalog, load_scenario_catalog  # noqa: E402
from ops_agent.evaluation_models import canonical_sha256  # noqa: E402
from ops_agent.scenario_agent_v3 import execution_contract_v3  # noqa: E402
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG, write_json  # noqa: E402


def build_probe_cases():
    base = load_scenario_catalog(DEFAULT_CATALOG).model_dump(mode="json")
    template = base["scenarios"][0]
    cases, scenarios = {}, []
    expected = [
        ("probe2-01", "Three failed primary queries", [], []),
        ("probe2-02", "Fresh capacity deficit with stale healthy PostgreSQL",
         ["WORKER_STAGE_ELEVATED", "WORKER_REPLICA_SHORTFALL"], ["WORKER_CAPACITY_SHORTFALL_SUSPECTED"]),
        ("probe2-03", "Fresh unreachable PostgreSQL with stale stage and missing replicas",
         ["POSTGRES_GUARDRAIL_DEGRADED"], ["POSTGRES_GUARDRAIL_PRESSURE_SUSPECTED"]),
        ("probe2-04", "Two fresh adverse signals with missing stage and stale scaler",
         ["WORKER_REPLICA_SHORTFALL", "POSTGRES_GUARDRAIL_DEGRADED"],
         ["WORKER_CAPACITY_SHORTFALL_SUSPECTED", "POSTGRES_GUARDRAIL_PRESSURE_SUSPECTED"]),
    ]
    for index, (fixture_id, title, observations, causes) in enumerate(expected):
        case = deepcopy(template)
        case.update(fixture_id=fixture_id, title=title, expected_primary_hypothesis="UNSCORED_V3_METADATA",
            branch_expectations=[{"after_tool_id": "get_worker_stage_latency", "expected_next_tools": []}])
        tools = case["observations"]
        for item in tools.values():
            item.update(status="OK", freshness="FRESH", observed_at="2026-08-23T15:27:08Z",
                        max_age_seconds=15.0, error_code=None)
        def fail(tool, status="UNAVAILABLE"):
            tools[tool].update(status=status, freshness="UNKNOWN", metrics={}, error_code="PROBE_SOURCE_TIMEOUT")
        def stale(tool):
            tools[tool].update(freshness="STALE", observed_at="2026-08-23T15:22:08Z")
        if index == 0:
            fail("get_worker_stage_latency", "ERROR")
            fail("get_postgres_health", "ERROR")
            fail("get_worker_replica_status")
            fail("get_keda_status")
        elif index == 1:
            tools["get_worker_stage_latency"]["metrics"].update(worker_stage_mean_ms=10.0, scenario_baseline_ms=5.0)
            stale("get_postgres_health")
            tools["get_worker_replica_status"]["metrics"].update(
                desired_replicas=7, current_replicas=7, ready_replicas=0, available_replicas=0)
            fail("get_keda_status")
        elif index == 2:
            tools["get_worker_stage_latency"]["metrics"].update(worker_stage_mean_ms=21.0, scenario_baseline_ms=4.0)
            stale("get_worker_stage_latency")
            tools["get_postgres_health"]["metrics"].update(primary_reachable=False, max_replication_delay_bytes=256)
            fail("get_worker_replica_status")
            tools["get_keda_status"]["metrics"].update(current_replicas=2, max_replicas=7)
            tools["get_application_readiness"]["metrics"].update(state="degraded", postgres_component="degraded")
        else:
            fail("get_worker_stage_latency")
            tools["get_postgres_health"]["metrics"].update(max_replication_delay_bytes=1048577)
            tools["get_worker_replica_status"]["metrics"].update(
                desired_replicas=5, current_replicas=5, ready_replicas=4, available_replicas=4)
            tools["get_keda_status"]["metrics"].update(active=False, current_replicas=5, max_replicas=5)
            stale("get_keda_status")
            tools["get_application_readiness"]["metrics"].update(state="degraded", postgres_component="degraded")
        replicas = tools["get_worker_replica_status"]["metrics"]
        if replicas:
            tools["get_pod_restart_status"]["metrics"]["ready_pods"] = replicas["ready_replicas"]
            tools["get_runtime_image"]["metrics"]["pod_images"] = ["messaging-portfolio:verified"] * replicas["current_replicas"]
        else:
            fail("get_pod_restart_status")
            fail("get_runtime_image")
        case.pop("fixture_digest")
        case["fixture_digest"] = canonical_sha256(case)
        scenarios.append(case)
        required = ["get_worker_stage_latency", "get_postgres_health", "get_worker_replica_status"]
        if index in (1, 3):
            required.append("get_keda_status")
        cases[fixture_id] = {"observations": observations, "supported_causes": causes, "required_tools": required}
    base["scenarios"] = scenarios
    return ScenarioCatalog.model_validate(base), {
        "schema_version": "ops.diagnosis.quality-rubric.v3.1",
        "policy": "Second author-created synthetic transfer probe. Unchanged v3 scorer semantics: exact observations and supported causes, separate attempted investigation coverage, all executed attempts in denominators. Freeze v3.5 and preserve first 4x2 batch; no result-driven tuning. Not independent production ground truth.",
        "cases": cases}


def export_probe(output_dir):
    catalog, rubric = build_probe_cases()
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json(output_dir / "catalog.json", catalog.model_dump(mode="json"))
    write_json(output_dir / "rubric.json", rubric)
    write_json(output_dir / "design.json", {
        "classification": "AUTHOR_CREATED_SECOND_SYNTHETIC_TRANSFER_PROBE",
        "case_count": 4, "planned_attempts": 8, "max_api_requests": 48,
        "catalog_sha256": canonical_sha256(catalog.model_dump(mode="json")),
        "rubric_sha256": canonical_sha256(rubric), "frozen_execution_identity": execution_contract_v3()["identity"],
        "author_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "activation_scope": "Historical PRESENT activation reused; synthetic normalized observations and snapshot-relative freshness, no live incident or outage injection.",
        "protocol": "One 4x2 model batch on newly authored combinations. Freeze v3.5 model, prompt, runtime policies, decision/semantic contracts, scorer and old fixtures. Preserve initial/final scores and failures, no paid retuning.",
        "legacy_metadata": "UNSCORED_V3_METADATA and branch placeholders are not model input or v3 scoring criteria."})
    print(f"Second synthetic probe and preregistered expected sets: {output_dir}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    return export_probe(parser.parse_args().output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
