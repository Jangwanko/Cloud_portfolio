from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from ops_agent import cli
from ops_agent.endpoint_provenance import endpoint_provenance
from ops_agent.evaluation_models import ConditionName, ConditionState, canonical_sha256
from ops_agent.recovery_evaluator import evaluate_recovery
from ops_agent.recovery_models import (
    LowLagEvidencePolicy,
    RecoveryEvaluation,
    RecoveryState,
)
from ops_agent.recovery_policies import load_recovery_policy
from ops_agent.sequence_evaluator import evaluate_bundle_sequence


ROOT = Path(__file__).resolve().parents[2]
CAPTURE = (
    ROOT
    / "results"
    / "ops-agent"
    / "live-baseline"
    / "no-backlog-20260812.json"
)
KAFKA_METRICS = {
    "kafka_topic_partition_current_offset",
    "kafka_consumergroup_current_offset",
    "kafka_consumergroup_lag",
}


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _add_endpoint_identity(bundle: dict, collector_source: str) -> None:
    base_url = (
        "http://127.0.0.1/prometheus"
        if collector_source == "prometheus"
        else "http://127.0.0.1"
    )
    value = endpoint_provenance(
        base_url=base_url,
        host_header="localhost",
        configuration_source="policy",
    )
    completed_at = bundle["collection"]["completed_at"]
    bundle["evidence"].append(
        {
            "evidence_id": f"collector_configuration.source_endpoint_identity.{collector_source}",
            "status": "OK",
            "source": "collector_configuration",
            "tool_id": "collector.endpoint.identity.v1",
            "source_timestamp": completed_at,
            "collected_at": completed_at,
            "freshness": {
                "status": "FRESH",
                "age_seconds": 0.0,
                "max_age_seconds": 1.0,
                "basis": "collector_time_endpoint_configuration",
            },
            "metric": {
                "name": "source_endpoint_identity",
                "value": value,
                "unit": None,
                "window": None,
                "aggregation": None,
                "sample_count": None,
            },
            "labels": {"collector_source": collector_source},
            "coverage": {
                "expected_count": None,
                "observed_count": None,
                "complete": None,
                "expected_items": [],
                "observed_items": [],
                "missing_items": [],
                "extra_items": [],
                "notes": None,
            },
            "semantic": {
                "type": "effective_source_endpoint_identity",
                "notes": "Synthetic fixed endpoint identity without credentials.",
                "is_db_commit_rate": None,
                "flags": ["configuration_provenance", "credentials_excluded"],
            },
            "raw_ref": None,
            "raw_sha256": None,
            "error": None,
        }
    )


def _shift_bundle(bundle: dict, *, seconds: int, sample_index: int) -> None:
    delta = timedelta(seconds=seconds)
    bundle["bundle_id"] = f"phase4-test-bundle-{sample_index:03d}"
    bundle["incident_id"] = f"phase4-test-sample-{sample_index:03d}"
    for key in ("started_at", "completed_at"):
        bundle["collection"][key] = _format_timestamp(
            _parse_timestamp(bundle["collection"][key]) + delta
        )
    for item in bundle["evidence"]:
        item["collected_at"] = _format_timestamp(
            _parse_timestamp(item["collected_at"]) + delta
        )
        if item["source_timestamp"] is not None:
            item["source_timestamp"] = _format_timestamp(
                _parse_timestamp(item["source_timestamp"]) + delta
            )
        value = item["metric"]["value"]
        if isinstance(value, list):
            for sample in value:
                if isinstance(sample, dict) and "timestamp" in sample:
                    sample["timestamp"] += seconds


