from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ops_agent import cli
from ops_agent.diagnosis_agent import ModelResponse
from ops_agent.diagnosis_models import (
    DiagnosisEvidenceStatus,
    DiagnosisFreshness,
    DiagnosisStopReason,
    HypothesisSupportStatus,
)
from ops_agent.diagnosis_scenarios import (
    ControlledScenarioRegistry,
    ScenarioCatalog,
    load_scenario_catalog,
)
from ops_agent.diagnosis_v2_models import (
    AgentDecisionV2,
    ControlledScenarioProvenance,
    DiagnosisAcquisitionMode,
    DiagnosisEvidenceV2,
    DiagnosisPolicyV2,
    FrozenProjectedProvenance,
    HypothesisNameV2,
    HypothesisResultV2,
    LiveReadOnlyProvenance,
)
from ops_agent.diagnosis_v2_validator import DiagnosisOutputValidatorV2
from ops_agent.diagnosis_validator import DiagnosisValidationError
from ops_agent.models import FreshnessStatus
from ops_agent.scenario_agent import RecordedBranchModelClient, run_scenario_diagnosis
from scripts.build_scenario_lab_artifact import build_artifact


ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "ops_agent" / "fixtures" / "diagnosis" / "scenario_lab_v1.json"
REPLAY_PATH = ROOT / "scenario_lab" / "scenario-lab-replay.json"


@pytest.fixture(scope="module")
def catalog() -> ScenarioCatalog:
    return load_scenario_catalog(CATALOG_PATH)


def _run(catalog: ScenarioCatalog, fixture_id: str):
    return run_scenario_diagnosis(
        catalog=catalog,
        fixture_id=fixture_id,
        client=RecordedBranchModelClient(),
        policy=DiagnosisPolicyV2(
            model="recorded-branch-policy-v1",
            model_mode="recorded",
            max_retries=0,
            max_output_repairs=0,
        ),
        started_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 28, 0, 0, 1, tzinfo=timezone.utc),
    )


def test_catalog_has_four_integrity_checked_scenarios(catalog: ScenarioCatalog) -> None:
    assert [item.fixture_id for item in catalog.scenarios] == [
        "worker-db-path-pressure",
        "worker-replica-shortfall",
        "postgres-path-degradation",
        "telemetry-unavailable",
    ]
    assert catalog.activation["state"] == "PRESENT"
    assert len(catalog.activation["source_bundle_digests"]) == 3
    for scenario in catalog.scenarios:
        scenario.verify_integrity()


def test_catalog_integrity_rejects_tampered_metric() -> None:
    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    tampered = deepcopy(raw)
    tampered["scenarios"][0]["observations"]["get_worker_stage_latency"][
        "metrics"
    ]["worker_stage_mean_ms"] = 999
    with pytest.raises(ValueError, match="fixture digest mismatch"):
        ScenarioCatalog.model_validate(tampered)


def test_acquisition_modes_enforce_distinct_provenance_contracts() -> None:
    now = datetime.now(timezone.utc)
    frozen = FrozenProjectedProvenance(
        acquisition_mode=DiagnosisAcquisitionMode.FROZEN_PROJECTED,
        source_bundle_digests=["a" * 64],
        source_evidence_ids=["evidence-1"],
    )
    scenario = ControlledScenarioProvenance(
        acquisition_mode=DiagnosisAcquisitionMode.CONTROLLED_SCENARIO,
        fixture_id="fixture",
        fixture_digest="b" * 64,
    )
    live = LiveReadOnlyProvenance(
        acquisition_mode=DiagnosisAcquisitionMode.LIVE_READ_ONLY,
        source_identity="prometheus/local-ha",
        query_contract_version="prometheus.worker-stage.v1",
        requested_at=now,
        source_timestamp=now,
    )
    assert frozen.source_bundle_digests == ["a" * 64]
    assert scenario.scenario_contract_version == "ops.diagnosis.scenario.v1"
    assert live.source_identity == "prometheus/local-ha"

    common = {
        "evidence_id": "evidence",
        "tool_id": "get_postgres_health",
        "status": "OK",
        "observed_at": now,
        "freshness": {"status": "FRESH", "max_age_seconds": 10},
        "semantic_type": "normalized_postgres",
        "summary": {},
    }
    with pytest.raises(ValueError):
        DiagnosisEvidenceV2.model_validate(
            {
                **common,
                "provenance": {
                    "acquisition_mode": "CONTROLLED_SCENARIO",
                    "source_bundle_digests": ["a" * 64],
                },
            }
        )


