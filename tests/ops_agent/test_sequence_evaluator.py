from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from ops_agent import cli
from ops_agent.endpoint_provenance import endpoint_provenance
from ops_agent.evaluation_models import ConditionName, ConditionState
from ops_agent.evaluator import evaluate_bundle
from ops_agent.sequence_evaluator import evaluate_bundle_sequence
from tests.ops_agent.synthetic_evidence import (
    build_positive_sequence,
    set_measurement,
    shift_bundle,
)


ROOT = Path(__file__).resolve().parents[2]
ADVERSARIAL = (
    ROOT
    / "ops_agent"
    / "fixtures"
    / "sequences"
    / "core_backlog_adversarial_v1.json"
)
@pytest.fixture
def positive_sequence() -> list[dict]:
    return build_positive_sequence()


def test_calibrated_three_capture_sequence_is_present(positive_sequence) -> None:
    evaluation = evaluate_bundle_sequence(positive_sequence)
    core = evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]

    assert evaluation.schema_version == "ops.conditions.v2"
    assert evaluation.policy.policy_version == "local-ha.conditions.v2"
    assert core.state == ConditionState.PRESENT
    assert core.facts["matched_activation_windows"] == [[0, 1, 2]]
    assert core.facts["activation_policy"] == {
        "consecutive_capture_count": 3,
        "total_lag_floor_records": 7000,
        "lag_slope_floor_records_per_second": 100.0,
        "require_increase_across_both_transitions": True,
        "produce_minus_committed_role": "arithmetic_consistency_only",
        "keda_worker_stage_role": "optional_context_only",
        "recovery_or_clearing_evaluated": False,
    }
    assert all(
        item.rate_arithmetic_consistent is True
        for item in evaluation.capture_observations
    )


def test_v1_single_bundle_behavior_is_unchanged(positive_sequence) -> None:
    result = evaluate_bundle(positive_sequence[-1]).conditions[
        ConditionName.CORE_BACKLOG_PRESSURE
    ]

    assert result.state == ConditionState.UNKNOWN
    assert "PRESSURE_POLICY_UNCALIBRATED" in result.reason_codes


def test_optional_worker_and_keda_context_do_not_gate_present(
    positive_sequence,
) -> None:
    optional_metrics = {
        "kubernetes_worker_deployment_observation",
        "kubernetes_worker_scaled_object_observation",
        "messaging_worker_stage_latency_seconds",
        "messaging_worker_stage_latency_seconds_count",
        "messaging_worker_stage_latency_seconds_sum",
        "messaging_worker_stage_latency_seconds_bucket",
    }
    for bundle in positive_sequence[:-1]:
        bundle["evidence"] = [
            item
            for item in bundle["evidence"]
            if item["metric"]["name"] not in optional_metrics
        ]

    evaluation = evaluate_bundle_sequence(positive_sequence)

    assert evaluation.conditions[
        ConditionName.CORE_BACKLOG_PRESSURE
    ].state == ConditionState.PRESENT
    assert evaluation.capture_observations[0].worker_context == {
        "desired_replicas": None,
        "current_replicas": None,
        "ready_replicas": None,
        "available_replicas": None,
        "observed_generation": None,
    }


def _apply_case(sequence: list[dict], case: dict) -> list[dict]:
    operation = case["operation"]
    if operation == "set_capture_measurements":
        for bundle, lag, slope in zip(
            sequence,
            case["latest_total_lag_records"],
            case["lag_slope_records_per_second"],
        ):
            set_measurement(bundle, lag, slope)
    elif operation == "truncate_captures":
        sequence = sequence[: case["capture_count"]]
    elif operation == "stale_required_lag":
        bundle = sequence[case["capture_index"]]
        lag = next(
            item
            for item in bundle["evidence"]
            if item["metric"]["name"] == "kafka_consumergroup_lag"
        )
        lag["freshness"].update(
            {"status": "STALE", "age_seconds": 16.0, "max_age_seconds": 15.0}
        )
    elif operation == "remove_required_partition":
        bundle = sequence[case["capture_index"]]
        bundle["evidence"] = [
            item
            for item in bundle["evidence"]
            if not (
                item["metric"]["name"] == "kafka_consumergroup_lag"
                and item["labels"].get("partition") == case["partition"]
            )
        ]
    elif operation == "change_consumer_group":
        sequence[case["capture_index"]]["scope"]["consumer_group"] = case[
            "consumer_group"
        ]
    elif operation == "reorder_captures":
        sequence = [sequence[index] for index in case["order"]]
    else:
        raise AssertionError(f"unknown fixture operation: {operation}")
    return sequence