def _set_measurement(
    bundle: dict,
    *,
    latest_lag: int,
    slope: int,
    produce_rate: int,
    offset_base: int,
) -> None:
    committed_rate = produce_rate - slope
    first_lag = latest_lag - slope * 60
    assert committed_rate >= 0 and first_lag >= 0
    by_key = {
        (item["metric"]["name"], item["labels"].get("partition")): item
        for item in bundle["evidence"]
        if item["metric"]["name"] in KAFKA_METRICS
    }
    for partition in map(str, range(8)):
        end = by_key[("kafka_topic_partition_current_offset", partition)]
        committed = by_key[("kafka_consumergroup_current_offset", partition)]
        lag = by_key[("kafka_consumergroup_lag", partition)]
        partition_base = offset_base + int(partition) * 1000
        for sample_index, (end_sample, committed_sample, lag_sample) in enumerate(
            zip(
                end["metric"]["value"],
                committed["metric"]["value"],
                lag["metric"]["value"],
            )
        ):
            if partition == "0":
                lag_value = first_lag + slope * 5 * sample_index
                committed_value = partition_base + committed_rate * 5 * sample_index
                end_value = partition_base + first_lag + produce_rate * 5 * sample_index
            else:
                lag_value = 0
                committed_value = partition_base
                end_value = partition_base
            end_sample["value"] = str(end_value)
            committed_sample["value"] = str(committed_value)
            lag_sample["value"] = str(lag_value)


def _base_bundle() -> dict:
    bundle = json.loads(CAPTURE.read_text(encoding="utf-8"))
    _add_endpoint_identity(bundle, "application")
    _add_endpoint_identity(bundle, "prometheus")
    return bundle


def _make_bundle(
    *,
    sample_index: int,
    latest_lag: int,
    slope: int,
    produce_rate: int,
    offset_base: int,
) -> dict:
    bundle = _base_bundle()
    _shift_bundle(bundle, seconds=sample_index * 15, sample_index=sample_index)
    _set_measurement(
        bundle,
        latest_lag=latest_lag,
        slope=slope,
        produce_rate=produce_rate,
        offset_base=offset_base,
    )
    return bundle


@pytest.fixture
def recovery_case() -> tuple[object, list[dict], list[str]]:
    activation_bundles = [
        _make_bundle(
            sample_index=index,
            latest_lag=latest,
            slope=100,
            produce_rate=200,
            offset_base=100000 + index * 20000,
        )
        for index, latest in enumerate((8000, 10000, 12000))
    ]
    activation = evaluate_bundle_sequence(activation_bundles)
    assert activation.conditions[ConditionName.CORE_BACKLOG_PRESSURE].state == ConditionState.PRESENT
    recovery_bundles = [
        _make_bundle(
            sample_index=index + 3,
            latest_lag=latest,
            slope=-50,
            produce_rate=75,
            offset_base=200000 + index * 20000,
        )
        for index, latest in enumerate((9000, 6000, 3000))
    ]
    digests = [canonical_sha256(value) for value in recovery_bundles]
    return activation, recovery_bundles, digests


def _evaluate(case, bundles=None, digests=None):
    activation, originals, original_digests = case
    return evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=originals if bundles is None else bundles,
        source_bundle_digests=original_digests if digests is None else digests,
    )


def test_policy_is_versioned_invalid_only_without_derived_lag() -> None:
    policy = load_recovery_policy()

    assert policy.policy_version == "worker-backlog-local-ha.recovery.v1"
    assert policy.low_lag_evidence_policy == LowLagEvidencePolicy.INVALID_ONLY
    assert policy.derived_lag_enabled is False
    assert policy.calibration_experiment_id == "20260816T100600Z"

    promoted = load_recovery_policy(version="v2")
    assert promoted.policy_version == "worker-backlog-local-ha.recovery.v2"
    assert promoted.recovered_policy_status.value == "PROMOTED"
    assert promoted.recovered_consecutive_capture_count == 3
    assert promoted.low_lag_evidence_policy == LowLagEvidencePolicy.INVALID_ONLY
    assert promoted.derived_lag_enabled is False


