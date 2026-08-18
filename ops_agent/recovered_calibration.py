"""Deterministic analysis helpers for RECOVERED policy calibration."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


RECOVERED_CALIBRATION_SCHEMA = "ops.recovered-calibration-analysis.v1"
MEDIUM_REENTRY_CONTRACT_VERSION = "local-ha.medium-reentry-candidate.v1"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_medium_reentry_contract(
    baseline_analysis: Mapping[str, Any],
    *,
    baseline_path: Path,
    baseline_display_path: str | None = None,
    configured_capture_interval_seconds: int,
    capture_interval_min_seconds: float,
    capture_interval_max_seconds: float,
) -> dict[str, Any]:
    """Freeze the existing Phase 4 MEDIUM envelope without inventing bounds."""

    envelope = baseline_analysis.get("operating_envelope")
    if not isinstance(envelope, Mapping) or envelope.get("complete") is not True:
        raise ValueError("a complete Phase 4 operating envelope is required")
    profiles = envelope.get("profiles")
    medium = profiles.get("MEDIUM") if isinstance(profiles, Mapping) else None
    if not isinstance(medium, Mapping) or medium.get("status") != "AVAILABLE":
        raise ValueError("the Phase 4 MEDIUM operating envelope is unavailable")
    produce = medium.get("actual_produce_rate_records_per_second")
    lag = medium.get("total_lag_records")
    slope = medium.get("lag_slope_records_per_second")
    if not all(isinstance(value, Mapping) for value in (produce, lag, slope)):
        raise ValueError("the MEDIUM envelope statistics are incomplete")
    produce_min = _number(produce.get("min"))
    produce_max = _number(produce.get("max"))
    lag_max = _number(lag.get("max"))
    slope_max = _number(slope.get("max"))
    if None in (produce_min, produce_max, lag_max, slope_max):
        raise ValueError("the MEDIUM envelope bounds are invalid")
    if produce_min > produce_max or lag_max < 0:
        raise ValueError("the MEDIUM envelope bounds are incoherent")
    return {
        "schema_version": "ops.recovered-envelope-candidate.v1",
        "contract_version": MEDIUM_REENTRY_CONTRACT_VERSION,
        "promotion_status": "CALIBRATION_PENDING",
        "profile": "local-ha",
        "workload_phase": "MEDIUM",
        "target_arrival_rate_records_per_second": 75,
        "rate_window_seconds": int(envelope.get("rate_window_seconds", 60)),
        "actual_produce_rate_records_per_second": {
            "minimum": produce_min,
            "maximum": produce_max,
            "basis": "observed Phase 4 MEDIUM minimum and maximum",
        },
        "total_lag_records": {
            "maximum": lag_max,
            "basis": "observed Phase 4 MEDIUM maximum; local-ha candidate only",
        },
        "lag_slope_records_per_second": {
            "maximum": slope_max,
            "basis": "observed Phase 4 MEDIUM maximum",
        },
        "required_quality": {
            "phase_rate_window_settled": True,
            "required_kafka_evidence_usable": True,
            "postgres_readiness_body_status": "ready",
            "postgres_ha_mode": True,
            "postgres_primary_reachable": True,
            "negative_exporter_lag_policy": "INVALID_ONLY",
        },
        "capture_cadence": {
            "configured_seconds": configured_capture_interval_seconds,
            "minimum_seconds": capture_interval_min_seconds,
            "maximum_seconds": capture_interval_max_seconds,
            "basis": "existing worker-backlog-local-ha.recovery.v1 provenance gate",
        },
        "not_required": [
            "lag_equals_zero",
            "worker_replica_count",
            "keda_inactive",
            "zero_ingress",
        ],
        "baseline_provenance": {
            "experiment_id": baseline_analysis.get("experiment_id"),
            "analysis_path": baseline_display_path or baseline_path.as_posix(),
            "analysis_sha256": _sha256(baseline_path),
            "medium_sample_count": medium.get("sample_count"),
        },
    }


def _recovery_observations(
    recovery: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], int | None, int]:
    values = recovery.get("observations")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError("recovery observations are missing")
    by_bundle: dict[str, Mapping[str, Any]] = {}
    first_recovering: int | None = None
    unknown_count = 0
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("recovery observation is invalid")
        bundle_id = value.get("bundle_id")
        if not isinstance(bundle_id, str) or not bundle_id:
            raise ValueError("recovery observation bundle identity is missing")
        by_bundle[bundle_id] = value
        state = value.get("state_after_capture")
        if state == "WORKER_BACKLOG_RECOVERING" and first_recovering is None:
            first_recovering = int(value["sequence_index"])
        elif state == "WORKER_BACKLOG_UNKNOWN":
            unknown_count += 1
    return by_bundle, first_recovering, unknown_count


def analyze_medium_reentry(
    samples: Sequence[Mapping[str, Any]],
    *,
    recovery: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Measure stable MEDIUM re-entry after RECOVERING has been observed."""

    if not samples:
        raise ValueError("recovery calibration samples are required")
    observations, first_recovering_relative, unknown_count = _recovery_observations(
        recovery
    )
    first_recovering_sample: int | None = None
    if first_recovering_relative is not None:
        recovering_items = [
            value
            for value in observations.values()
            if value.get("state_after_capture") == "WORKER_BACKLOG_RECOVERING"
        ]
        first = min(recovering_items, key=lambda value: int(value["sequence_index"]))
        first_bundle = first.get("bundle_id")
        first_recovering_sample = next(
            (
                int(sample["sequence_index"])
                for sample in samples
                if sample.get("source_bundle_id") == first_bundle
            ),
            None,
        )

    produce_bound = contract["actual_produce_rate_records_per_second"]
    lag_bound = contract["total_lag_records"]
    slope_bound = contract["lag_slope_records_per_second"]
    cadence = contract["capture_cadence"]
    checks: list[dict[str, Any]] = []
    previous_timestamp: datetime | None = None
    for sample in samples:
        sequence_index = int(sample["sequence_index"])
        collection_timestamp = _timestamp(sample.get("collection_timestamp"))
        interval = (
            (collection_timestamp - previous_timestamp).total_seconds()
            if collection_timestamp is not None and previous_timestamp is not None
            else None
        )
        if collection_timestamp is not None:
            previous_timestamp = collection_timestamp
        phase = sample.get("phase") if isinstance(sample.get("phase"), Mapping) else {}
        kafka = sample.get("kafka") if isinstance(sample.get("kafka"), Mapping) else {}
        postgres = (
            sample.get("postgres")
            if isinstance(sample.get("postgres"), Mapping)
            else {}
        )
        postgres_values = (
            postgres.get("values")
            if isinstance(postgres.get("values"), Mapping)
            else {}
        )
        quality = (
            sample.get("evidence_quality")
            if isinstance(sample.get("evidence_quality"), Mapping)
            else {}
        )
        observation = observations.get(str(sample.get("source_bundle_id")))
        produce = _number(kafka.get("produce_rate_records_per_second"))
        committed = _number(kafka.get("committed_offset_rate_records_per_second"))
        lag = _number(kafka.get("total_lag"))
        slope = _number(kafka.get("lag_slope_records_per_second"))
        after_recovering = bool(
            first_recovering_sample is not None
            and sequence_index >= first_recovering_sample
        )
        evidence_usable = bool(
            quality.get("usable") is True
            and isinstance(observation, Mapping)
            and observation.get("usable") is True
            and observation.get("state_after_capture")
            != "WORKER_BACKLOG_UNKNOWN"
        )
        individual = {
            "after_recovering_observed": after_recovering,
            "medium_recovery_phase": phase.get("profile") == "MEDIUM",
            "target_rate_matches": sample.get("target_arrival_rate_records_per_second")
            == contract["target_arrival_rate_records_per_second"],
            "rate_window_settled": sample.get("rate_window_settled") is True,
            "actual_produce_within_medium_envelope": bool(
                produce is not None
                and produce_bound["minimum"] <= produce <= produce_bound["maximum"]
            ),
            "lag_within_medium_envelope": bool(
                lag is not None and lag <= lag_bound["maximum"]
            ),
            "no_growth_above_medium_envelope": bool(
                slope is not None and slope <= slope_bound["maximum"]
            ),
            "processing_direction_consistent": bool(
                produce is not None
                and committed is not None
                and committed >= produce
            ),
            "required_evidence_usable": evidence_usable,
            "postgres_guardrail_acceptable": bool(
                postgres.get("readiness_body_status") == "ready"
                and postgres_values.get("ha_mode") is True
                and postgres_values.get("primary_reachable") is True
            ),
            "capture_cadence_acceptable": bool(
                interval is None
                or cadence["minimum_seconds"]
                <= interval
                <= cadence["maximum_seconds"]
            ),
        }
        stable = all(individual.values())
        checks.append(
            {
                "sequence_index": sequence_index,
                "source_bundle_id": sample.get("source_bundle_id"),
                "collection_timestamp": sample.get("collection_timestamp"),
                "capture_interval_seconds": interval,
                "produce_rate_records_per_second": produce,
                "committed_offset_rate_records_per_second": committed,
                "total_lag_records": lag,
                "lag_slope_records_per_second": slope,
                "stable_reentry_candidate": stable,
                "checks": individual,
            }
        )

    windows: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for item in checks:
        if item["stable_reentry_candidate"]:
            current.append(item)
        elif current:
            windows.append(
                {
                    "start_index": current[0]["sequence_index"],
                    "end_index": current[-1]["sequence_index"],
                    "capture_count": len(current),
                }
            )
            current = []
    if current:
        windows.append(
            {
                "start_index": current[0]["sequence_index"],
                "end_index": current[-1]["sequence_index"],
                "capture_count": len(current),
            }
        )
    stable_indexes = {
        index
        for window in windows
        for index in range(window["start_index"], window["end_index"] + 1)
    }
    first_reentry = min(stable_indexes) if stable_indexes else None
    reexit_after_reentry = [
        item["sequence_index"]
        for item in checks
        if first_reentry is not None
        and item["sequence_index"] > first_reentry
        and item["sequence_index"] not in stable_indexes
    ]
    negative_invalid = sum(
        bool(item.get("negative_exporter_lag"))
        for item in observations.values()
    )
    return {
        "schema_version": RECOVERED_CALIBRATION_SCHEMA,
        "contract_version": contract["contract_version"],
        "recovery_evaluation_id": recovery.get("recovery_evaluation_id"),
        "first_recovering_sample_index": first_recovering_sample,
        "first_usable_envelope_reentry_index": first_reentry,
        "stable_reentry_windows": windows,
        "maximum_consecutive_usable_reentry_count": max(
            (window["capture_count"] for window in windows),
            default=0,
        ),
        "unknown_capture_count": unknown_count,
        "negative_exporter_lag_invalid_capture_count": negative_invalid,
        "reexit_after_first_reentry_indexes": reexit_after_reentry,
        "capture_checks": checks,
        "recovered_state_emitted": False,
        "promotion_status": "CALIBRATION_PENDING",
    }


def stable_count_distribution(run_analyses: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    counts = {
        name: int(value.get("maximum_consecutive_usable_reentry_count", 0))
        for name, value in sorted(run_analyses.items())
    }
    maximum = max(counts.values(), default=0)
    support = {
        str(candidate): sorted(
            name for name, count in counts.items() if count >= candidate
        )
        for candidate in range(1, maximum + 1)
    }
    return {
        "per_run_maximum_consecutive_usable_reentry_count": counts,
        "candidate_supporting_runs": support,
        "candidate_selected": None,
        "selection_status": "PENDING_EVIDENCE_REVIEW",
    }


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


__all__ = [
    "MEDIUM_REENTRY_CONTRACT_VERSION",
    "RECOVERED_CALIBRATION_SCHEMA",
    "analyze_medium_reentry",
    "build_medium_reentry_contract",
    "load_json",
    "stable_count_distribution",
]
