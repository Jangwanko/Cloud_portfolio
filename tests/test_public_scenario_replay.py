import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = ROOT / "demo" / "verified-scenario-replays.json"
DEMO_PATH = ROOT / "demo" / "order-dashboard.html"
EXPORTER_PATH = ROOT / "scripts" / "export_public_scenario_replays.py"

EXPECTED_TOOLS = {
    "worker-db-path-pressure": [
        "get_worker_stage_latency",
        "get_postgres_health",
        "get_worker_replica_status",
    ],
    "worker-replica-shortfall": [
        "get_worker_stage_latency",
        "get_worker_replica_status",
        "get_keda_status",
    ],
    "postgres-path-degradation": [
        "get_worker_stage_latency",
        "get_postgres_health",
    ],
    "telemetry-unavailable": [
        "get_worker_stage_latency",
        "get_postgres_health",
    ],
}

EXPECTED_RESULTS = {
    "worker-db-path-pressure": (
        "WORKER_PATH_PRESSURE_SUSPECTED",
        "SUPPORTED",
        "sufficient_evidence",
    ),
    "worker-replica-shortfall": (
        "WORKER_CAPACITY_SHORTFALL_SUSPECTED",
        "SUPPORTED",
        "sufficient_evidence",
    ),
    "postgres-path-degradation": (
        "POSTGRES_PATH_DEGRADED_SUSPECTED",
        "SUPPORTED",
        "sufficient_evidence",
    ),
    "telemetry-unavailable": (
        "INSUFFICIENT_EVIDENCE",
        "INSUFFICIENT",
        "insufficient_evidence",
    ),
}


def _artifact() -> dict:
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