def test_active_then_recovering_under_continuous_ingress(recovery_case) -> None:
    result = _evaluate(recovery_case)

    assert result.schema_version == "ops.recovery.v1"
    assert [item.state_after_capture for item in result.observations] == [
        RecoveryState.WORKER_BACKLOG_ACTIVE,
        RecoveryState.WORKER_BACKLOG_ACTIVE,
        RecoveryState.WORKER_BACKLOG_RECOVERING,
    ]
    assert result.state == RecoveryState.WORKER_BACKLOG_RECOVERING
    assert result.window.matched_recovering_windows == [[0, 1, 2]]
    assert "BACKLOG_DRAINING_UNDER_ACTIVE_INGRESS" in result.reason_codes
    assert result.recovery_completion.status.value == "CALIBRATION_PENDING"
    assert result.quality.negative_lag_clamped_to_zero is False
    assert result.quality.derived_lag_created is False


def test_zero_ingress_backlog_can_be_recovering(recovery_case) -> None:
    activation, _, _ = recovery_case
    bundles = [
        _make_bundle(
            sample_index=index + 3,
            latest_lag=latest,
            slope=-50,
            produce_rate=0,
            offset_base=300000 + index * 20000,
        )
        for index, latest in enumerate((9000, 6000, 3000))
    ]
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=bundles,
        source_bundle_digests=[canonical_sha256(value) for value in bundles],
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_RECOVERING
    assert "BACKLOG_DRAINING_WITH_ZERO_INGRESS" in result.reason_codes
    assert all(item.produce_rate_60s_records_per_second == 0 for item in result.observations)


def test_zero_ingress_without_drain_remains_active(recovery_case) -> None:
    activation, _, _ = recovery_case
    bundles = [
        _make_bundle(
            sample_index=index + 3,
            latest_lag=5000,
            slope=0,
            produce_rate=0,
            offset_base=400000 + index * 20000,
        )
        for index in range(3)
    ]
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=bundles,
        source_bundle_digests=[canonical_sha256(value) for value in bundles],
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_ACTIVE
    assert RecoveryState.WORKER_BACKLOG_RECOVERED not in {
        item.state_after_capture for item in result.observations
    }


def test_negative_exporter_lag_is_preserved_invalid_and_never_clamped(
    recovery_case,
) -> None:
    activation, bundles, _ = recovery_case
    changed = deepcopy(bundles)
    last = changed[-1]
    by_key = {
        (item["metric"]["name"], item["labels"].get("partition")): item
        for item in last["evidence"]
        if item["metric"]["name"] in KAFKA_METRICS
    }
    end = by_key[("kafka_topic_partition_current_offset", "0")]
    committed = by_key[("kafka_consumergroup_current_offset", "0")]
    lag = by_key[("kafka_consumergroup_lag", "0")]
    end_value = int(end["metric"]["value"][-1]["value"])
    committed["metric"]["value"][-1]["value"] = str(end_value + 2)
    lag["metric"]["value"][-1]["value"] = "-2"
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
    )

    observation = result.observations[-1]
    assert result.state == RecoveryState.WORKER_BACKLOG_UNKNOWN
    assert "NEGATIVE_EXPORTER_LAG_INVALID" in observation.issue_codes
    assert observation.negative_exporter_lag[0].exporter_lag_records == -2
    assert observation.negative_exporter_lag[0].end_offset_records == end_value
    assert observation.negative_exporter_lag[0].committed_offset_records == end_value + 2
    assert observation.derived_lag_evidence_ids == []
    assert result.quality.negative_lag_clamped_to_zero is False


def test_stale_middle_capture_makes_candidate_window_unknown(recovery_case) -> None:
    activation, bundles, _ = recovery_case
    changed = deepcopy(bundles)
    for item in changed[1]["evidence"]:
        if item["metric"]["name"] == "kafka_consumergroup_lag":
            item["freshness"].update(
                {"status": "STALE", "age_seconds": 16.0, "max_age_seconds": 15.0}
            )
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_UNKNOWN
    assert any("STALE" in code for code in result.reason_codes)


