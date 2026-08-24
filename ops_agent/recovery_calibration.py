"""Measurement-only helpers for Phase 4 load-aware recovery calibration."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
import hashlib
import math
from pathlib import Path
from statistics import median
from typing import Any

from ops_agent.calibration import summarize_bundle
from ops_agent.evaluation_models import canonical_sha256
from ops_agent.models import EvidenceBundle, EvidenceStatus, FreshnessStatus


RECOVERY_CALIBRATION_SCHEMA = "ops.recovery-calibration.v1"
RECOVERY_ANALYSIS_SCHEMA = "ops.recovery-calibration-analysis.v1"
RATE_WINDOW_SECONDS = 60
SUPPORTED_SCENARIOS = {
    "PREFLIGHT": ("LOW", "MEDIUM", "HIGH_SUSTAINABLE", "OVERLOAD"),
    "A": ("IDLE", "LOW", "IDLE"),
    "B": ("LOW", "MEDIUM", "LOW"),
    "C": ("MEDIUM", "HIGH_SUSTAINABLE", "MEDIUM"),
    "D": ("MEDIUM", "OVERLOAD", "MEDIUM"),
    "E": ("MEDIUM", "OVERLOAD", "MEDIUM"),
    "F": ("MEDIUM", "OVERLOAD", "IDLE"),
}
REQUIRED_KAFKA_METRICS = (
    "kafka_topic_partition_current_offset",
    "kafka_consumergroup_current_offset",
    "kafka_consumergroup_lag",
)


def _as_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        return result if math.isfinite(result) else None
    return None


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("capture timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("capture timestamp must be timezone-aware")
    return parsed


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _stats(values: Sequence[Any]) -> dict[str, Any]:
    usable = [number for value in values if (number := _as_number(value)) is not None]
    if not usable:
        return {
            "count": 0,
            "min": None,
            "median": None,
            "p95_nearest_rank": None,
            "max": None,
        }
    return {
        "count": len(usable),
        "min": min(usable),
        "median": median(usable),
        "p95_nearest_rank": _nearest_rank(usable, 0.95),
        "max": max(usable),
    }


def derive_rate_candidates(
    *,
    observed_sustainable_rate: float,
    observed_overload_rate: float,
    observed_committed_capacity: float,
) -> dict[str, Any]:
    """Derive preflight candidates from prior measurements, not runtime constants."""

    inputs = [
        observed_sustainable_rate,
        observed_overload_rate,
        observed_committed_capacity,
    ]
    if any(not math.isfinite(value) or value <= 0 for value in inputs):
        raise ValueError("historical rate inputs must be finite and positive")
    sustainable_reference = min(
        observed_sustainable_rate,
        observed_committed_capacity,
    )

    def rounded(value: float, quantum: int) -> int:
        return max(1, int(round(value / quantum) * quantum))

    low = rounded(sustainable_reference * 0.25, 5)
    medium = rounded(sustainable_reference * 0.60, 5)
    high = rounded(sustainable_reference * 0.90, 5)
    overload = rounded(
        max(observed_overload_rate, observed_committed_capacity * 2.25),
        10,
    )
    if not 0 < low < medium < high < overload:
        raise ValueError("historical measurements did not produce ordered rate candidates")
    return {
        "provenance": {
            "observed_sustainable_rate_records_per_second": observed_sustainable_rate,
            "observed_overload_rate_records_per_second": observed_overload_rate,
            "observed_committed_capacity_records_per_second": observed_committed_capacity,
            "method": "25/60/90 percent of the lower sustainable/capacity reference; overload preserves the measured overload floor",
            "production_capacity_constant": False,
        },
        "rates": {
            "IDLE": 0,
            "LOW": low,
            "MEDIUM": medium,
            "HIGH_SUSTAINABLE": high,
            "OVERLOAD": overload,
        },
    }


def build_scenario_plan(
    *,
    scenario: str,
    rates: Mapping[str, int],
    durations_seconds: Sequence[int],
    streams: int = 64,
    capture_interval_seconds: int = 15,
    pre_allocated_vus: int = 100,
    max_vus: int = 400,
) -> dict[str, Any]:
    scenario = scenario.upper()
    profiles = SUPPORTED_SCENARIOS.get(scenario)
    if profiles is None:
        raise ValueError(f"unsupported recovery scenario: {scenario}")
    if len(durations_seconds) != len(profiles):
        raise ValueError("phase durations must match the scenario matrix")
    if streams < 2:
        raise ValueError("recovery workload must use multiple streams")
    if capture_interval_seconds < 5:
        raise ValueError("capture interval is too short for the collector")
    phases: list[dict[str, Any]] = []
    offset = 0
    for index, (profile, duration) in enumerate(zip(profiles, durations_seconds)):
        rate = rates.get(profile)
        if not isinstance(rate, int) or isinstance(rate, bool) or rate < 0:
            raise ValueError(f"missing or invalid rate for {profile}")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 1:
            raise ValueError("phase duration must be a positive integer")
        phases.append(
            {
                "phase_index": index,
                "phase_id": f"{index:02d}_{profile}",
                "profile": profile,
                "target_arrival_rate_records_per_second": rate,
                "duration_seconds": duration,
                "start_offset_seconds": offset,
                "end_offset_seconds": offset + duration,
            }
        )
        offset += duration
    identity = {
        "schema_version": RECOVERY_CALIBRATION_SCHEMA,
        "scenario": scenario,
        "streams": streams,
        "capture_interval_seconds": capture_interval_seconds,
        "pre_allocated_vus": pre_allocated_vus,
        "max_vus": max_vus,
        "phases": phases,
    }
    return {
        **identity,
        "plan_id": canonical_sha256(identity),
        "executor": "constant-arrival-rate",
        "arrival_rate_is_primary_load_parameter": True,
        "manual_keda_changes": False,
        "manual_replica_changes": False,
        "kubernetes_workload_objects_created": False,
        "total_duration_seconds": offset,
    }


def validate_scenario_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != RECOVERY_CALIBRATION_SCHEMA:
        raise ValueError("unexpected recovery calibration schema")
    scenario = str(plan.get("scenario", ""))
    expected_profiles = SUPPORTED_SCENARIOS.get(scenario)
    phases = plan.get("phases")
    if expected_profiles is None or not isinstance(phases, list):
        raise ValueError("invalid scenario plan")
    if tuple(item.get("profile") for item in phases) != expected_profiles:
        raise ValueError("scenario phase identity does not match the matrix")
    if [item.get("phase_index") for item in phases] != list(range(len(phases))):
        raise ValueError("phase indexes must be consecutive")
    phase_ids = [item.get("phase_id") for item in phases]
    if len(phase_ids) != len(set(phase_ids)):
        raise ValueError("phase IDs must be unique")
    expected_offset = 0
    for item in phases:
        if item.get("start_offset_seconds") != expected_offset:
            raise ValueError("phase offsets must be contiguous")
        duration = item.get("duration_seconds")
        if not isinstance(duration, int) or duration < 1:
            raise ValueError("phase duration must be positive")
        expected_offset += duration
        if item.get("end_offset_seconds") != expected_offset:
            raise ValueError("phase end offset is inconsistent")
    if plan.get("total_duration_seconds") != expected_offset:
        raise ValueError("scenario total duration is inconsistent")
    identity = {
        key: plan[key]
        for key in (
            "schema_version",
            "scenario",
            "streams",
            "capture_interval_seconds",
            "pre_allocated_vus",
            "max_vus",
            "phases",
        )
    }
    if plan.get("plan_id") != canonical_sha256(identity):
        raise ValueError("scenario plan ID does not match its content")


def phase_for_elapsed(plan: Mapping[str, Any], elapsed_seconds: float) -> dict[str, Any]:
    validate_scenario_plan(plan)
    if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
        raise ValueError("elapsed time must be finite and non-negative")
    phases = plan["phases"]
    for phase in phases:
        if elapsed_seconds < phase["end_offset_seconds"]:
            return dict(phase)
    return dict(phases[-1])


def _kafka_evidence_quality(bundle: EvidenceBundle) -> dict[str, Any]:
    families: dict[str, Any] = {}
    all_items = []
    for metric_name in REQUIRED_KAFKA_METRICS:
        items = [item for item in bundle.evidence if item.metric.name == metric_name]
        all_items.extend(items)
        partitions = sorted(
            {
                item.labels["partition"]
                for item in items
                if "partition" in item.labels
            },
            key=lambda value: int(value),
        )
        families[metric_name] = {
            "evidence_count": len(items),
            "status_counts": {
                status.value: sum(item.status == status for item in items)
                for status in EvidenceStatus
                if any(item.status == status for item in items)
            },
            "freshness_counts": {
                status.value: sum(item.freshness.status == status for item in items)
                for status in FreshnessStatus
                if any(item.freshness.status == status for item in items)
            },
            "observed_partitions": partitions,
            "coverage_complete": bool(
                len(partitions) == 8
                and len(items) == 8
                and all(item.coverage.complete is not False for item in items)
            ),
        }
    required_usable = bool(
        all_items
        and all(item.status == EvidenceStatus.OK for item in all_items)
        and all(item.freshness.status == FreshnessStatus.FRESH for item in all_items)
        and all(value["coverage_complete"] for value in families.values())
    )
    return {
        "required_kafka_usable": required_usable,
        "families": families,
    }


def summarize_recovery_capture(
    bundle: EvidenceBundle | Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    sequence_index: int,
    elapsed_seconds: float,
) -> dict[str, Any]:
    validate_scenario_plan(plan)
    source = bundle if isinstance(bundle, EvidenceBundle) else EvidenceBundle.model_validate(bundle)
    base = summarize_bundle(source)
    phase = phase_for_elapsed(plan, elapsed_seconds)
    phase_elapsed = max(0.0, elapsed_seconds - phase["start_offset_seconds"])
    per_partition = base["kafka"]["per_partition"]
    total_lag = _as_number(base["kafka"]["total_lag"])
    lags = [
        _as_number(item.get("lag"))
        for item in per_partition.values()
        if isinstance(item, Mapping)
    ]
    max_partition_share = None
    if total_lag is not None and lags and all(value is not None for value in lags):
        max_partition_share = 0.0 if total_lag == 0 else max(lags) / total_lag
    raw_artifacts = sorted(
        {
            (item.raw_ref, item.raw_sha256)
            for item in source.evidence
            if item.raw_ref is not None and item.raw_sha256 is not None
        }
    )
    quality = _kafka_evidence_quality(source)
    quality["collection_status"] = source.collection.status.value
    quality["kafka_anomalies"] = list(base["kafka"]["anomalies"])
    quality["usable"] = bool(
        quality["required_kafka_usable"]
        and not quality["kafka_anomalies"]
    )
    return {
        "schema_version": "ops.recovery-capture-summary.v1",
        "plan_id": plan["plan_id"],
        "scenario": plan["scenario"],
        "sequence_index": sequence_index,
        "elapsed_seconds": elapsed_seconds,
        "phase": phase,
        "phase_elapsed_seconds": phase_elapsed,
        "rate_window_settled": phase_elapsed >= RATE_WINDOW_SECONDS,
        "target_arrival_rate_records_per_second": phase[
            "target_arrival_rate_records_per_second"
        ],
        "source_bundle_id": source.bundle_id,
        "source_bundle_sha256": canonical_sha256(source.model_dump(mode="json")),
        "collection_timestamp": source.collection.completed_at.isoformat(),
        "collector_wall_seconds": (
            source.collection.completed_at - source.collection.started_at
        ).total_seconds(),
        "kafka": {
            **base["kafka"],
            "maximum_partition_lag_share": max_partition_share,
            "produce_rate_semantic": "topic end-offset delta over the Prometheus range window; not HTTP request rate",
            "committed_rate_semantic": "message-worker committed-offset delta over the Prometheus range window; not DB persistence rate",
            "rate_gap_role": "lag slope arithmetic consistency and explanatory context only",
        },
        "worker": base["worker"],
        "keda": base["keda"],
        "worker_db_persist_stage_latency": base[
            "worker_db_persist_stage_latency"
        ],
        "postgres": base["postgres"],
        "evidence_quality": quality,
        "raw_artifacts": [
            {"raw_ref": raw_ref, "raw_sha256": raw_sha256}
            for raw_ref, raw_sha256 in raw_artifacts
        ],
    }


def validate_ordered_capture_summaries(
    samples: Sequence[Mapping[str, Any]],
    *,
    plan: Mapping[str, Any],
) -> None:
    validate_scenario_plan(plan)
    if not samples:
        raise ValueError("ordered recovery samples are required")
    if [item.get("sequence_index") for item in samples] != list(range(len(samples))):
        raise ValueError("recovery sample indexes must be consecutive")
    timestamps = [_timestamp(item.get("collection_timestamp")) for item in samples]
    if any(later <= earlier for earlier, later in zip(timestamps, timestamps[1:])):
        raise ValueError("recovery sample timestamps must be strictly increasing")
    for sample in samples:
        if sample.get("plan_id") != plan["plan_id"]:
            raise ValueError("recovery sample plan identity changed")
        elapsed = _as_number(sample.get("elapsed_seconds"))
        if elapsed is None:
            raise ValueError("recovery sample elapsed time is invalid")
        expected = phase_for_elapsed(plan, elapsed)
        phase = sample.get("phase")
        if not isinstance(phase, Mapping) or phase.get("phase_id") != expected["phase_id"]:
            raise ValueError("recovery sample phase identity is inconsistent")


def validate_capture_artifacts(
    samples: Sequence[Mapping[str, Any]],
    *,
    repository_root: Path,
) -> dict[str, Any]:
    root = repository_root.resolve()
    errors: list[dict[str, Any]] = []
    verified_bundles = 0
    verified_raw_artifacts = 0

    def resolve_inside_root(value: Any) -> Path:
        if not isinstance(value, str) or not value:
            raise ValueError("artifact path is missing")
        candidate = Path(value)
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError("artifact path escapes repository root")
        return resolved

    for sample in samples:
        sequence_index = sample.get("sequence_index")
        try:
            bundle_path = resolve_inside_root(sample.get("bundle_path"))
            bundle = EvidenceBundle.model_validate_json(bundle_path.read_bytes())
            actual = canonical_sha256(bundle.model_dump(mode="json"))
            expected = sample.get("source_bundle_sha256")
            if actual != expected:
                raise ValueError("source bundle canonical digest mismatch")
            verified_bundles += 1
        except Exception as exc:
            errors.append(
                {
                    "sequence_index": sequence_index,
                    "artifact": "bundle",
                    "error": type(exc).__name__,
                    "detail": str(exc)[:300],
                }
            )

        raw_items = sample.get("raw_artifacts")
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            errors.append(
                {
                    "sequence_index": sequence_index,
                    "artifact": "raw",
                    "error": "ValueError",
                    "detail": "raw artifact list is missing",
                }
            )
            continue
        for raw in raw_items:
            try:
                if not isinstance(raw, Mapping):
                    raise ValueError("raw artifact entry is invalid")
                path = resolve_inside_root(raw.get("raw_ref"))
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual != raw.get("raw_sha256"):
                    raise ValueError("raw artifact digest mismatch")
                verified_raw_artifacts += 1
            except Exception as exc:
                errors.append(
                    {
                        "sequence_index": sequence_index,
                        "artifact": "raw",
                        "error": type(exc).__name__,
                        "detail": str(exc)[:300],
                    }
                )

    return {
        "status": "PASS" if not errors else "FAIL",
        "sample_count": len(samples),
        "verified_bundle_count": verified_bundles,
        "verified_raw_artifact_count": verified_raw_artifacts,
        "errors": errors,
    }


def build_operating_envelope(
    samples: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    baseline_samples = [
        sample for sample in samples if sample.get("scenario") in {"A", "B", "C"}
    ]
    quality_excluded = [
        {
            "scenario": sample.get("scenario"),
            "sequence_index": sample.get("sequence_index"),
            "phase": sample.get("phase", {}).get("profile"),
            "kafka_anomalies": sample.get("evidence_quality", {}).get(
                "kafka_anomalies", []
            ),
        }
        for sample in baseline_samples
        if sample.get("evidence_quality", {}).get("usable") is not True
    ]
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for sample in baseline_samples:
        quality = sample.get("evidence_quality")
        phase = sample.get("phase")
        if (
            sample.get("rate_window_settled") is not True
            or not isinstance(quality, Mapping)
            or quality.get("usable") is not True
            or not isinstance(phase, Mapping)
        ):
            continue
        grouped[str(phase.get("profile"))].append(sample)

    profiles: dict[str, Any] = {}
    for profile in ("IDLE", "LOW", "MEDIUM", "HIGH_SUSTAINABLE"):
        items = grouped.get(profile, [])
        stage = [item.get("worker_db_persist_stage_latency", {}) for item in items]
        postgres_values = [
            item.get("postgres", {}).get("values")
            for item in items
            if isinstance(item.get("postgres"), Mapping)
        ]
        profiles[profile] = {
            "status": "AVAILABLE" if items else "MISSING",
            "sample_count": len(items),
            "actual_produce_rate_records_per_second": _stats(
                [item.get("kafka", {}).get("produce_rate_records_per_second") for item in items]
            ),
            "committed_offset_rate_records_per_second": _stats(
                [
                    item.get("kafka", {}).get(
                        "committed_offset_rate_records_per_second"
                    )
                    for item in items
                ]
            ),
            "total_lag_records": _stats(
                [item.get("kafka", {}).get("total_lag") for item in items]
            ),
            "lag_slope_records_per_second": _stats(
                [item.get("kafka", {}).get("lag_slope_records_per_second") for item in items]
            ),
            "maximum_partition_lag_share": _stats(
                [item.get("kafka", {}).get("maximum_partition_lag_share") for item in items]
            ),
            "worker_db_persist_stage_mean_seconds": _stats(
                [value.get("mean_seconds") for value in stage if isinstance(value, Mapping)]
            ),
            "worker_db_persist_stage_p95_bucket_upper_bound_seconds": _stats(
                [
                    value.get("p95_finite_bucket_upper_bound_seconds")
                    for value in stage
                    if isinstance(value, Mapping)
                ]
            ),
            "worker_desired_replicas": sorted(
                {
                    value
                    for item in items
                    if (value := item.get("worker", {}).get("desired_replicas"))
                    is not None
                }
            ),
            "worker_available_replicas": sorted(
                {
                    value
                    for item in items
                    if (value := item.get("worker", {}).get("available_replicas"))
                    is not None
                }
            ),
            "postgres_guardrail": {
                "all_readiness_ready": all(
                    item.get("postgres", {}).get("readiness_body_status") == "ready"
                    for item in items
                )
                if items
                else None,
                "all_primary_reachable": all(
                    isinstance(value, Mapping)
                    and value.get("primary_reachable") is True
                    for value in postgres_values
                )
                if postgres_values
                else None,
                "minimum_standby_count": min(
                    (
                        int(value["standby_count"])
                        for value in postgres_values
                        if isinstance(value, Mapping)
                        and isinstance(value.get("standby_count"), int)
                    ),
                    default=None,
                ),
                "minimum_sync_standby_count": min(
                    (
                        int(value["sync_standby_count"])
                        for value in postgres_values
                        if isinstance(value, Mapping)
                        and isinstance(value.get("sync_standby_count"), int)
                    ),
                    default=None,
                ),
                "maximum_replication_delay_bytes": max(
                    (
                        int(value["max_replication_delay_bytes"])
                        for value in postgres_values
                        if isinstance(value, Mapping)
                        and isinstance(value.get("max_replication_delay_bytes"), int)
                    ),
                    default=None,
                ),
            },
            "evidence_quality": {
                "usable_samples": len(items),
                "required_kafka_complete_and_fresh": bool(items),
            },
        }
    available = [name for name, value in profiles.items() if value["status"] == "AVAILABLE"]
    return {
        "schema_version": "ops.load-aware-operating-envelope.v1",
        "policy_applied": False,
        "source_scenarios": ["A", "B", "C"],
        "source_sample_quality": {
            "input_sample_count": len(baseline_samples),
            "usable_sample_count": sum(
                sample.get("evidence_quality", {}).get("usable") is True
                for sample in baseline_samples
            ),
            "quality_excluded_sample_count": len(quality_excluded),
            "quality_excluded_samples": quality_excluded,
            "settling_samples_are_excluded_from_profile_statistics": True,
        },
        "rate_window_seconds": RATE_WINDOW_SECONDS,
        "percentile_method": "nearest_rank",
        "profiles": profiles,
        "available_profiles": available,
        "complete": len(available) == 4,
    }


def estimated_drain_context(total_lag: Any, lag_slope: Any) -> dict[str, Any]:
    lag = _as_number(total_lag)
    slope = _as_number(lag_slope)
    if lag is None or slope is None:
        return {"status": "UNAVAILABLE", "estimated_drain_seconds": None}
    if lag <= 0:
        return {"status": "NO_BACKLOG", "estimated_drain_seconds": None}
    if slope >= 0:
        return {"status": "NOT_DRAINING", "estimated_drain_seconds": None}
    return {
        "status": "AVAILABLE",
        "estimated_drain_seconds": lag / abs(slope),
        "net_drain_rate_records_per_second": abs(slope),
        "semantic": "diagnostic context from observed negative lag slope; not a recovery threshold",
    }


def _profile_for_rate(envelope: Mapping[str, Any], rate: float) -> str | None:
    profiles = envelope.get("profiles")
    if not isinstance(profiles, Mapping):
        return None
    candidates: list[tuple[float, str]] = []
    for name, value in profiles.items():
        if not isinstance(value, Mapping) or value.get("status") != "AVAILABLE":
            continue
        stats = value.get("actual_produce_rate_records_per_second")
        if not isinstance(stats, Mapping):
            continue
        center = _as_number(stats.get("median"))
        if center is not None:
            candidates.append((abs(rate - center), str(name)))
    return min(candidates)[1] if candidates else None


def analyze_recovery_candidates(
    samples: Sequence[Mapping[str, Any]],
    *,
    envelope: Mapping[str, Any],
    matched_activation_windows: Sequence[Sequence[int]],
) -> dict[str, Any]:
    if not samples:
        raise ValueError("recovery samples are required")
    activation = [list(window) for window in matched_activation_windows]
    if not activation:
        return {
            "status": "NO_ACTIVATION",
            "policy_applied": False,
            "matched_activation_windows": [],
        }
    first_window = activation[0]
    if not first_window or any(not isinstance(index, int) for index in first_window):
        raise ValueError("activation window indexes are invalid")
    start_index = first_window[0]
    end_index = first_window[-1]
    if start_index < 0 or end_index >= len(samples):
        raise ValueError("activation window is outside the capture sequence")
    post_activation = list(samples[end_index:])
    usable_lag = [
        (index + end_index, _as_number(item.get("kafka", {}).get("total_lag")))
        for index, item in enumerate(post_activation)
    ]
    usable_lag = [(index, value) for index, value in usable_lag if value is not None]
    if not usable_lag:
        raise ValueError("activation sequence has no usable lag observations")
    peak_index, peak_lag = max(usable_lag, key=lambda item: item[1])

    negative_runs: list[dict[str, Any]] = []
    current: list[int] = []
    for index in range(peak_index, len(samples)):
        slope = _as_number(samples[index].get("kafka", {}).get("lag_slope_records_per_second"))
        if slope is not None and slope < 0:
            current.append(index)
        elif current:
            negative_runs.append(
                {
                    "start_index": current[0],
                    "end_index": current[-1],
                    "capture_count": len(current),
                }
            )
            current = []
    if current:
        negative_runs.append(
            {
                "start_index": current[0],
                "end_index": current[-1],
                "capture_count": len(current),
            }
        )

    balance_reversal = None
    reentry_candidates: list[dict[str, Any]] = []
    scale_events: list[dict[str, Any]] = []
    previous_desired = None
    for index, sample in enumerate(samples):
        kafka = sample.get("kafka", {})
        produce = _as_number(kafka.get("produce_rate_records_per_second"))
        committed = _as_number(kafka.get("committed_offset_rate_records_per_second"))
        lag = _as_number(kafka.get("total_lag"))
        slope = _as_number(kafka.get("lag_slope_records_per_second"))
        desired = sample.get("worker", {}).get("desired_replicas")
        if desired != previous_desired:
            scale_events.append(
                {"sequence_index": index, "desired_replicas": desired}
            )
            previous_desired = desired
        if (
            index >= peak_index
            and balance_reversal is None
            and produce is not None
            and committed is not None
            and committed >= produce
        ):
            balance_reversal = index
        if (
            index < peak_index
            or sample.get("rate_window_settled") is not True
            or produce is None
            or lag is None
            or slope is None
        ):
            continue
        profile_name = _profile_for_rate(envelope, produce)
        profile = envelope.get("profiles", {}).get(profile_name, {})
        lag_stats = profile.get("total_lag_records", {}) if isinstance(profile, Mapping) else {}
        slope_stats = profile.get("lag_slope_records_per_second", {}) if isinstance(profile, Mapping) else {}
        lag_max = _as_number(lag_stats.get("max")) if isinstance(lag_stats, Mapping) else None
        lag_p95 = _as_number(lag_stats.get("p95_nearest_rank")) if isinstance(lag_stats, Mapping) else None
        slope_max = _as_number(slope_stats.get("max")) if isinstance(slope_stats, Mapping) else None
        quality = sample.get("evidence_quality", {})
        postgres = sample.get("postgres", {})
        values = postgres.get("values") if isinstance(postgres, Mapping) else None
        db_acceptable = bool(
            postgres.get("readiness_body_status") == "ready"
            and isinstance(values, Mapping)
            and values.get("primary_reachable") is True
        )
        reentry_candidates.append(
            {
                "sequence_index": index,
                "matched_profile": profile_name,
                "within_observed_max_lag": lag_max is not None and lag <= lag_max,
                "within_observed_p95_lag": lag_p95 is not None and lag <= lag_p95,
                "no_growth_above_observed_slope_max": slope_max is not None and slope <= slope_max,
                "processing_keeps_up_with_ingress": committed is not None and committed >= produce,
                "required_evidence_usable": isinstance(quality, Mapping) and quality.get("usable") is True,
                "postgres_guardrail_candidate_acceptable": db_acceptable,
                "estimated_drain": estimated_drain_context(lag, slope),
            }
        )

    stable_runs: list[dict[str, Any]] = []
    current_reentry: list[int] = []
    for item in reentry_candidates:
        stable = all(
            item.get(key) is True
            for key in (
                "within_observed_max_lag",
                "no_growth_above_observed_slope_max",
                "processing_keeps_up_with_ingress",
                "required_evidence_usable",
                "postgres_guardrail_candidate_acceptable",
            )
        )
        if stable:
            current_reentry.append(item["sequence_index"])
        elif current_reentry:
            stable_runs.append(
                {
                    "start_index": current_reentry[0],
                    "end_index": current_reentry[-1],
                    "capture_count": len(current_reentry),
                }
            )
            current_reentry = []
    if current_reentry:
        stable_runs.append(
            {
                "start_index": current_reentry[0],
                "end_index": current_reentry[-1],
                "capture_count": len(current_reentry),
            }
        )
    return {
        "status": "ANALYZED",
        "policy_applied": False,
        "recovery_state_emitted": False,
        "matched_activation_windows": activation,
        "activation_end_index": end_index,
        "peak_lag_records": peak_lag,
        "peak_lag_index": peak_index,
        "first_negative_lag_slope_index": (
            negative_runs[0]["start_index"] if negative_runs else None
        ),
        "negative_lag_slope_run_candidates": negative_runs,
        "produce_committed_balance_reversal_index": balance_reversal,
        "operating_envelope_reentry_candidates": reentry_candidates,
        "stable_reentry_window_candidates": stable_runs,
        "scale_timing_context": scale_events,
        "candidate_semantics": {
            "recovering": "backlog remains above the load-aware envelope while lag decreases and committed processing keeps up with ingress",
            "recovered": "backlog re-enters the observed load-aware envelope and remains stable with complete fresh Kafka evidence and acceptable PostgreSQL guardrail",
            "keda_and_replica_role": "optional timing context only",
            "thresholds_promoted": False,
        },
    }


__all__ = [
    "RECOVERY_ANALYSIS_SCHEMA",
    "RECOVERY_CALIBRATION_SCHEMA",
    "RATE_WINDOW_SECONDS",
    "SUPPORTED_SCENARIOS",
    "analyze_recovery_candidates",
    "build_operating_envelope",
    "build_scenario_plan",
    "derive_rate_candidates",
    "estimated_drain_context",
    "phase_for_elapsed",
    "summarize_recovery_capture",
    "validate_ordered_capture_summaries",
    "validate_capture_artifacts",
    "validate_scenario_plan",
]
