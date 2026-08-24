from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path

from ops_agent.endpoint_provenance import endpoint_provenance


ROOT = Path(__file__).resolve().parents[2]
TRACKED_BASELINE = (
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


def shift_bundle(bundle: dict, seconds: int, index: int) -> None:
    delta = timedelta(seconds=seconds)
    bundle["bundle_id"] = f"synthetic-sequence-{index}"
    bundle["incident_id"] = f"synthetic-sequence-{index}"
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


def _add_prometheus_endpoint_identity(bundle: dict) -> None:
    value = endpoint_provenance(
        base_url="http://127.0.0.1/prometheus",
        host_header="localhost",
        configuration_source="policy",
    )
    completed_at = bundle["collection"]["completed_at"]
    bundle["evidence"].append(
        {
            "evidence_id": "collector_configuration.source_endpoint_identity.synthetic",
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
            "labels": {"collector_source": "prometheus"},
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
                "notes": "Synthetic fixed collector endpoint configuration identity.",
                "is_db_commit_rate": None,
                "flags": ["configuration_provenance", "credentials_excluded"],
            },
            "raw_ref": None,
            "raw_sha256": None,
            "error": None,
        }
    )


def set_measurement(bundle: dict, latest_lag: int, slope: int) -> None:
    first_lag = latest_lag - (slope * 60)
    assert first_lag >= 0
    by_metric_partition = {
        (item["metric"]["name"], item["labels"].get("partition")): item
        for item in bundle["evidence"]
        if item["metric"]["name"] in KAFKA_METRICS
    }
    for partition in map(str, range(8)):
        committed = by_metric_partition[
            ("kafka_consumergroup_current_offset", partition)
        ]
        end = by_metric_partition[
            ("kafka_topic_partition_current_offset", partition)
        ]
        lag = by_metric_partition[("kafka_consumergroup_lag", partition)]
        committed_value = int(committed["metric"]["value"][0]["value"])
        for sample_index, (committed_sample, end_sample, lag_sample) in enumerate(
            zip(
                committed["metric"]["value"],
                end["metric"]["value"],
                lag["metric"]["value"],
            )
        ):
            partition_lag = (
                first_lag + (slope * 5 * sample_index)
                if partition == "0"
                else 0
            )
            committed_sample["value"] = str(committed_value)
            lag_sample["value"] = str(partition_lag)
            end_sample["value"] = str(committed_value + partition_lag)


def build_positive_sequence() -> list[dict]:
    baseline = json.loads(TRACKED_BASELINE.read_text(encoding="utf-8"))
    sequence: list[dict] = []
    for index, (latest_lag, slope) in enumerate(
        ((8000, 100), (10000, 100), (12000, 100))
    ):
        bundle = deepcopy(baseline)
        shift_bundle(bundle, index * 15, index)
        _add_prometheus_endpoint_identity(bundle)
        set_measurement(bundle, latest_lag, slope)
        sequence.append(bundle)
    return sequence