def test_partial_partition_coverage_is_unknown(recovery_case) -> None:
    activation, bundles, _ = recovery_case
    changed = deepcopy(bundles)
    changed[-1]["evidence"] = [
        item
        for item in changed[-1]["evidence"]
        if not (
            item["metric"]["name"] == "kafka_consumergroup_lag"
            and item["labels"].get("partition") == "7"
        )
    ]
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_UNKNOWN
    assert "REQUIRED_PARTITION_COVERAGE_MISMATCH" in result.reason_codes


def test_cross_capture_offset_reset_is_unknown(recovery_case) -> None:
    activation, bundles, _ = recovery_case
    changed = deepcopy(bundles)
    _set_measurement(
        changed[1],
        latest_lag=6000,
        slope=-50,
        produce_rate=75,
        offset_base=1000,
    )
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_UNKNOWN
    assert "RECOVERY_OFFSET_DECREASE_OR_RESET" in result.reason_codes


def test_identity_change_mid_sequence_is_unknown(recovery_case) -> None:
    activation, bundles, _ = recovery_case
    changed = deepcopy(bundles)
    changed[1]["scope"]["consumer_group"] = "other-worker"
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_UNKNOWN
    assert "RECOVERY_SCOPE_MISMATCH" in result.reason_codes


def test_db_degraded_while_lag_drains_is_unknown(recovery_case) -> None:
    activation, bundles, _ = recovery_case
    changed = deepcopy(bundles)
    for item in changed[-1]["evidence"]:
        if item["metric"]["name"] == "application_readiness_observation":
            item["metric"]["value"]["body_status"] = "degraded"
        elif item["metric"]["name"] == "application_postgres_runtime_observation":
            item["metric"]["value"]["primary_reachable"] = False
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_UNKNOWN
    assert "POSTGRES_REQUIRED_READINESS_NOT_ACCEPTABLE" in result.reason_codes
    assert result.observations[-1].postgres_ready is False


def test_timestamp_skew_beyond_coherence_window_is_unknown(recovery_case) -> None:
    activation, bundles, _ = recovery_case
    changed = deepcopy(bundles)
    for item in changed[-1]["evidence"]:
        if item["metric"]["name"] not in KAFKA_METRICS:
            continue
        item["source_timestamp"] = _format_timestamp(
            _parse_timestamp(item["source_timestamp"]) - timedelta(seconds=30)
        )
        item["freshness"]["age_seconds"] += 30
        item["freshness"]["status"] = "STALE"
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_UNKNOWN
    assert any("SOURCE_TIME_MISMATCH" in code for code in result.reason_codes)


def test_reordered_capture_timestamps_are_unknown(recovery_case) -> None:
    activation, bundles, _ = recovery_case
    changed = [bundles[0], bundles[2], bundles[1]]
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_UNKNOWN
    assert "RECOVERY_COLLECTION_TIMESTAMPS_REORDERED" in result.reason_codes


def test_digest_mismatch_is_unknown(recovery_case) -> None:
    _, bundles, digests = recovery_case
    changed_digests = list(digests)
    changed_digests[1] = "0" * 64
    result = _evaluate(recovery_case, bundles=bundles, digests=changed_digests)

    assert result.state == RecoveryState.WORKER_BACKLOG_UNKNOWN
    assert "RECOVERY_BUNDLE_DIGEST_MISMATCH" in result.reason_codes


def test_decreasing_then_regrowing_returns_to_active(recovery_case) -> None:
    activation, bundles, _ = recovery_case
    changed = deepcopy(bundles)
    for index, latest in enumerate((6000, 9000, 12000), start=3):
        changed.append(
            _make_bundle(
                sample_index=index + 3,
                latest_lag=latest,
                slope=50,
                produce_rate=150,
                offset_base=300000 + index * 20000,
            )
        )
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
    )

    assert RecoveryState.WORKER_BACKLOG_RECOVERING in {
        item.state_after_capture for item in result.observations
    }
    assert result.state == RecoveryState.WORKER_BACKLOG_ACTIVE
    assert "BACKLOG_REGROWTH_OR_DRAIN_STOPPED_ACTIVE_REMAINS" in result.reason_codes
    assert RecoveryState.WORKER_BACKLOG_RECOVERED not in {
        item.state_after_capture for item in result.observations
    }