@pytest.mark.parametrize(
    "case",
    json.loads(ADVERSARIAL.read_text(encoding="utf-8"))["cases"],
    ids=lambda case: case["name"],
)
def test_adversarial_sequences_never_present(positive_sequence, case) -> None:
    evaluation = evaluate_bundle_sequence(
        _apply_case(deepcopy(positive_sequence), case)
    )
    core = evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]

    assert core.state == ConditionState.UNKNOWN
    assert case["expected_reason"] in core.reason_codes
    assert core.facts["matched_activation_windows"] == []


def test_offset_rate_gap_is_only_an_arithmetic_consistency_gate(
    positive_sequence,
) -> None:
    end = next(
        item
        for item in positive_sequence[1]["evidence"]
        if item["metric"]["name"] == "kafka_topic_partition_current_offset"
        and item["labels"].get("partition") == "0"
    )
    end["metric"]["value"][-1]["value"] = str(
        int(end["metric"]["value"][-1]["value"]) + 1
    )

    evaluation = evaluate_bundle_sequence(positive_sequence)
    core = evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]

    assert core.state == ConditionState.UNKNOWN
    assert "SEQUENCE_REQUIRED_EVIDENCE_UNUSABLE" in core.reason_codes


def test_evaluation_id_binds_ordered_digests_and_capture_timing(
    positive_sequence,
) -> None:
    first = evaluate_bundle_sequence(positive_sequence)
    shifted = deepcopy(positive_sequence)
    shift_bundle(shifted[-1], 1, 2)
    shifted[-1]["bundle_id"] = "synthetic-sequence-2-shifted"
    second = evaluate_bundle_sequence(shifted)

    assert first.evaluation_id != second.evaluation_id
    assert [
        item.source_bundle_sha256 for item in first.source_bundles
    ] != [item.source_bundle_sha256 for item in second.source_bundles]
    assert (
        first.source_bundles[-1].collection_completed_at
        != second.source_bundles[-1].collection_completed_at
    )


def test_changed_endpoint_configuration_identity_makes_sequence_unknown(
    positive_sequence,
) -> None:
    endpoint = next(
        item
        for item in positive_sequence[1]["evidence"]
        if item["metric"]["name"] == "source_endpoint_identity"
        and item["labels"].get("collector_source") == "prometheus"
    )
    endpoint["metric"]["value"] = endpoint_provenance(
        base_url="http://127.0.0.1:19090/prometheus",
        host_header="localhost",
        configuration_source="operator_override",
    )

    evaluation = evaluate_bundle_sequence(positive_sequence)
    core = evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE]

    assert core.state == ConditionState.UNKNOWN
    assert "SEQUENCE_SOURCE_IDENTITY_MISMATCH" in core.reason_codes


def test_sequence_output_mutation_invalidates_evaluation_id(
    positive_sequence,
) -> None:
    evaluation = evaluate_bundle_sequence(positive_sequence)
    evaluation.conditions[ConditionName.CORE_BACKLOG_PRESSURE].facts[
        "matched_activation_windows"
    ] = []

    with pytest.raises(ValueError, match="evaluation_id"):
        evaluation.model_dump(mode="json")


def test_cli_evaluate_sequence_preserves_input_order(
    positive_sequence,
    tmp_path,
    capsys,
) -> None:
    inputs: list[Path] = []
    for index, bundle in enumerate(positive_sequence):
        path = tmp_path / f"sample-{index}.json"
        path.write_text(json.dumps(bundle), encoding="utf-8")
        inputs.append(path)
    output = tmp_path / "conditions-v2.json"

    exit_code = cli.main(
        [
            "evaluate-sequence",
            "--input",
            *(str(path) for path in inputs),
            "--output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == output.as_posix()
    assert payload["schema_version"] == "ops.conditions.v2"
    assert payload["conditions"]["CORE_BACKLOG_PRESSURE"]["state"] == "PRESENT"
    assert [item["sequence_index"] for item in payload["source_bundles"]] == [
        0,
        1,
        2,
    ]


def test_cli_evaluate_sequence_rejects_input_overwrite(
    positive_sequence,
    tmp_path,
) -> None:
    source = tmp_path / "sample.json"
    source.write_text(json.dumps(positive_sequence[0]), encoding="utf-8")

    with pytest.raises(ValueError, match="must not overwrite"):
        cli.main(
            [
                "evaluate-sequence",
                "--input",
                str(source),
                "--output",
                str(source),
            ]
        )


def test_cli_evaluate_sequence_bounds_total_input_bytes(
    positive_sequence,
    monkeypatch,
    tmp_path,
) -> None:
    inputs: list[Path] = []
    for index, bundle in enumerate(positive_sequence):
        path = tmp_path / f"sample-{index}.json"
        path.write_text(json.dumps(bundle), encoding="utf-8")
        inputs.append(path)
    monkeypatch.setattr(cli, "_MAX_SEQUENCE_INPUT_BYTES", 1)

    with pytest.raises(ValueError, match="64 MiB"):
        cli.main(
            [
                "evaluate-sequence",
                "--input",
                *(str(path) for path in inputs),
            ]
        )