def test_registry_normalizes_raw_scenario_observations(catalog: ScenarioCatalog) -> None:
    pressure = ControlledScenarioRegistry(
        catalog=catalog,
        fixture_id="worker-db-path-pressure",
    )
    stage = pressure.execute("get_worker_stage_latency")
    postgres = pressure.execute("get_postgres_health")
    assert stage.summary["worker_stage_mean_ms"] == 14.3
    assert stage.summary["relative_to_baseline"] == pytest.approx(4.46875)
    assert stage.summary["semantic_flag"] == "ABOVE_SCENARIO_BASELINE"
    assert postgres.summary["semantic_flag"] == "POSTGRES_PATH_HEALTHY"
    assert stage.provenance.acquisition_mode == DiagnosisAcquisitionMode.CONTROLLED_SCENARIO

    shortfall = ControlledScenarioRegistry(
        catalog=catalog,
        fixture_id="worker-replica-shortfall",
    ).execute("get_worker_replica_status")
    assert shortfall.summary["availability_gap"] == 2
    assert shortfall.summary["semantic_flag"] == "WORKER_CAPACITY_SHORTFALL"


@pytest.mark.parametrize(
    ("fixture_id", "tools", "hypothesis", "stop_reason"),
    [
        (
            "worker-db-path-pressure",
            [
                "get_worker_stage_latency",
                "get_postgres_health",
                "get_worker_replica_status",
            ],
            "WORKER_PATH_PRESSURE_SUSPECTED",
            "sufficient_evidence",
        ),
        (
            "worker-replica-shortfall",
            [
                "get_worker_stage_latency",
                "get_worker_replica_status",
                "get_keda_status",
            ],
            "WORKER_CAPACITY_SHORTFALL_SUSPECTED",
            "sufficient_evidence",
        ),
        (
            "postgres-path-degradation",
            ["get_worker_stage_latency", "get_postgres_health"],
            "POSTGRES_PATH_DEGRADED_SUSPECTED",
            "sufficient_evidence",
        ),
        (
            "telemetry-unavailable",
            ["get_worker_stage_latency", "get_postgres_health"],
            "INSUFFICIENT_EVIDENCE",
            "insufficient_evidence",
        ),
    ],
)
def test_recorded_scenarios_use_real_agent_loop_and_validator(
    catalog: ScenarioCatalog,
    fixture_id: str,
    tools: list[str],
    hypothesis: str,
    stop_reason: str,
) -> None:
    run = _run(catalog, fixture_id)
    assert run.schema_version == "ops.diagnosis.v2"
    assert run.acquisition_mode == DiagnosisAcquisitionMode.CONTROLLED_SCENARIO
    assert [item.tool_id for item in run.steps] == tools
    assert run.hypotheses[0].hypothesis.value == hypothesis
    assert run.stop_reason.value == stop_reason
    assert run.validation.schema_valid is True
    assert {item.result for item in run.branch_evaluations} == {"PASS"}
    assert run.usage.api_requests == 0
    assert run.model_turns == len(tools) + 1
    run.verify_integrity()