def test_brief_envelope_entry_then_regrowing_remains_active(recovery_case) -> None:
    activation, bundles, _ = recovery_case
    changed = deepcopy(bundles)
    changed.extend(
        [
            _make_bundle(
                sample_index=6,
                latest_lag=20,
                slope=-1,
                produce_rate=75,
                offset_base=300000,
            ),
            _make_bundle(
                sample_index=7,
                latest_lag=10,
                slope=-1,
                produce_rate=75,
                offset_base=320000,
            ),
            _make_bundle(
                sample_index=8,
                latest_lag=1500,
                slope=10,
                produce_rate=100,
                offset_base=340000,
            ),
        ]
    )
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_ACTIVE
    assert "BACKLOG_REGROWTH_OR_DRAIN_STOPPED_ACTIVE_REMAINS" in result.reason_codes
    assert RecoveryState.WORKER_BACKLOG_RECOVERED not in {
        item.state_after_capture for item in result.observations
    }


def test_one_and_two_drain_captures_remain_active(recovery_case) -> None:
    activation, bundles, digests = recovery_case
    for capture_count in (1, 2):
        result = evaluate_recovery(
            incident_id="phase4-test",
            activation_evaluation=activation,
            bundles=bundles[:capture_count],
            source_bundle_digests=digests[:capture_count],
        )
        assert result.state == RecoveryState.WORKER_BACKLOG_ACTIVE


def test_recovery_evaluation_id_is_deterministic_and_binds_timing(recovery_case) -> None:
    first = _evaluate(recovery_case)
    second = _evaluate(recovery_case)
    assert first.recovery_evaluation_id == second.recovery_evaluation_id

    activation, bundles, _ = recovery_case
    shifted = deepcopy(bundles)
    _shift_bundle(shifted[-1], seconds=1, sample_index=5)
    changed = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=shifted,
        source_bundle_digests=[canonical_sha256(value) for value in shifted],
    )
    assert changed.recovery_evaluation_id != first.recovery_evaluation_id


def test_mutation_invalidates_recovery_evaluation_id(recovery_case) -> None:
    result = _evaluate(recovery_case)
    result.observations[-1].postgres_context["primary_reachable"] = False

    with pytest.raises(ValueError, match="recovery_evaluation_id"):
        result.model_dump(mode="json")


def test_recovered_state_is_rejected_by_v1_schema(recovery_case) -> None:
    result = _evaluate(recovery_case)
    payload = result.model_dump(mode="json")
    payload["state"] = "WORKER_BACKLOG_RECOVERED"
    payload["recovery_evaluation_id"] = "0" * 64

    with pytest.raises(ValueError, match="must not emit RECOVERED"):
        RecoveryEvaluation.model_validate(payload)


def _append_medium_reentry(
    bundles: list[dict],
    *,
    count: int,
    start_sample_index: int = 6,
) -> list[dict]:
    result = deepcopy(bundles)
    for offset in range(count):
        result.append(
            _make_bundle(
                sample_index=start_sample_index + offset,
                latest_lag=max(1, 22 - offset * 7),
                slope=0,
                produce_rate=75,
                offset_base=300000 + offset * 20000,
            )
        )
    return result


def test_recovery_v2_promotes_three_capture_medium_reentry(recovery_case) -> None:
    activation, bundles, _ = recovery_case
    changed = _append_medium_reentry(bundles, count=3)
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
        policy=load_recovery_policy(version="v2"),
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_RECOVERED
    assert result.evaluator_version == "ops.recovery.evaluator.v2"
    assert result.ruleset_version == "ops.recovery.rules.v2"
    assert result.recovery_completion.status.value == "COMPLETE"
    assert result.window.evaluated_sequence_indexes == [3, 4, 5]
    assert result.observations[-1].total_lag_records == 8
    assert "MEDIUM_ENVELOPE_REENTRY_STABLE_THREE_CAPTURES" in result.reason_codes