def _load_exporter():
    spec = importlib.util.spec_from_file_location("public_scenario_exporter", EXPORTER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_scenario_artifact_is_sanitized_and_read_only():
    artifact = _artifact()
    assert artifact["schema_version"] == "demo.verified-scenario-replays.v1"
    assert artifact["classification"] == "SANITIZED_CONTROLLED_SCENARIO_REPLAY"
    assert artifact["activation"]["condition"] == "CORE_BACKLOG_PRESSURE"
    assert artifact["activation"]["state"] == "PRESENT"
    assert artifact["activation"]["total_lag_records"] == [7205, 10497, 13936]
    assert artifact["boundary"] == {
        "recorded_replay": True,
        "openai_api_called": False,
        "runtime_source_called": False,
        "runtime_write_allowed": False,
    }

    serialized = json.dumps(artifact)
    for forbidden in (
        "evidence_id",
        "diagnosis_id",
        "incident_id",
        "source_bundle_digests",
        "initial_evidence_ids",
        "fixture_digest",
        "model_response_ids",
        "cluster_context",
        "namespace",
        "raw_ref",
        "raw_sha256",
    ):
        assert forbidden not in serialized


def test_public_scenarios_preserve_verified_branching_and_results():
    scenarios = {item["scenario_id"]: item for item in _artifact()["scenarios"]}
    assert set(scenarios) == set(EXPECTED_TOOLS)
    for scenario_id, expected_tools in EXPECTED_TOOLS.items():
        scenario = scenarios[scenario_id]
        assert [item["tool_id"] for item in scenario["steps"]] == expected_tools
        assert scenario["validation"]["result"] == "VALID"
        assert scenario["usage"]["api_requests"] == 0
        assert scenario["acquisition_mode"] == "CONTROLLED_SCENARIO"
        assert all(item["result"] == "PASS" for item in scenario["branch_trace"])
        assert all(item["observed_at"].endswith("Z") for item in scenario["evidence"])
        evidence_refs = {item["evidence_ref"] for item in scenario["evidence"]}
        assert evidence_refs == {
            item["returned_evidence_ref"] for item in scenario["steps"]
        }
        for hypothesis in scenario["hypotheses"]:
            citations = set(hypothesis["supporting_evidence_refs"])
            citations.update(hypothesis["conflicting_evidence_refs"])
            assert citations <= evidence_refs

        hypothesis, status, stop = EXPECTED_RESULTS[scenario_id]
        assert scenario["hypotheses"][0]["hypothesis"] == hypothesis
        assert scenario["hypotheses"][0]["support_status"] == status
        assert scenario["stop_reason"] == stop

    telemetry = scenarios["telemetry-unavailable"]
    unavailable = telemetry["evidence"][-1]
    assert unavailable["status"] == "UNAVAILABLE"
    assert unavailable["error_code"] == "PROMETHEUS_TIMEOUT"
    assert telemetry["hypotheses"][0]["evidence_gaps"] == [
        "POSTGRES_HEALTH_TELEMETRY_UNAVAILABLE"
    ]

    worker_shortfall = scenarios["worker-replica-shortfall"]
    replica = worker_shortfall["evidence"][1]
    keda = worker_shortfall["evidence"][2]
    assert replica["observed_at"] == keda["observed_at"]
    assert replica["summary"]["current_replicas"] == 4
    assert replica["summary"]["ready_replicas"] == 2
    assert replica["summary"]["available_replicas"] == 2
    assert keda["summary"]["current_replicas"] == 4


def test_public_demo_renders_and_replays_scenarios_without_new_calls():
    demo = DEMO_PATH.read_text(encoding="utf-8")
    assert 'id="ai-scenario-replay"' in demo
    assert 'id="scenario-options"' in demo
    assert 'id="replay-selected-scenario"' in demo
    assert 'fetch("./verified-scenario-replays.json"' in demo
    assert "function renderScenarioReplay()" in demo
    assert "function replaySelectedScenario()" in demo
    assert "function selectScenario(scenarioId, replay = false)" in demo
    assert "function renderScenarioComparison()" in demo
    assert 'selectScenario(button.dataset.scenarioId);' in demo
    assert 'selectScenario(button.dataset.scenarioId, true)' not in demo
    assert demo.index('id="ai-scenario-replay"') < demo.index(
        'class="investigation-entry investigation-archive"'
    )
    assert '<details class="investigation-entry investigation-archive">' in demo
    assert '<details class="investigation-entry investigation-archive" open>' not in demo
    assert 'class="scenario-awaiting"' in demo
    assert ".scenario-awaiting[hidden] {\n      display: none;" in demo
    assert "scenario-replay-node" in demo
    assert ".scenario-replay-node {\n      display: none;" in demo
    assert ".scenario-replay-node.replay-visible" in demo
    assert "previousScenarioId = activeScenarioId" in demo
    assert 'id="scenario-comparison"' in demo
    assert "scenarioBranchChanged" in demo
    assert "scenarioOutcomeChanged" in demo
    assert "scenarioGroundingValidator" in demo
    assert "scenarioAbstained" in demo
    assert "scenarioWhyTool" in demo
    assert 'class="scenario-why"' in demo
    assert 'id="scenario-trace-summary"' in demo
    assert "scenarioObservedAtLabel" in demo
    assert 'class="scenario-observed-at"' in demo
    assert "scenarioReplicaAvailabilityNote" in demo
    assert "scenarioKedaReplicaNote" in demo
    assert "관측 분류" in demo
    assert "AI 가설 판단" in demo
    assert "CHECK_KEDA_ACTIVITY" in demo
    assert 'id="event-processing-demo-title"' in demo
    assert "worker-db-path-pressure" in demo
    assert "worker-replica-shortfall" in demo
    assert "previousScenarioId = dbPathScenario?.scenario_id" in demo
    assert "activeScenarioId = workerShortfallScenario?.scenario_id" in demo
    assert 'grid-template-columns: minmax(0, 1fr);' in demo
    replay_function = demo.split("function replaySelectedScenario()", 1)[1].split(
        "function selectScenario", 1
    )[0]
    assert "fetch(" not in replay_function
    assert "scrollIntoView" in replay_function
    assert 'awaiting.hidden = true' in replay_function
    assert 'classList.add("replay-visible", "replay-active")' in replay_function
    load_function = demo.split("async function loadVerifiedScenarioReplays()", 1)[1].split(
        "function evidenceDetailRows", 1
    )[0]
    assert "openai_api_called !== false" in load_function
    assert "runtime_source_called !== false" in load_function
    assert "runtime_write_allowed !== false" in load_function
    assert 'fetch("./verified-incident-replay.json"' in demo


def test_exporter_rejects_unapproved_fields_and_fabricated_citations():
    exporter = _load_exporter()
    evidence = {
        "tool_id": "get_worker_stage_latency",
        "summary": {
            "worker_stage_mean_ms": 14.3,
            "scenario_baseline_ms": 3.2,
            "semantic_flag": "ABOVE_SCENARIO_BASELINE",
            "raw_query": "must-not-pass",
        },
    }
    with pytest.raises(ValueError, match="unapproved summary fields"):
        exporter._safe_summary(evidence)
    with pytest.raises(ValueError, match="outside public tool trace"):
        exporter._map_citations(["fabricated-evidence-id"], {})
