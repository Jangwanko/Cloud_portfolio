from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "demo.verified-incident-replay.v1"
ALLOWED_TOOLS = {
    "get_partition_lag",
    "get_worker_stage_latency",
    "get_worker_replica_status",
    "get_postgres_health",
}


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _project_partition_lag(summary: dict[str, Any]) -> dict[str, Any]:
    captures = []
    for capture in summary["captures"]:
        captures.append(
            {
                "sequence_index": capture["sequence_index"],
                "total_lag_records": capture["total_lag_records"],
                "lag_slope_60s_records_per_second": capture[
                    "lag_slope_60s_records_per_second"
                ],
                "maximum_partition_share": capture["maximum_partition_share"],
                "per_partition_latest_lag": {
                    partition: int(values["latest_value"])
                    for partition, values in capture["per_partition"].items()
                },
            }
        )
    return {
        "activation_window": summary["activation_window"],
        "captures": captures,
    }


def _project_stage_latency(summary: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "status",
        "semantic",
        "observation_count_delta",
        "sum_seconds_delta",
        "mean_seconds",
        "p95_finite_bucket_upper_bound_seconds",
        "counter_decrease",
    )
    return {
        "commit_latency": summary["commit_latency"],
        "stage_semantic": summary["stage_semantic"],
        "captures": [
            {
                "sequence_index": capture["sequence_index"],
                "context": {
                    field: capture["context"][field]
                    for field in fields
                },
            }
            for capture in summary["captures"]
        ],
    }


def _project_worker_replicas(summary: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "desired_replicas",
        "current_replicas",
        "ready_replicas",
        "available_replicas",
        "observed_generation",
    )
    return {
        "captures": [
            {
                "sequence_index": capture["sequence_index"],
                "context": {
                    field: capture["context"][field]
                    for field in fields
                },
            }
            for capture in summary["captures"]
        ]
    }


def _project_postgres(summary: dict[str, Any]) -> dict[str, Any]:
    value_fields = (
        "ha_mode",
        "primary_reachable",
        "standby_count",
        "sync_standby_count",
        "max_replication_delay_bytes",
    )
    return {
        "observations": [
            {
                "status": observation["status"],
                "freshness": observation["freshness"],
                "source_timestamp": observation["source_timestamp"],
                "value": {
                    field: observation["value"][field]
                    for field in value_fields
                },
            }
            for observation in summary["observations"]
        ]
    }


SUMMARY_PROJECTORS = {
    "get_partition_lag": _project_partition_lag,
    "get_worker_stage_latency": _project_stage_latency,
    "get_worker_replica_status": _project_worker_replicas,
    "get_postgres_health": _project_postgres,
}


def build_replay(
    diagnosis: dict[str, Any],
    references: dict[str, Any],
    *,
    diagnosis_sha256: str,
) -> dict[str, Any]:
    diagnosis_reference = references["diagnosis"]
    if diagnosis_reference["artifact_sha256"] != diagnosis_sha256:
        raise ValueError("diagnosis artifact hash does not match canonical reference")
    if diagnosis_reference["diagnosis_id"] != diagnosis["diagnosis_id"]:
        raise ValueError("diagnosis ID does not match canonical reference")

    evidence_by_id = {
        item["evidence_id"]: item for item in diagnosis["additional_evidence"]
    }
    tool_calls = []
    for step in diagnosis["steps"]:
        tool_id = step["tool_id"]
        if tool_id not in ALLOWED_TOOLS:
            raise ValueError(f"tool is not public replay allowlisted: {tool_id}")
        if len(step["returned_evidence_ids"]) != 1:
            raise ValueError("public replay expects exactly one normalized evidence per step")
        evidence = evidence_by_id[step["returned_evidence_ids"][0]]
        if evidence["tool_id"] != tool_id:
            raise ValueError("tool step and evidence tool do not match")
        tool_calls.append(
            {
                "step": step["step"],
                "tool_id": tool_id,
                "reason_code": step["reason_code"],
                "requested_at": step["requested_at"],
                "evidence": {
                    "evidence_id": evidence["evidence_id"],
                    "status": evidence["status"],
                    "observed_at": evidence["observed_at"],
                    "freshness": evidence["freshness"],
                    "semantic_type": evidence["semantic_type"],
                    "summary": SUMMARY_PROJECTORS[tool_id](evidence["summary"]),
                },
            }
        )

    condition_reference = references["condition"]
    return {
        "schema_version": SCHEMA_VERSION,
        "classification": "SANITIZED_RECORDED_REPLAY",
        "source": {
            "incident_id": references["incident_id"],
            "logical_diagnosis_incident_id": diagnosis["incident_id"],
            "condition": {
                "name": condition_reference["condition_name"],
                "state": condition_reference["condition_state"],
                "evaluation_id": diagnosis["condition_evaluation_id"],
                "policy_version": condition_reference["policy_version"],
            },
            "diagnosis_id": diagnosis["diagnosis_id"],
            "diagnosis_artifact_sha256": diagnosis_sha256,
            "recorded_at": diagnosis["completed_at"],
            "profile": diagnosis["context"]["profile"],
            "model": diagnosis["policy"]["model"],
            "tool_registry_version": diagnosis["policy"]["tool_registry_version"],
        },
        "investigation": {
            "steps_used": diagnosis["steps_used"],
            "max_steps": diagnosis["policy"]["max_steps"],
            "tool_calls": tool_calls,
        },
        "hypotheses": [
            {
                "hypothesis": item["hypothesis"],
                "support_status": item["support_status"],
                "reason_codes": item["reason_codes"],
                "supporting_evidence_ids": item["supporting_evidence_ids"],
                "conflicting_evidence_ids": item["conflicting_evidence_ids"],
                "evidence_gaps": item["evidence_gaps"],
            }
            for item in diagnosis["hypotheses"]
        ],
        "validation": {
            "result": diagnosis_reference["validation_status"],
            "checks": diagnosis["validation"],
            "output_repairs_used": diagnosis["output_repairs_used"],
            "attempts": [
                {
                    "attempt": item["attempt"],
                    "phase": item["phase"],
                    "result": item["result"],
                    "error_code": item["error_code"],
                }
                for item in diagnosis["validation_attempts"]
            ],
            "scope": "schema, citation, tool, budget, stop, and forbidden-claim contract",
            "causal_truth_validated": False,
        },
        "stop_reason": diagnosis["stop_reason"],
        "usage": {
            "api_requests": diagnosis["usage"]["api_requests"],
            "input_tokens": diagnosis["usage"]["input_tokens"],
            "output_tokens": diagnosis["usage"]["output_tokens"],
            "total_tokens": diagnosis["usage"]["total_tokens"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a sanitized, static Verified Incident diagnosis replay."
    )
    parser.add_argument("--diagnosis", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    diagnosis = json.loads(args.diagnosis.read_text(encoding="utf-8"))
    references = json.loads(args.references.read_text(encoding="utf-8"))
    replay = build_replay(
        diagnosis,
        references,
        diagnosis_sha256=_canonical_sha256(diagnosis),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(replay, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