def test_recovery_v2_does_not_require_zero_lag_worker_two_or_keda_inactive(
    recovery_case,
) -> None:
    activation, bundles, _ = recovery_case
    changed = _append_medium_reentry(bundles, count=3)
    for bundle in changed[-3:]:
        for item in bundle["evidence"]:
            if item["metric"]["name"] == "kubernetes_worker_deployment_observation":
                item["metric"]["value"].update(
                    {
                        "desired_replicas": 4,
                        "current_replicas": 4,
                        "ready_replicas": 4,
                        "available_replicas": 4,
                    }
                )
            elif item["metric"]["name"] == "kubernetes_worker_scaled_object_observation":
                for condition in item["metric"]["value"]["conditions"]:
                    if condition["type"] == "Active":
                        condition["status"] = "True"
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
        policy=load_recovery_policy(version="v2"),
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_RECOVERED
    assert all(item.total_lag_records > 0 for item in result.observations[-3:])


def test_recovery_v2_one_or_two_reentry_captures_remain_in_progress(
    recovery_case,
) -> None:
    activation, bundles, _ = recovery_case
    for count in (1, 2):
        changed = _append_medium_reentry(bundles, count=count)
        result = evaluate_recovery(
            incident_id="phase4-test",
            activation_evaluation=activation,
            bundles=changed,
            source_bundle_digests=[canonical_sha256(value) for value in changed],
            policy=load_recovery_policy(version="v2"),
        )
        assert result.state != RecoveryState.WORKER_BACKLOG_RECOVERED
        assert result.recovery_completion.status.value == "IN_PROGRESS"


def test_recovery_v2_unknown_interrupts_reentry_sequence(recovery_case) -> None:
    activation, bundles, _ = recovery_case
    changed = _append_medium_reentry(bundles, count=4)
    interrupted = changed[-3]
    for item in interrupted["evidence"]:
        if item["metric"]["name"] == "kafka_consumergroup_lag":
            item["freshness"].update(
                {"status": "STALE", "age_seconds": 16.0, "max_age_seconds": 15.0}
            )
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
        policy=load_recovery_policy(version="v2"),
    )

    assert result.state != RecoveryState.WORKER_BACKLOG_RECOVERED
    assert RecoveryState.WORKER_BACKLOG_UNKNOWN in {
        item.state_after_capture for item in result.observations
    }


def test_recovery_v2_brief_entry_then_regrowth_is_not_recovered(
    recovery_case,
) -> None:
    activation, bundles, _ = recovery_case
    changed = _append_medium_reentry(bundles, count=2)
    changed.append(
        _make_bundle(
            sample_index=8,
            latest_lag=1500,
            slope=10,
            produce_rate=100,
            offset_base=340000,
        )
    )
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
        policy=load_recovery_policy(version="v2"),
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_ACTIVE
    assert RecoveryState.WORKER_BACKLOG_RECOVERED not in {
        item.state_after_capture for item in result.observations
    }


def test_recovery_v2_reentry_then_regrowth_is_not_recovered(recovery_case) -> None:
    activation, bundles, _ = recovery_case
    changed = _append_medium_reentry(bundles, count=3)
    changed.append(
        _make_bundle(
            sample_index=9,
            latest_lag=1800,
            slope=10,
            produce_rate=100,
            offset_base=360000,
        )
    )
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
        policy=load_recovery_policy(version="v2"),
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_ACTIVE
    assert RecoveryState.WORKER_BACKLOG_RECOVERED not in {
        item.state_after_capture for item in result.observations
    }


