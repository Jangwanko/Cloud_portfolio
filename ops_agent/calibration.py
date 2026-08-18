"""Derived measurements for controlled Worker backlog calibration captures."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from ops_agent.models import EvidenceBundle, EvidenceItem, EvidenceStatus, FreshnessStatus


PRESSURE_CANDIDATE_VERSION = "phase2.5.pressure-candidate.v1"
PRESSURE_CANDIDATE_LAG_FLOOR = 7_000
PRESSURE_CANDIDATE_SLOPE_FLOOR = 100.0
PRESSURE_CANDIDATE_CAPTURE_COUNT = 3
PRESSURE_CANDIDATE_PARTITION_COUNT = 8


def _validated_bundle(bundle: EvidenceBundle | Mapping[str, Any]) -> EvidenceBundle:
    if isinstance(bundle, EvidenceBundle):
        return bundle
    return EvidenceBundle.model_validate(bundle)


def _items(bundle: EvidenceBundle, metric_name: str) -> list[EvidenceItem]:
    return [item for item in bundle.evidence if item.metric.name == metric_name]


def _series(item: EvidenceItem) -> tuple[list[Decimal], list[Decimal]] | None:
    if (
        item.status != EvidenceStatus.OK
        or item.freshness.status != FreshnessStatus.FRESH
        or not isinstance(item.metric.value, list)
    ):
        return None
    timestamps: list[Decimal] = []
    values: list[Decimal] = []
    try:
        for sample in item.metric.value:
            if not isinstance(sample, Mapping):
                return None
            timestamp = Decimal(str(sample["timestamp"]))
            value = Decimal(str(sample["value"]))
            if not timestamp.is_finite() or not value.is_finite():
                return None
            timestamps.append(timestamp)
            values.append(value)
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return None
    if len(timestamps) < 2 or timestamps[-1] <= timestamps[0]:
        return None
    return timestamps, values


def _partition_family(
    bundle: EvidenceBundle,
    metric_name: str,
) -> dict[str, tuple[EvidenceItem, list[Decimal], list[Decimal]]]:
    result: dict[str, tuple[EvidenceItem, list[Decimal], list[Decimal]]] = {}
    for item in _items(bundle, metric_name):
        partition = item.labels.get("partition")
        parsed = _series(item)
        if partition is None or parsed is None or partition in result:
            continue
        result[partition] = (item, *parsed)
    return result


def _number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _histogram_stage_summary(bundle: EvidenceBundle) -> dict[str, Any]:
    count_delta = Decimal(0)
    sum_delta = Decimal(0)
    bucket_deltas: dict[Decimal, Decimal] = {}
    count_series = 0
    sum_series = 0
    counter_decrease = False
    source_timestamps: set[str] = set()
    matching_items: list[EvidenceItem] = []

    for item in bundle.evidence:
        if item.metric.name == "messaging_worker_stage_latency_seconds":
            pass
        elif not (
            item.metric.name.startswith("messaging_worker_stage_latency_seconds_")
            and item.labels.get("stage") == "db_persist"
        ):
            continue
        matching_items.append(item)
        parsed = _series(item)
        if parsed is None:
            continue
        _, values = parsed
        delta = values[-1] - values[0]
        if delta < 0:
            counter_decrease = True
            continue
        if item.source_timestamp is not None:
            source_timestamps.add(item.source_timestamp.isoformat())
        if item.metric.name.endswith("_count"):
            count_delta += delta
            count_series += 1
        elif item.metric.name.endswith("_sum"):
            sum_delta += delta
            sum_series += 1
        elif item.metric.name.endswith("_bucket"):
            raw_le = item.labels.get("le")
            if raw_le is None or raw_le == "+Inf":
                continue
            try:
                upper_bound = Decimal(raw_le)
            except InvalidOperation:
                continue
            bucket_deltas[upper_bound] = bucket_deltas.get(upper_bound, Decimal(0)) + delta

    p95_upper_bound: Decimal | None = None
    if count_delta > 0 and bucket_deltas:
        target = count_delta * Decimal("0.95")
        for upper_bound, cumulative_delta in sorted(bucket_deltas.items()):
            if cumulative_delta >= target:
                p95_upper_bound = upper_bound
                break

    evidence_status_counts = {
        value.value: sum(item.status == value for item in matching_items)
        for value in EvidenceStatus
        if any(item.status == value for item in matching_items)
    }
    freshness_status_counts = {
        value.value: sum(item.freshness.status == value for item in matching_items)
        for value in FreshnessStatus
        if any(item.freshness.status == value for item in matching_items)
    }

    if count_series == 0 and sum_series == 0:
        if any(item.status == EvidenceStatus.OK for item in matching_items):
            status = "UNKNOWN"
        elif any(item.status == EvidenceStatus.ERROR for item in matching_items):
            status = "ERROR"
        else:
            status = "MISSING"
    elif count_series > 0 and sum_series > 0 and not counter_decrease:
        status = "OK"
    else:
        status = "PARTIAL"
    return {
        "status": status,
        "semantic": "_persist_message_with_cursor stage only; transaction commit excluded",
        "observation_count_delta": (
            _number(count_delta) if count_series > 0 else None
        ),
        "sum_seconds_delta": _number(sum_delta) if sum_series > 0 else None,
        "mean_seconds": (
            float(sum_delta / count_delta) if count_delta > 0 else None
        ),
        "p95_finite_bucket_upper_bound_seconds": _number(p95_upper_bound),
        "count_series": count_series,
        "sum_series": sum_series,
        "counter_decrease": counter_decrease,
        "source_timestamps": sorted(source_timestamps),
        "matching_evidence_items": len(matching_items),
        "evidence_status_counts": evidence_status_counts,
        "freshness_status_counts": freshness_status_counts,
    }


def summarize_bundle(bundle: EvidenceBundle | Mapping[str, Any]) -> dict[str, Any]:
    """Return measurement-only values without applying incident thresholds."""

    source = _validated_bundle(bundle)
    end = _partition_family(source, "kafka_topic_partition_current_offset")
    committed = _partition_family(source, "kafka_consumergroup_current_offset")
    lag = _partition_family(source, "kafka_consumergroup_lag")
    partitions = sorted(set(end) | set(committed) | set(lag), key=lambda value: int(value))

    per_partition: dict[str, dict[str, Any]] = {}
    produce_delta = Decimal(0)
    committed_delta = Decimal(0)
    lag_first = Decimal(0)
    lag_latest = Decimal(0)
    windows: set[Decimal] = set()
    anomalies: list[str] = []

    for partition in partitions:
        end_series = end.get(partition)
        committed_series = committed.get(partition)
        lag_series = lag.get(partition)
        if end_series is None or committed_series is None or lag_series is None:
            anomalies.append(f"partition_{partition}_family_missing")
            continue
        _, end_timestamps, end_values = end_series
        _, committed_timestamps, committed_values = committed_series
        _, lag_timestamps, lag_values = lag_series
        if not (end_timestamps == committed_timestamps == lag_timestamps):
            anomalies.append(f"partition_{partition}_grid_mismatch")
            continue
        window = end_timestamps[-1] - end_timestamps[0]
        windows.add(window)
        end_delta = end_values[-1] - end_values[0]
        current_delta = committed_values[-1] - committed_values[0]
        lag_delta = lag_values[-1] - lag_values[0]
        if any(later < earlier for earlier, later in zip(end_values, end_values[1:])):
            anomalies.append(f"partition_{partition}_end_offset_decrease")
        if any(
            later < earlier
            for earlier, later in zip(committed_values, committed_values[1:])
        ):
            anomalies.append(f"partition_{partition}_committed_offset_decrease")
        if any(value == -1 for value in committed_values):
            anomalies.append(f"partition_{partition}_committed_uninitialized")
        if any(value < 0 for value in end_values):
            anomalies.append(f"partition_{partition}_negative_end_offset")
        if any(value < 0 for value in committed_values):
            anomalies.append(f"partition_{partition}_negative_committed_offset")
        if any(value < 0 for value in lag_values):
            anomalies.append(f"partition_{partition}_negative_lag")
        if any(
            lag_value != end_value - committed_value
            for end_value, committed_value, lag_value in zip(
                end_values, committed_values, lag_values
            )
        ):
            anomalies.append(f"partition_{partition}_offset_lag_arithmetic_mismatch")
        produce_delta += end_delta
        committed_delta += current_delta
        lag_first += lag_values[0]
        lag_latest += lag_values[-1]
        per_partition[partition] = {
            "end_offset": _number(end_values[-1]),
            "committed_offset": _number(committed_values[-1]),
            "lag": _number(lag_values[-1]),
            "end_offset_delta": _number(end_delta),
            "committed_offset_delta": _number(current_delta),
            "lag_delta": _number(lag_delta),
        }

    window_seconds = next(iter(windows)) if len(windows) == 1 else None
    if len(windows) != 1:
        anomalies.append("kafka_window_mismatch")
    required_partition_count = source.context.policy_version == "local-ha.evidence.v1" and 8
    if required_partition_count and len(per_partition) != required_partition_count:
        anomalies.append("partition_coverage_incomplete")

    deployment_items = _items(source, "kubernetes_worker_deployment_observation")
    deployment = (
        deployment_items[0].metric.value
        if len(deployment_items) == 1
        and deployment_items[0].status == EvidenceStatus.OK
        and isinstance(deployment_items[0].metric.value, Mapping)
        else {}
    )
    scaled_items = _items(source, "kubernetes_worker_scaled_object_observation")
    scaled = (
        scaled_items[0].metric.value
        if len(scaled_items) == 1
        and scaled_items[0].status == EvidenceStatus.OK
        and isinstance(scaled_items[0].metric.value, Mapping)
        else {}
    )
    keda_conditions = {
        str(condition.get("type")): str(condition.get("status"))
        for condition in scaled.get("conditions", [])
        if isinstance(condition, Mapping) and condition.get("type") is not None
    }
    postgres_items = _items(source, "application_postgres_runtime_observation")
    postgres = (
        postgres_items[0].metric.value
        if len(postgres_items) == 1
        and postgres_items[0].status == EvidenceStatus.OK
        and isinstance(postgres_items[0].metric.value, Mapping)
        else None
    )
    readiness_items = _items(source, "application_readiness_observation")
    readiness = (
        readiness_items[0].metric.value
        if len(readiness_items) == 1
        and readiness_items[0].status == EvidenceStatus.OK
        and isinstance(readiness_items[0].metric.value, Mapping)
        else None
    )

    return {
        "bundle_id": source.bundle_id,
        "incident_id": source.incident_id,
        "collection_started_at": source.collection.started_at.isoformat(),
        "collection_completed_at": source.collection.completed_at.isoformat(),
        "collection_status": source.collection.status.value,
        "kafka": {
            "window_seconds": _number(window_seconds),
            "produce_rate_records_per_second": (
                float(produce_delta / window_seconds)
                if window_seconds is not None and window_seconds > 0 and not anomalies
                else None
            ),
            "committed_offset_rate_records_per_second": (
                float(committed_delta / window_seconds)
                if window_seconds is not None and window_seconds > 0 and not anomalies
                else None
            ),
            "total_lag": _number(lag_latest) if not anomalies else None,
            "lag_slope_records_per_second": (
                float((lag_latest - lag_first) / window_seconds)
                if window_seconds is not None and window_seconds > 0 and not anomalies
                else None
            ),
            "per_partition": per_partition,
            "anomalies": sorted(set(anomalies)),
        },
        "worker": {
            "desired_replicas": deployment.get("desired_replicas"),
            "current_replicas": deployment.get("current_replicas"),
            "ready_replicas": deployment.get("ready_replicas"),
            "available_replicas": deployment.get("available_replicas"),
            "observed_generation": deployment.get("observed_generation"),
        },
        "keda": {
            "scale_target_name": scaled.get("scale_target_name"),
            "min_replicas": scaled.get("min_replicas"),
            "max_replicas": scaled.get("max_replicas"),
            "polling_interval_seconds": scaled.get("polling_interval_seconds"),
            "cooldown_period_seconds": scaled.get("cooldown_period_seconds"),
            "conditions": keda_conditions,
        },
        "postgres": {
            "readiness_body_status": (
                readiness.get("body_status") if readiness is not None else None
            ),
            "values": dict(postgres) if postgres is not None else None,
        },
        "worker_db_persist_stage_latency": _histogram_stage_summary(source),
    }


def evaluate_pressure_activation_candidate(
    samples: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply the frozen Phase 2.5 candidate without changing Phase 2 rules."""

    observations: list[dict[str, Any]] = []
    for index, sample in enumerate(samples):
        kafka = sample.get("kafka")
        if not isinstance(kafka, Mapping):
            observations.append(
                {"sample_index": index, "usable": False, "reason": "KAFKA_MISSING"}
            )
            continue
        lag = kafka.get("total_lag")
        slope = kafka.get("lag_slope_records_per_second")
        produce = kafka.get("produce_rate_records_per_second")
        committed = kafka.get("committed_offset_rate_records_per_second")
        partitions = kafka.get("per_partition")
        anomalies = kafka.get("anomalies")
        numeric = all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (lag, slope, produce, committed)
        )
        usable = bool(
            numeric
            and kafka.get("window_seconds") == 60
            and isinstance(partitions, Mapping)
            and len(partitions) == PRESSURE_CANDIDATE_PARTITION_COUNT
            and anomalies == []
        )
        if not usable:
            observations.append(
                {"sample_index": index, "usable": False, "reason": "KAFKA_UNUSABLE"}
            )
            continue

        rate_gap = float(produce) - float(committed)
        arithmetic_consistent = abs(rate_gap - float(slope)) <= 0.001
        growth_signal = bool(
            arithmetic_consistent
            and float(slope) >= PRESSURE_CANDIDATE_SLOPE_FLOOR
        )
        observations.append(
            {
                "sample_index": index,
                "sample_number": sample.get("sample_number"),
                "collection_started_at": sample.get("collection_started_at"),
                "usable": True,
                "total_lag": lag,
                "lag_slope_records_per_second": slope,
                "produce_rate_records_per_second": produce,
                "committed_offset_rate_records_per_second": committed,
                "produce_minus_committed_records_per_second": rate_gap,
                "rate_gap_matches_lag_slope": arithmetic_consistent,
                "lag_floor_met": float(lag) >= PRESSURE_CANDIDATE_LAG_FLOOR,
                "growth_signal_met": growth_signal,
                "candidate_sample_met": bool(
                    float(lag) >= PRESSURE_CANDIDATE_LAG_FLOOR
                    and growth_signal
                ),
            }
        )

    matched_windows: list[dict[str, Any]] = []
    width = PRESSURE_CANDIDATE_CAPTURE_COUNT
    for start in range(0, len(observations) - width + 1):
        window = observations[start : start + width]
        if not all(item.get("candidate_sample_met") is True for item in window):
            continue
        lags = [float(item["total_lag"]) for item in window]
        if not all(later > earlier for earlier, later in zip(lags, lags[1:])):
            continue
        matched_windows.append(
            {
                "start_sample_index": start,
                "end_sample_index": start + width - 1,
                "sample_numbers": [item.get("sample_number") for item in window],
                "total_lags": [_number(Decimal(str(value))) for value in lags],
                "growth_interval_count": width - 1,
            }
        )

    usable_count = sum(item.get("usable") is True for item in observations)
    if matched_windows:
        result = "PRESENT"
        reason = "THREE_CAPTURE_SUSTAINED_GROWTH_MATCHED"
    elif usable_count >= width:
        result = "NOT_PRESENT"
        reason = "NO_THREE_CAPTURE_SUSTAINED_GROWTH_MATCH"
    else:
        result = "INDETERMINATE"
        reason = "INSUFFICIENT_USABLE_CAPTURES"
    return {
        "candidate_version": PRESSURE_CANDIDATE_VERSION,
        "result": result,
        "reason": reason,
        "thresholds": {
            "total_lag_floor": PRESSURE_CANDIDATE_LAG_FLOOR,
            "lag_slope_floor_records_per_second": PRESSURE_CANDIDATE_SLOPE_FLOOR,
            "consecutive_capture_count": PRESSURE_CANDIDATE_CAPTURE_COUNT,
            "required_lag_growth_intervals": PRESSURE_CANDIDATE_CAPTURE_COUNT - 1,
        },
        "growth_signal": {
            "independent_vote_count": 1,
            "primary": "lag_slope_records_per_second",
            "produce_minus_committed_role": "arithmetic_consistency_check",
        },
        "usable_capture_count": usable_count,
        "matched_windows": matched_windows,
        "observations": observations,
    }


__all__ = ["summarize_bundle"]
