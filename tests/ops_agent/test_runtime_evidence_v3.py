from copy import deepcopy
import json

import pytest

from ops_agent.diagnosis_quality_v3 import fixed_decision_v3
from ops_agent.diagnosis_scenarios import ControlledScenarioRegistry, load_scenario_catalog
from ops_agent.evaluation_models import canonical_sha256
from ops_agent.runtime_agent_v3 import run_runtime_v3
from ops_agent.runtime_evidence_v3 import FrozenRuntimeRegistry, RUNTIME_TOOLS, verify_runtime_inputs
from ops_agent.scenario_agent_v3 import run_scenario_v3
from ops_agent.sequence_evaluator import evaluate_bundle_sequence
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG
from scripts.run_runtime_diagnosis_v3 import run_runtime_pair
from tests.ops_agent.synthetic_evidence import build_positive_sequence, set_measurement
from tests.ops_agent.test_scenario_agent_v3 import OfflineClient


def registry(sequence=None):
    sequence = build_positive_sequence() if sequence is None else sequence
    return FrozenRuntimeRegistry(bundles=sequence, evaluation=evaluate_bundle_sequence(sequence))


def metric(sequence, name):
    return next(item for item in sequence[-1]["evidence"] if item["metric"]["name"] == name)


def test_real_provenance_identity_and_missing_runtime_baseline():
    source = registry()
    activation = source.activation
    assert "fixture_id" not in json.dumps(activation)
    assert activation["acquisition_mode"] == "FROZEN_PROJECTED"
    for tool in RUNTIME_TOOLS:
        item = source.execute(tool)
        assert item.provenance.acquisition_mode.value == "FROZEN_PROJECTED"
        assert item.provenance.source_bundle_digests == [activation["projection_bundle_sha256"]]
        assert item.summary["freshness_basis"] == "FROZEN_COLLECTION_TIME"
        if tool == "get_worker_stage_latency":
            assert item.summary["baseline_status"] == "UNAVAILABLE"
            assert "SCENARIO_BASELINE" not in item.summary["semantic_flag"]
            assert "scenario_baseline_ms" not in item.summary
        if tool == "get_postgres_health":
            assert item.summary["semantic_flag"] == "POSTGRES_PATH_HEALTHY"


def test_latest_matched_capture_shortfall_with_unproven_grace():
    sequence = build_positive_sequence()
    metric(sequence, "kubernetes_worker_deployment_observation")["metric"]["value"].update(
        available_replicas=1, current_replicas=2)
    source = registry(sequence)
    item = source.execute("get_worker_replica_status")
    assert item.summary["semantic_flag"] == "WORKER_CAPACITY_SHORTFALL"
    assert item.summary["condition_state"] == "UNKNOWN"
    assert item.summary["grace_proven"] is False
    assert item.status.value == "OK"
    fixed = fixed_decision_v3(activation=source.activation, evidence=[item])
    assert fixed.hypotheses[0].hypothesis.value == "WORKER_CAPACITY_SHORTFALL_SUSPECTED"


@pytest.mark.parametrize("mutation", ["null_available", "generation", "missing", "stale"])
def test_invalid_replica_never_becomes_healthy_or_positive(mutation):
    sequence = build_positive_sequence()
    item = metric(sequence, "kubernetes_worker_deployment_observation")
    if mutation == "null_available":
        item["metric"]["value"]["available_replicas"] = None
    elif mutation == "generation":
        item["metric"]["value"]["observed_generation"] += 1
    elif mutation == "missing":
        sequence[-1]["evidence"].remove(item)
    else:
        item["freshness"].update(status="STALE", age_seconds=31, max_age_seconds=30)
    observed = registry(sequence).execute("get_worker_replica_status")
    assert observed.status.value == "UNAVAILABLE"
    assert "semantic_flag" not in observed.summary


def test_postgres_reason_component_conflict_is_unavailable():
    sequence = build_positive_sequence()
    metric(sequence, "application_postgres_runtime_observation")["metric"]["value"]["max_replication_delay_bytes"] = 1048577
    item = registry(sequence).execute("get_postgres_health")
    assert item.status.value == "UNAVAILABLE"
    assert "semantic_flag" not in item.summary


def test_bound_to_original_order_digests_and_recomputed_activation():
    sequence = build_positive_sequence()
    evaluation = evaluate_bundle_sequence(sequence)
    altered = deepcopy(sequence)
    metric(altered, "kubernetes_worker_deployment_observation")["metric"]["value"]["available_replicas"] = 1
    with pytest.raises(ValueError, match="canonical source replay"):
        verify_runtime_inputs(altered, evaluation)
    with pytest.raises(ValueError):
        verify_runtime_inputs(sequence[::-1], evaluation)


