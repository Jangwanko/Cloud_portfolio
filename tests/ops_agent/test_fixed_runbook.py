"""Check the fixed baseline's information boundary, budget and paired reporting."""

from copy import deepcopy
import json
import socket

import pytest

from ops_agent.diagnosis_quality import QualityAttempt, QualityRubric, score_attempt
from ops_agent.diagnosis_scenarios import ControlledScenarioRegistry, load_scenario_catalog
from ops_agent.fixed_runbook import TOOL_ORDER, decide, run_fixed_runbook
from scripts.compare_diagnosis_runbook import compare
from scripts.evaluate_diagnosis_quality import DEFAULT_CATALOG, DEFAULT_REPLAY, DEFAULT_RUBRIC


@pytest.fixture
def catalog():
    return load_scenario_catalog(DEFAULT_CATALOG)


class ToolOnlyRegistry:
    """Give the runner only tools; access to scenario/expected metadata would fail."""

    def __init__(self, delegate):
        self._execute = delegate.execute
        self.tool_ids = delegate.tool_ids
        self.called = []

    def execute(self, tool):
        self.called.append(tool)
        return self._execute(tool)


@pytest.mark.parametrize("fixture_id,expected", [
    ("worker-db-path-pressure", "WORKER_PATH_PRESSURE_SUSPECTED"),
    ("worker-replica-shortfall", "WORKER_CAPACITY_SHORTFALL_SUSPECTED"),
    ("postgres-path-degradation", "POSTGRES_PATH_DEGRADED_SUSPECTED"),
    ("telemetry-unavailable", "INSUFFICIENT_EVIDENCE"),
])
def test_checklist_uses_fixed_order_without_answer_metadata(catalog, fixture_id, expected):
    registry = ToolOnlyRegistry(ControlledScenarioRegistry(catalog=catalog, fixture_id=fixture_id))
    run = run_fixed_runbook(activation=catalog.activation, registry=registry)
    assert registry.called == list(TOOL_ORDER)
    assert [step.tool_id for step in run.steps] == list(TOOL_ORDER)
    assert run.hypotheses[0].hypothesis.value == expected
    assert run.usage.api_requests == run.usage.total_tokens == 0
    assert run.output_repairs_used == 0
    rubric = QualityRubric.model_validate_json(DEFAULT_RUBRIC.read_bytes())
    score = score_attempt(QualityAttempt(
        attempt_id="baseline", fixture_id=fixture_id, execution_kind="scripted",
        run=run.model_dump(mode="json"),
    ), catalog=catalog, rubric=rubric)
    assert score["quality_pass"]
    assert score["tool_call_count"] == 4


def test_budget_rejected_before_tool_execution(catalog):
    registry = ToolOnlyRegistry(ControlledScenarioRegistry(
        catalog=catalog, fixture_id="worker-db-path-pressure"
    ))
    with pytest.raises(ValueError, match="four-call"):
        run_fixed_runbook(activation=catalog.activation, registry=registry, max_tool_calls=3)
    assert registry.called == []


def test_stale_postgres_evidence_causes_abstention(catalog):
    registry = ControlledScenarioRegistry(catalog=catalog, fixture_id="postgres-path-degradation")
    evidence = [registry.execute(tool) for tool in TOOL_ORDER]
    postgres = evidence[1]
    evidence[1] = postgres.model_copy(update={
        "freshness": postgres.freshness.model_copy(update={
            "status": type(postgres.freshness.status)("STALE")
        })
    })
    decision = decide(evidence)
    assert decision.hypotheses[0].hypothesis.value == "INSUFFICIENT_EVIDENCE"
    assert decision.hypotheses[0].evidence_gaps


def test_conflicting_signals_do_not_force_a_supported_cause(catalog):
    registry = ControlledScenarioRegistry(catalog=catalog, fixture_id="worker-db-path-pressure")
    evidence = [registry.execute(tool) for tool in TOOL_ORDER]
    replica = evidence[2]
    evidence[2] = replica.model_copy(update={"summary": {
        **replica.summary, "semantic_flag": "WORKER_CAPACITY_SHORTFALL",
    }})
    assert decide(evidence).hypotheses[0].hypothesis.value == "INSUFFICIENT_EVIDENCE"