def test_paired_observation_changes_the_next_tool(catalog: ScenarioCatalog) -> None:
    elevated = _run(catalog, "worker-db-path-pressure")
    normal = _run(catalog, "worker-replica-shortfall")
    assert elevated.steps[0].tool_id == normal.steps[0].tool_id == "get_worker_stage_latency"
    assert elevated.additional_evidence[0].summary["semantic_flag"] == (
        "ABOVE_SCENARIO_BASELINE"
    )
    assert normal.additional_evidence[0].summary["semantic_flag"] == (
        "WITHIN_SCENARIO_BASELINE"
    )
    assert elevated.steps[1].tool_id == "get_postgres_health"
    assert normal.steps[1].tool_id == "get_worker_replica_status"


def test_unavailable_is_evidence_and_causes_abstention(catalog: ScenarioCatalog) -> None:
    run = _run(catalog, "telemetry-unavailable")
    postgres = run.additional_evidence[1]
    assert postgres.status == DiagnosisEvidenceStatus.UNAVAILABLE
    assert postgres.error_code == "PROMETHEUS_TIMEOUT"
    assert postgres.freshness.status == FreshnessStatus.UNKNOWN
    assert run.hypotheses[0].support_status == HypothesisSupportStatus.INSUFFICIENT
    assert run.hypotheses[0].evidence_gaps == [
        "POSTGRES_HEALTH_TELEMETRY_UNAVAILABLE"
    ]


class UnexpectedBranchClient:
    def __init__(self) -> None:
        self.turn = 0

    def create(self, payload: dict[str, Any]) -> ModelResponse:
        self.turn += 1
        tool = "get_worker_stage_latency" if self.turn == 1 else "get_runtime_image"
        if self.turn <= 2:
            return ModelResponse(
                response_id=f"unexpected-{self.turn}",
                output=[
                    {
                        "type": "function_call",
                        "call_id": f"unexpected-call-{self.turn}",
                        "name": tool,
                        "arguments": "{}",
                    }
                ],
                output_text=None,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
            )
        evidence_ids = [
            json.loads(item["output"])["evidence_id"]
            for item in payload["input"]
            if item.get("type") == "function_call_output"
        ]
        decision = {
            "hypotheses": [
                {
                    "hypothesis": "INSUFFICIENT_EVIDENCE",
                    "support_status": "INSUFFICIENT",
                    "reason_codes": ["UNEXPECTED_BRANCH"],
                    "supporting_evidence_ids": [],
                    "conflicting_evidence_ids": evidence_ids,
                    "evidence_gaps": ["EXPECTED_INVESTIGATION_PATH_NOT_FOLLOWED"],
                }
            ],
            "stop_reason": "insufficient_evidence",
        }
        output = json.dumps(decision)
        return ModelResponse(
            response_id=f"unexpected-{self.turn}",
            output=[{"type": "message", "content": [{"type": "output_text", "text": output}]}],
            output_text=output,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )


def test_unexpected_model_branch_is_recorded_as_failure(catalog: ScenarioCatalog) -> None:
    run = run_scenario_diagnosis(
        catalog=catalog,
        fixture_id="worker-db-path-pressure",
        client=UnexpectedBranchClient(),
        policy=DiagnosisPolicyV2(
            model="unexpected-test-model",
            model_mode="recorded",
            max_output_repairs=0,
        ),
        started_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 28, 0, 0, 1, tzinfo=timezone.utc),
    )
    first_branch = run.branch_evaluations[0]
    assert first_branch.expected_next_tools == ["get_postgres_health"]
    assert first_branch.selected_next_tool == "get_runtime_image"
    assert first_branch.result == "FAIL"
    assert run.validation.schema_valid is True