def test_detaches_mutable_caller_data():
    sequence = build_positive_sequence()
    source = registry(sequence)
    expected = registry(sequence).execute("get_worker_replica_status").model_dump(mode="json")
    metric(sequence, "kubernetes_worker_deployment_observation")["metric"]["value"]["available_replicas"] = 0
    source.activation["evidence_ids"].clear()
    assert source.execute("get_worker_replica_status").model_dump(mode="json") == expected


def test_repeat_arbitrary_and_unoffered_tools_rejected():
    source = registry()
    source.execute("get_postgres_health")
    assert "get_postgres_health" not in {tool["name"] for tool in source.function_tools()}
    for tool in ("get_postgres_health", "shell", "get_partition_lag"):
        with pytest.raises(ValueError):
            source.execute(tool)


def test_absent_live_flag_does_not_load_key_or_invoke_client(tmp_path, monkeypatch):
    sequence = build_positive_sequence()
    for bundle in sequence:
        set_measurement(bundle, 0, 0)
    monkeypatch.setattr("ops_agent.diagnosis_agent.load_openai_configuration",
        lambda _: pytest.fail("must gate before loading key"))
    client = OfflineClient()
    report = run_runtime_pair(bundles=sequence, evaluation=evaluate_bundle_sequence(sequence),
        output_dir=tmp_path / "skip", live=True, client=client)
    assert report["status"] == "SKIPPED_NO_ACTIVATION"
    assert report["client_request_count"] == 0 and client.requests == []
    assert report["fixed_runbook_status"] == "NOT_RUN"


def test_positive_prepare_requires_explicit_model_opt_in(tmp_path):
    sequence = build_positive_sequence()
    destination = tmp_path / "prepare"
    report = run_runtime_pair(bundles=sequence, evaluation=evaluate_bundle_sequence(sequence), output_dir=destination)
    assert report["status"] == "PREPARED" and report["model_status"] == "NOT_RUN"
    assert report["fixed_runbook_status"] == "CONTRACT_VALID"
    assert report["comparison"] == "NOT_EVALUATED"
    assert (destination / "projection.json").exists()
    with pytest.raises(FileExistsError):
        run_runtime_pair(bundles=sequence, evaluation=evaluate_bundle_sequence(sequence), output_dir=destination)


def test_scripted_runtime_loop_and_pair_is_not_accuracy_score(tmp_path):
    sequence = build_positive_sequence()
    client = OfflineClient()
    report = run_runtime_pair(bundles=sequence, evaluation=evaluate_bundle_sequence(sequence),
        output_dir=tmp_path / "scripted", client=client, model="scripted-offline")
    assert report["model_status"] == "CONTRACT_VALID"
    assert report["client_request_count"] == 5
    assert report["execution_mode"] == "SCRIPTED_TEST"
    assert report["comparison"]["interpretation"] == "AGREEMENT_ONLY"
    assert report["causal_truth_verified"] is False


def test_runtime_repair_stays_bounded():
    client = OfflineClient(mutate=lambda decision: decision.update(conclusion="SUPPORTED_CANDIDATES"), repair=True)
    run = run_runtime_v3(registry=registry(), client=client, model="offline")
    assert run["contract_pass"] and run["repair_used"] == 1
    assert len(client.requests) == 6
    assert "tools" not in client.requests[-1]


@pytest.mark.parametrize("tools", [["shell"], [], ["get_postgres_health", "get_postgres_health"]])
def test_runtime_call_gate_blocks_unoffered_repeat_and_early_final(tools):
    run = run_runtime_v3(registry=registry(), client=OfflineClient(tools=tools), model="offline")
    assert not run["contract_pass"]
    assert len(run["tool_calls"]) <= 4


@pytest.mark.parametrize("index", range(4))
def test_runtime_driver_wire_parity_with_frozen_controlled_loop(index):
    catalog = load_scenario_catalog(DEFAULT_CATALOG)
    fixture_id = catalog.scenarios[index].fixture_id
    class Registry(ControlledScenarioRegistry):
        @property
        def activation(self):
            return catalog.activation
    first, second = OfflineClient(), OfflineClient()
    expected = run_scenario_v3(catalog=catalog, fixture_id=fixture_id, client=first, model="offline")
    actual = run_runtime_v3(registry=Registry(catalog=catalog, fixture_id=fixture_id), client=second, model="offline")
    assert first.requests == second.requests
    for field in ("decision", "acquired_evidence", "investigation_audit", "citation_selection_audit", "repair_used"):
        assert actual[field] == expected[field]
