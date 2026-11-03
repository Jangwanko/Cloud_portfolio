"""Mandatory investigation coverage without fixture answers or hidden tool execution."""

import json

import pytest

from ops_agent.diagnosis_agent import ModelResponse
from ops_agent.diagnosis_quality_v3 import score_v3
from ops_agent.diagnosis_scenarios import ControlledScenarioRegistry, load_scenario_catalog
from ops_agent.investigation_gate_v3 import PRIMARY_TOOLS, pending_investigation
from ops_agent.scenario_agent_v3 import run_scenario_v3
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG
from scripts.run_diagnosis_v3_pilot import DEFAULT_RUBRIC_V3
from scripts.build_diagnosis_v3_novel_cases import build_novel_cases
from tests.ops_agent.test_scenario_agent_v3 import OfflineClient


def test_three_primary_tools_pending_before_queries():
    assert pending_investigation([]) == list(PRIMARY_TOOLS)


@pytest.mark.parametrize("fixture_id,expected", [("novel-01", ["get_keda_status"]),
    ("novel-02", []), ("novel-03", ["get_keda_status"]), ("novel-04", [])])
def test_fresh_deficit_needs_keda_but_stale_deficit_does_not(fixture_id, expected):
    catalog, _ = build_novel_cases()
    registry = ControlledScenarioRegistry(catalog=catalog, fixture_id=fixture_id)
    evidence = [registry.execute(tool) for tool in PRIMARY_TOOLS]
    assert pending_investigation(evidence) == expected
    if expected:
        evidence.append(registry.execute("get_keda_status"))
        assert not pending_investigation(evidence)


@pytest.mark.parametrize("tools", [[], list(PRIMARY_TOOLS[:2]), list(PRIMARY_TOOLS)])
def test_early_decision_rejected_without_repair_or_budget_extension(tools):
    catalog = load_scenario_catalog(DEFAULT_CATALOG)
    client = OfflineClient(tools=tools)
    run = run_scenario_v3(catalog=catalog, fixture_id="worker-replica-shortfall", client=client, model="offline")
    assert not run["contract_pass"] and not run["repair_used"]
    assert run["failure_code"] == "V3_MINIMUM_INVESTIGATION_INCOMPLETE"
    assert len(client.requests) == len(tools) + 1
    assert run["decisions"][0]["failure_code"] == run["failure_code"]


def test_model_can_choose_different_required_tool_order():
    catalog = load_scenario_catalog(DEFAULT_CATALOG)
    rubric = json.loads(DEFAULT_RUBRIC_V3.read_bytes())
    order = ["get_worker_replica_status", "get_keda_status", "get_postgres_health", "get_worker_stage_latency"]
    client = OfflineClient(tools=order)
    run = run_scenario_v3(catalog=catalog, fixture_id="worker-replica-shortfall", client=client, model="offline")
    assert run["tool_calls"] == order and score_v3(run, catalog=catalog, rubric=rubric)["joint_pass"]
    assert all(request["tool_choice"] == "required" for request in client.requests[:-1])
    assert "tools" not in client.requests[-1]
    assert all(not item["pending_tools"] for item in run["investigation_audit"][-1:])


def test_optional_tool_is_not_executable_while_primary_coverage_pending():
    catalog = load_scenario_catalog(DEFAULT_CATALOG)
    client = OfflineClient(tools=["get_partition_lag"])
    run = run_scenario_v3(catalog=catalog, fixture_id="worker-db-path-pressure", client=client, model="offline")
    assert not run["tool_calls"] and run["failure_code"] == "V3_INVALID_TOOL_CALL"
    assert {tool["name"] for tool in client.requests[0]["tools"]} == set(PRIMARY_TOOLS)


def test_satisfied_gate_allows_final_without_fourth_tool():
    catalog = load_scenario_catalog(DEFAULT_CATALOG)
    client = OfflineClient(tools=list(PRIMARY_TOOLS))
    run = run_scenario_v3(catalog=catalog, fixture_id="worker-db-path-pressure", client=client, model="offline")
    assert run["contract_pass"] and len(client.requests) == 4
    assert client.requests[-1]["tool_choice"] == "auto"


def test_unavailable_queries_count_as_coverage_and_never_become_positive_observations():
    catalog, rubric = build_novel_cases()
    client = OfflineClient()
    run = run_scenario_v3(catalog=catalog, fixture_id="novel-03", client=client, model="offline")
    assert score_v3(run, catalog=catalog, rubric=rubric)["joint_pass"]
    assert [item["signal"] for item in run["decision"]["observations"]] == ["WORKER_REPLICA_SHORTFALL"]
    assert len(client.requests) == 5


def test_duplicate_acquisition_is_not_silently_accepted():
    catalog = load_scenario_catalog(DEFAULT_CATALOG)
    item = ControlledScenarioRegistry(catalog=catalog, fixture_id="worker-db-path-pressure").execute(PRIMARY_TOOLS[0])
    with pytest.raises(ValueError, match="duplicate"):
        pending_investigation([item, item])