def test_replica_shortfall_with_degraded_postgres_is_ambiguous(catalog):
    registry = ControlledScenarioRegistry(catalog=catalog, fixture_id="worker-replica-shortfall")
    evidence = [registry.execute(tool) for tool in TOOL_ORDER]
    postgres = evidence[1]
    evidence[1] = postgres.model_copy(update={"summary": {
        **postgres.summary, "semantic_flag": "POSTGRES_PATH_DEGRADED",
    }})
    assert decide(evidence).hypotheses[0].hypothesis.value == "INSUFFICIENT_EVIDENCE"


def test_comparison_network_free_paired_counts_and_identical_observations(tmp_path, monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("baseline comparison attempted network access")
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    output = tmp_path / "comparison"
    kwargs = dict(replay_path=DEFAULT_REPLAY, catalog_path=DEFAULT_CATALOG,
                  rubric_path=DEFAULT_RUBRIC, output_dir=output)
    assert compare(**kwargs) == 0
    result = json.loads((output / "comparison.json").read_bytes())
    fixed = result["arms"]["fixed_runbook"]
    scripted = result["arms"]["scripted_investigation"]
    assert fixed["attempt_count"] == scripted["attempt_count"] == 4
    assert fixed["quality_success_rate"] == scripted["quality_success_rate"] == 1
    assert fixed["tool_call_count_total_known"] == 16
    assert scripted["tool_call_count_total_known"] == 10
    assert fixed["extra_tool_call_count_total_known"] == 6
    assert scripted["extra_tool_call_count_total_known"] == 0
    assert scripted["telemetry"]["elapsed_seconds"]["known_attempt_count"] == 0
    for index in range(1, 5):
        fixed_run = json.loads((output / "fixed-runbook" / f"attempt-{index:03d}.json").read_bytes())["attempt"]["run"]
        scripted_run = json.loads((output / "scripted-investigation" / f"attempt-{index:03d}.json").read_bytes())["attempt"]["run"]
        baseline_by_tool = {item["tool_id"]: item for item in fixed_run["additional_evidence"]}
        assert all(item == baseline_by_tool[item["tool_id"]] for item in scripted_run["additional_evidence"])
    assert json.loads((output / "manifest.json").read_bytes())["status"] == "completed"
    with pytest.raises(FileExistsError):
        compare(**kwargs)


def test_comparison_rejects_unequal_budgets_before_output(tmp_path):
    source = json.loads(DEFAULT_REPLAY.read_bytes())
    source["scenarios"][0]["run"]["policy"]["max_tool_calls"] = 8
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    output = tmp_path / "comparison"
    with pytest.raises(ValueError, match="same four"):
        compare(replay_path=path, catalog_path=DEFAULT_CATALOG,
                rubric_path=DEFAULT_RUBRIC, output_dir=output)
    assert not output.exists()


def test_comparison_keeps_failed_source_attempt_in_both_denominators(tmp_path):
    source = deepcopy(json.loads(DEFAULT_REPLAY.read_bytes()))
    source["scenarios"][0]["run"]["diagnosis_id"] = "0" * 64
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    output = tmp_path / "comparison"
    assert compare(replay_path=path, catalog_path=DEFAULT_CATALOG,
                   rubric_path=DEFAULT_RUBRIC, output_dir=output) == 1
    arms = json.loads((output / "comparison.json").read_bytes())["arms"]
    assert arms["fixed_runbook"]["attempt_count"] == 4
    assert arms["scripted_investigation"]["attempt_count"] == 4
    assert arms["scripted_investigation"]["final_contract_failure_count"] == 1
    assert arms["scripted_investigation"]["quality_success_rate"] == 0.75
