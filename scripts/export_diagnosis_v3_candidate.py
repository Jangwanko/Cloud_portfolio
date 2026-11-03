"""Export the isolated v3 contract and hand-authored examples; never run a model."""

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.diagnosis_scenarios import ControlledScenarioRegistry, load_scenario_catalog  # noqa: E402
from ops_agent.diagnosis_v3_contract import decision_v3_contract  # noqa: E402
from ops_agent.diagnosis_v3_models import AgentDecisionV3  # noqa: E402
from ops_agent.diagnosis_v3_validator import SIGNAL_RULES, validate_decision_v3  # noqa: E402
from ops_agent.evaluation_models import canonical_sha256  # noqa: E402
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG, write_json  # noqa: E402


def build_examples(catalog):
    examples = []
    for case in catalog.scenarios:
        registry = ControlledScenarioRegistry(catalog=catalog, fixture_id=case.fixture_id)
        # Fixed four-tool acquisition for readable examples; not an investigation agent.
        evidence = [registry.execute(tool) for tool in (
            "get_worker_stage_latency", "get_postgres_health", "get_worker_replica_status", "get_keda_status",
        )]
        by_tool = {item.tool_id: item for item in evidence}
        observations = [{"signal": signal, "evidence_ids": [item.evidence_id]}
            for signal, (tool, flag) in SIGNAL_RULES.items()
            for item in [by_tool[tool]]
            if item.status.value == "OK" and item.freshness.status.value == "FRESH"
            and item.summary.get("semantic_flag") == flag]
        # These authored expected examples are never inserted into model prompts.
        anchors = {
            "worker-replica-shortfall": ("WORKER_CAPACITY_SHORTFALL_SUSPECTED", "get_worker_replica_status"),
            "postgres-path-degradation": ("POSTGRES_GUARDRAIL_PRESSURE_SUSPECTED", "get_postgres_health"),
        }
        hypotheses, support_ids = [], []
        if case.fixture_id in anchors:
            name, tool = anchors[case.fixture_id]
            support_ids = [by_tool[tool].evidence_id]
            hypotheses = [{"hypothesis": name, "support_status": "SUPPORTED",
                "supporting_evidence_ids": support_ids, "conflicting_evidence_ids": [], "evidence_gaps": []}]
        gaps = [] if hypotheses else ["CAUSAL_DISCRIMINATOR_UNAVAILABLE"]
        decision = AgentDecisionV3.model_validate({
            "schema_version": "ops.diagnosis.decision.v3",
            "input_condition": "CORE_BACKLOG_PRESSURE=PRESENT",
            "observations": observations, "hypotheses": hypotheses,
            "context_evidence_ids": [*catalog.activation["evidence_ids"],
                *(item.evidence_id for item in evidence if item.evidence_id not in support_ids)],
            "investigation_gaps": gaps,
            "conclusion": "SUPPORTED_CANDIDATES" if hypotheses else "ABSTAIN",
        })
        validation = validate_decision_v3(decision,
            initial_evidence_ids=catalog.activation["evidence_ids"], acquired_evidence=evidence)
        examples.append({"fixture_id": case.fixture_id, "acquired_evidence": [item.model_dump(mode="json") for item in evidence],
                         "decision": decision.model_dump(mode="json"), "validation": validation})
    return {"classification": "HAND_AUTHORED_OFFLINE_EXAMPLES_NOT_MODEL_INPUT",
            "catalog_sha256": canonical_sha256(catalog.model_dump(mode="json")), "examples": examples}


def export_candidate(output_dir):
    catalog = load_scenario_catalog(DEFAULT_CATALOG)
    contract = decision_v3_contract()
    examples = build_examples(catalog)
    output_dir.mkdir(parents=True, exist_ok=False)
    paths = [Path(__file__), ROOT / "ops_agent/diagnosis_v3_models.py",
             ROOT / "ops_agent/diagnosis_v3_validator.py", ROOT / "ops_agent/diagnosis_v3_contract.py"]
    manifest = {"classification": "OFFLINE_CONTRACT_CANDIDATE", "status": "running",
                "created_at": datetime.now(timezone.utc).isoformat(), "contract_identity": contract["identity"],
                "catalog_sha256": examples["catalog_sha256"], "api_requests": 0,
                "file_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}}
    write_json(output_dir / "manifest.json", manifest)
    write_json(output_dir / "contract.json", contract)
    write_json(output_dir / "examples.json", examples)
    manifest.update(status="completed", authored_example_count=len(examples["examples"]))
    write_json(output_dir / "manifest.json", manifest)
    print(f"Offline contract candidate and {len(examples['examples'])} authored examples: {output_dir}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    return export_candidate(args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
