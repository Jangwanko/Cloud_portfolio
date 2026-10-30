"""Author four novel synthetic combinations without calling or changing the model."""

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


def build_novel_cases():
    base = load_scenario_catalog(DEFAULT_CATALOG).model_dump(mode="json")
    template = base["scenarios"][0]
    scenarios, cases = [], {}
    designs = [
        ("novel-01", "Simultaneous direct causal candidates",
         ["WORKER_REPLICA_SHORTFALL", "POSTGRES_GUARDRAIL_DEGRADED"],
         ["WORKER_CAPACITY_SHORTFALL_SUSPECTED", "POSTGRES_GUARDRAIL_PRESSURE_SUSPECTED"]),
        ("novel-02", "Stale adverse measurements with a fresh symptom",
         ["WORKER_STAGE_ELEVATED"], []),
        ("novel-03", "Unavailable stage and scaler with a fresh replica deficit",
         ["WORKER_REPLICA_SHORTFALL"], ["WORKER_CAPACITY_SHORTFALL_SUSPECTED"]),
        ("novel-04", "No directly supported cause despite backlog and skew",
         [], []),
    ]
    for index, (fixture_id, title, observations, causes) in enumerate(designs):
        case = deepcopy(template)
        case.update(fixture_id=fixture_id, title=title,
            expected_primary_hypothesis="UNSCORED_V3_METADATA",
            branch_expectations=[{"after_tool_id": "get_worker_stage_latency", "expected_next_tools": []}])
        tools = case["observations"]
        for item in tools.values():
            item.update(observed_at="2026-08-23T15:27:08Z", status="OK", freshness="FRESH",
                        max_age_seconds=15.0, error_code=None)
        tools["get_worker_stage_latency"]["metrics"].update(
            worker_stage_mean_ms=4.1, scenario_baseline_ms=5.0, observation_count=1800 + index * 19)
        if index == 0:
            tools["get_worker_replica_status"]["metrics"].update(
                desired_replicas=6, current_replicas=6, ready_replicas=3, available_replicas=3)
            tools["get_postgres_health"]["metrics"].update(
                standby_count=1, sync_standby_count=0, max_replication_delay_bytes=3145728)
            tools["get_application_readiness"]["metrics"].update(state="degraded", postgres_component="degraded")
        elif index == 1:
            tools["get_worker_stage_latency"]["metrics"].update(worker_stage_mean_ms=17.5)
            tools["get_worker_replica_status"]["metrics"].update(
                desired_replicas=5, current_replicas=5, ready_replicas=1, available_replicas=1)
            tools["get_postgres_health"]["metrics"].update(
                primary_reachable=False, standby_count=0, sync_standby_count=0,
                max_replication_delay_bytes=4194304)
            for tool in ("get_worker_replica_status", "get_postgres_health"):
                tools[tool].update(freshness="STALE", observed_at="2026-08-23T15:22:08Z")
            tools["get_application_readiness"]["metrics"].update(state="unknown", postgres_component="unknown")
        elif index == 2:
            tools["get_worker_replica_status"]["metrics"].update(
                desired_replicas=6, current_replicas=6, ready_replicas=4, available_replicas=4)
            for tool in ("get_worker_stage_latency", "get_keda_status"):
                tools[tool].update(status="UNAVAILABLE", freshness="UNKNOWN", metrics={}, error_code="SOURCE_TIMEOUT")
        else:
            tools["get_worker_stage_latency"]["metrics"].update(worker_stage_mean_ms=9.95)
            tools["get_worker_replica_status"]["metrics"].update(
                desired_replicas=3, current_replicas=3, ready_replicas=3, available_replicas=3)
            tools["get_postgres_health"]["metrics"].update(max_replication_delay_bytes=1048576)
            tools["get_partition_lag"]["metrics"].update(
                per_partition_lag={str(i): 14000 if i == 3 else 8 for i in range(8)})
        replicas = tools["get_worker_replica_status"]["metrics"]
        if tools["get_keda_status"]["status"] == "OK":
            tools["get_keda_status"]["metrics"].update(
                current_replicas=replicas["current_replicas"], max_replicas=6)
        tools["get_pod_restart_status"]["metrics"]["ready_pods"] = replicas["ready_replicas"]
        tools["get_runtime_image"]["metrics"]["pod_images"] = ["messaging-portfolio:verified"] * replicas["current_replicas"]
        case.pop("fixture_digest")
        case["fixture_digest"] = canonical_sha256(case)
        scenarios.append(case)
        required = ["get_worker_stage_latency", "get_postgres_health", "get_worker_replica_status"]
        if index in (0, 2):
            required.append("get_keda_status")
        cases[fixture_id] = {"observations": observations, "supported_causes": causes, "required_tools": required}
    base["scenarios"] = scenarios
    catalog = ScenarioCatalog.model_validate(base)
    rubric = {"schema_version": "ops.diagnosis.quality-rubric.v3.1",
        "policy": "Unchanged v3 scorer/validator; exact positive observation and supported-cause sets. All executed attempts in denominators; completeness independent. Three primary discriminators required in every novel case; KEDA additionally required for fresh replica deficit. Author-created synthetic transfer probe, not independent production ground truth.",
        "cases": cases}
    return catalog, rubric


def export_novel_cases(output_dir):
    catalog, rubric = build_novel_cases()
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json(output_dir / "catalog.json", catalog.model_dump(mode="json"))
    write_json(output_dir / "rubric.json", rubric)
    write_json(output_dir / "design.json", {
        "classification": "AUTHOR_CREATED_SYNTHETIC_TRANSFER_PROBE",
        "case_count": 4, "planned_attempts": 8, "max_api_requests": 48,
        "training_catalog_sha256": canonical_sha256(load_scenario_catalog(DEFAULT_CATALOG).model_dump(mode="json")),
        "catalog_sha256": canonical_sha256(catalog.model_dump(mode="json")), "rubric_sha256": canonical_sha256(rubric),
        "frozen_execution_identity": execution_contract_v3()["identity"],
        "author_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "activation_scope": "Historical PRESENT activation reused as controlled context; synthetic observations use frozen snapshot-relative freshness, not live source freshness.",
        "unused_metadata": "expected_primary_hypothesis and branch_expectations are inherited schema placeholders, never sent to model or scored by v3.",
        "protocol": "One 4x2 paid batch. Freeze model/prompt/schema/validator/scorer/normalizer and old development fixtures. Preserve first failures; no result-driven prompt adjustment in this evaluation.",
    })
    print(f"Four novel synthetic cases and preregistered rubric: {output_dir}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    return export_novel_cases(parser.parse_args().output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
