from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PUBLIC_SCHEMA_VERSION = "demo.verified-scenario-replays.v1"
PUBLIC_CLASSIFICATION = "SANITIZED_CONTROLLED_SCENARIO_REPLAY"
SOURCE_SCHEMA_VERSION = "demo.scenario-lab-replay.v1"
SOURCE_CLASSIFICATION = "CONTROLLED_SCENARIO_REPLAY"

SAFE_SUMMARY_FIELDS = {
    "get_worker_stage_latency": {
        "worker_stage_mean_ms",
        "scenario_baseline_ms",
        "observation_count",
        "relative_to_baseline",
        "semantic_flag",
        "commit_latency",
    },
    "get_postgres_health": {
        "observation",
        "ha_mode",
        "primary_reachable",
        "standby_count",
        "required_standby_count",
        "sync_standby_count",
        "required_sync_standby_count",
        "max_replication_delay_bytes",
        "semantic_flag",
    },
    "get_worker_replica_status": {
        "desired_replicas",
        "current_replicas",
        "ready_replicas",
        "available_replicas",
        "availability_gap",
        "semantic_flag",
    },
    "get_keda_status": {
        "ready",
        "active",
        "min_replicas",
        "max_replicas",
        "current_replicas",
    },
}

HIGHLIGHT_FIELDS = {
    "get_worker_stage_latency": (
        "worker_stage_mean_ms",
        "scenario_baseline_ms",
        "semantic_flag",
    ),
    "get_postgres_health": (
        "primary_reachable",
        "standby_count",
        "sync_standby_count",
        "semantic_flag",
    ),
    "get_worker_replica_status": (
        "desired_replicas",
        "available_replicas",
        "semantic_flag",
    ),
    "get_keda_status": ("ready", "active", "current_replicas"),
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _safe_summary(evidence: dict[str, Any]) -> dict[str, Any]:
    tool_id = str(evidence.get("tool_id", ""))
    allowed = SAFE_SUMMARY_FIELDS.get(tool_id)
    _require(allowed is not None, f"unsupported public scenario tool: {tool_id}")
    summary = evidence.get("summary")
    _require(isinstance(summary, dict), f"missing normalized summary: {tool_id}")
    unknown = set(summary) - allowed
    _require(not unknown, f"unapproved summary fields for {tool_id}: {sorted(unknown)}")
    return {key: summary[key] for key in summary if key in allowed}


def _public_evidence(
    fixture_id: str,
    step: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    freshness = evidence.get("freshness") or {}
    reference = f"{fixture_id}.evidence.step-{step['step']}"
    projected = {
        "evidence_ref": reference,
        "tool_id": evidence["tool_id"],
        "status": evidence["status"],
        "observed_at": evidence["observed_at"],
        "freshness": {
            "status": freshness.get("status", "UNKNOWN"),
            "max_age_seconds": freshness.get("max_age_seconds"),
        },
        "semantic_type": evidence["semantic_type"],
        "summary": _safe_summary(evidence),
        "acquisition_mode": "CONTROLLED_SCENARIO",
    }
    if evidence.get("error_code"):
        projected["error_code"] = evidence["error_code"]
    return projected


def _map_citations(values: list[str], evidence_refs: dict[str, str]) -> list[str]:
    missing = [value for value in values if value not in evidence_refs]
    _require(not missing, f"hypothesis cites evidence outside public tool trace: {missing}")
    return [evidence_refs[value] for value in values]


def _scenario_highlights(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    highlights: list[dict[str, Any]] = []
    for item in evidence:
        tool_id = item["tool_id"]
        summary = item["summary"]
        if item["status"] == "UNAVAILABLE":
            highlights.append(
                {
                    "tool_id": tool_id,
                    "field": "status",
                    "value": "UNAVAILABLE",
                }
            )
            if item.get("error_code"):
                highlights.append(
                    {
                        "tool_id": tool_id,
                        "field": "error_code",
                        "value": item["error_code"],
                    }
                )
            continue
        for field in HIGHLIGHT_FIELDS[tool_id]:
            if field in summary:
                highlights.append(
                    {"tool_id": tool_id, "field": field, "value": summary[field]}
                )
    return highlights


def _project_scenario(source_scenario: dict[str, Any]) -> dict[str, Any]:
    fixture_id = source_scenario["fixture_id"]
    run = source_scenario["run"]
    _require(run.get("schema_version") == "ops.diagnosis.v2", "unexpected diagnosis schema")
    _require(run.get("acquisition_mode") == "CONTROLLED_SCENARIO", "unexpected acquisition mode")
    validation = run.get("validation") or {}
    _require(validation and all(validation.values()), f"invalid scenario output: {fixture_id}")
    _require(
        all(item.get("result") == "PASS" for item in run.get("branch_evaluations", [])),
        f"failed branch evaluation cannot be public verified replay: {fixture_id}",
    )

    evidence_by_id = {
        item["evidence_id"]: item for item in run.get("additional_evidence", [])
    }
    public_evidence: list[dict[str, Any]] = []
    evidence_refs: dict[str, str] = {}
    public_steps: list[dict[str, Any]] = []
    for step in run.get("steps", []):
        returned = step.get("returned_evidence_ids") or []
        _require(len(returned) == 1, f"public replay expects one evidence result per step: {fixture_id}")
        source_id = returned[0]
        _require(source_id in evidence_by_id, f"missing step evidence: {source_id}")
        projected = _public_evidence(fixture_id, step, evidence_by_id[source_id])
        evidence_refs[source_id] = projected["evidence_ref"]
        public_evidence.append(projected)
        public_steps.append(
            {
                "step": step["step"],
                "tool_id": step["tool_id"],
                "reason_code": step["reason_code"],
                "returned_evidence_ref": projected["evidence_ref"],
            }
        )

    hypotheses = []
    for hypothesis in run.get("hypotheses", []):
        hypotheses.append(
            {
                "hypothesis": hypothesis["hypothesis"],
                "support_status": hypothesis["support_status"],
                "supporting_evidence_refs": _map_citations(
                    hypothesis.get("supporting_evidence_ids", []), evidence_refs
                ),
                "conflicting_evidence_refs": _map_citations(
                    hypothesis.get("conflicting_evidence_ids", []), evidence_refs
                ),
                "evidence_gaps": list(hypothesis.get("evidence_gaps", [])),
            }
        )

    branches = [
        {
            "after_tool_id": item["after_tool_id"],
            "selected_next_tool": item.get("selected_next_tool"),
            "result": item["result"],
        }
        for item in run.get("branch_evaluations", [])
    ]
    return {
        "scenario_id": fixture_id,
        "title": source_scenario["title"],
        "expected_primary_hypothesis": source_scenario["expected_primary_hypothesis"],
        "acquisition_mode": "CONTROLLED_SCENARIO",
        "steps": public_steps,
        "evidence": public_evidence,
        "situation_highlights": _scenario_highlights(public_evidence),
        "branch_trace": branches,
        "hypotheses": hypotheses,
        "stop_reason": run["stop_reason"],
        "validation": {"result": "VALID"},
        "usage": {"api_requests": 0},
    }


def project_public_replay(source: dict[str, Any]) -> dict[str, Any]:
    _require(source.get("schema_version") == SOURCE_SCHEMA_VERSION, "unexpected source schema")
    _require(source.get("classification") == SOURCE_CLASSIFICATION, "unexpected source classification")
    activation = source.get("activation") or {}
    facts = activation.get("facts") or {}
    scenarios = [_project_scenario(item) for item in source.get("scenarios", [])]
    _require(len(scenarios) == 4, "public scenario replay requires four verified scenarios")
    return {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "classification": PUBLIC_CLASSIFICATION,
        "generated_at": source["generated_at"],
        "source_catalog_digest": source["catalog_digest"],
        "activation": {
            "condition": activation["condition"],
            "state": activation["state"],
            "policy_version": facts["policy_version"],
            "total_lag_records": list(facts["activation_window_total_lag_records"]),
            "lag_slope_records_per_second": list(
                facts["activation_window_lag_slope_records_per_second"]
            ),
        },
        "scenarios": scenarios,
        "boundary": {
            "recorded_replay": True,
            "openai_api_called": False,
            "runtime_source_called": False,
            "runtime_write_allowed": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("demo/verified-scenario-replays.json"),
    )
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    projected = project_public_replay(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(projected, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output.as_posix())


if __name__ == "__main__":
    main()