@pytest.mark.parametrize(
    "defect",
    ("coverage", "offset_reset", "identity", "postgres", "negative_lag"),
)
def test_recovery_v2_required_defect_is_unknown(recovery_case, defect: str) -> None:
    activation, bundles, _ = recovery_case
    changed = _append_medium_reentry(bundles, count=3)
    last = changed[-1]
    if defect == "coverage":
        last["evidence"] = [
            item
            for item in last["evidence"]
            if not (
                item["metric"]["name"] in KAFKA_METRICS
                and item["labels"].get("partition") == "7"
            )
        ]
    elif defect == "offset_reset":
        _set_measurement(
            last,
            latest_lag=8,
            slope=0,
            produce_rate=75,
            offset_base=1000,
        )
    elif defect == "identity":
        for item in last["evidence"]:
            if item["metric"]["name"] in {
                "kafka_consumergroup_current_offset",
                "kafka_consumergroup_lag",
            }:
                item["labels"]["consumergroup"] = "other-worker"
    elif defect == "postgres":
        for item in last["evidence"]:
            if item["metric"]["name"] == "application_readiness_observation":
                item["metric"]["value"]["body_status"] = "degraded"
            elif item["metric"]["name"] == "application_postgres_runtime_observation":
                item["metric"]["value"]["primary_reachable"] = False
    else:
        by_key = {
            (item["metric"]["name"], item["labels"].get("partition")): item
            for item in last["evidence"]
            if item["metric"]["name"] in KAFKA_METRICS
        }
        end = by_key[("kafka_topic_partition_current_offset", "0")]
        committed = by_key[("kafka_consumergroup_current_offset", "0")]
        lag = by_key[("kafka_consumergroup_lag", "0")]
        end_value = int(end["metric"]["value"][-1]["value"])
        committed["metric"]["value"][-1]["value"] = str(end_value + 2)
        lag["metric"]["value"][-1]["value"] = "-2"
    result = evaluate_recovery(
        incident_id="phase4-test",
        activation_evaluation=activation,
        bundles=changed,
        source_bundle_digests=[canonical_sha256(value) for value in changed],
        policy=load_recovery_policy(version="v2"),
    )

    assert result.state == RecoveryState.WORKER_BACKLOG_UNKNOWN
    assert result.recovery_completion.status.value == "IN_PROGRESS"
    assert result.quality.negative_lag_clamped_to_zero is False
    assert result.quality.derived_lag_created is False


def test_cli_evaluate_recovery_is_offline_and_preserves_order(
    recovery_case,
    tmp_path,
    capsys,
) -> None:
    activation, bundles, digests = recovery_case
    activation_path = tmp_path / "activation.json"
    activation_path.write_text(
        json.dumps(activation.model_dump(mode="json")),
        encoding="utf-8",
    )
    inputs = []
    for index, bundle in enumerate(bundles):
        path = tmp_path / f"sample-{index}.json"
        path.write_text(json.dumps(bundle), encoding="utf-8")
        inputs.append(path)
    output = tmp_path / "recovery.json"

    exit_code = cli.main(
        [
            "evaluate-recovery",
            "--activation",
            str(activation_path),
            "--input",
            *(str(path) for path in inputs),
            "--source-digest",
            *digests,
            "--incident-id",
            "phase4-test",
            "--output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == output.as_posix()
    assert payload["schema_version"] == "ops.recovery.v1"
    assert payload["state"] == "WORKER_BACKLOG_RECOVERING"
    assert [item["sequence_index"] for item in payload["source_bundles"]] == [0, 1, 2]


def test_cli_requires_one_digest_per_bundle(recovery_case, tmp_path) -> None:
    activation, bundles, _ = recovery_case
    activation_path = tmp_path / "activation.json"
    activation_path.write_text(json.dumps(activation.model_dump(mode="json")), encoding="utf-8")
    source = tmp_path / "sample.json"
    source.write_text(json.dumps(bundles[0]), encoding="utf-8")

    with pytest.raises(ValueError, match="one --source-digest"):
        cli.main(
            [
                "evaluate-recovery",
                "--activation",
                str(activation_path),
                "--input",
                str(source),
                "--source-digest",
                "0" * 64,
                "1" * 64,
                "--incident-id",
                "phase4-test",
            ]
        )