def test_v2_validator_rejects_fabricated_citation() -> None:
    decision = AgentDecisionV2(
        hypotheses=[
            HypothesisResultV2(
                hypothesis=HypothesisNameV2.WORKER_CAPACITY_SHORTFALL_SUSPECTED,
                support_status=HypothesisSupportStatus.SUPPORTED,
                reason_codes=["FABRICATED"],
                supporting_evidence_ids=["fabricated"],
            )
        ],
        stop_reason=DiagnosisStopReason.SUFFICIENT_EVIDENCE,
    )
    with pytest.raises(DiagnosisValidationError, match="unknown evidence"):
        DiagnosisOutputValidatorV2(
            allowlisted_tool_ids={"get_worker_replica_status"}
        ).validate(
            decision=decision,
            policy=DiagnosisPolicyV2(model="test-model"),
            initial_evidence_ids=[],
            additional_evidence=[],
            steps=[],
        )


def test_cli_recorded_scenario_never_loads_openai_configuration(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli,
        "load_openai_configuration",
        lambda _workspace: (_ for _ in ()).throw(AssertionError("OpenAI must not load")),
    )
    output = tmp_path / "scenario.json"
    result = cli.run_diagnose_scenario(
        SimpleNamespace(
            catalog=CATALOG_PATH,
            scenario="worker-db-path-pressure",
            model_mode="recorded",
            output=output,
        )
    )
    assert result == 0
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == (
        "ops.diagnosis.v2"
    )


def test_committed_scenario_replay_is_reproducible() -> None:
    assert json.loads(REPLAY_PATH.read_text(encoding="utf-8")) == build_artifact()


def test_local_scenario_lab_ui_preserves_mode_and_replay_boundaries() -> None:
    source = (ROOT / "scenario_lab" / "index.html").read_text(encoding="utf-8")
    assert "AI Investigation Scenario Lab" in source
    assert "LOCAL / ADMIN" in source
    assert "CONTROLLED_SCENARIO" in source
    assert "READ-ONLY" in source
    assert 'fetch("scenario-lab-replay.json"' in source
    assert "grid-auto-flow: column" in source
    assert "Branch Evaluation" in source
    assert "Expected:" in source
    assert "Selected:" in source
    assert "Validator" in source
    assert "원인의 사실 여부를 독립적으로 확정하지 않습니다" in source
    assert "api.openai.com" not in source
    assert "OPENAI_API_KEY" not in source


def test_scenario_replay_contains_only_recorded_model_runs() -> None:
    artifact = json.loads(REPLAY_PATH.read_text(encoding="utf-8"))
    assert artifact["classification"] == "CONTROLLED_SCENARIO_REPLAY"
    assert len(artifact["scenarios"]) == 4
    for scenario in artifact["scenarios"]:
        run = scenario["run"]
        assert run["schema_version"] == "ops.diagnosis.v2"
        assert run["acquisition_mode"] == "CONTROLLED_SCENARIO"
        assert run["policy"]["model_mode"] == "recorded"
        assert run["usage"]["api_requests"] == 0
        assert all(item["result"] == "PASS" for item in run["branch_evaluations"])


def test_scenario_lab_is_not_part_of_the_public_demo_static_mount() -> None:
    assert not (ROOT / "demo" / "scenario-lab.html").exists()
    assert not (ROOT / "demo" / "scenario-lab-replay.json").exists()
    main_source = (ROOT / "portfolio" / "main.py").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    base = (ROOT / "k8s" / "gitops" / "base" / "manifests-ha.yaml").read_text(
        encoding="utf-8"
    )
    overlay = (
        ROOT / "k8s" / "gitops" / "overlays" / "local-ha" / "kustomization.yaml"
    ).read_text(encoding="utf-8")
    assert "if settings.scenario_lab_enabled:" in main_source
    assert '"/admin/scenario-lab"' in main_source
    assert "COPY --chown=app:app scenario_lab ./scenario_lab" in dockerfile
    assert 'SCENARIO_LAB_ENABLED: "false"' in base
    assert "name: SCENARIO_LAB_ENABLED" in overlay
    assert 'value: "true"' in overlay.split("name: SCENARIO_LAB_ENABLED", 1)[1]
