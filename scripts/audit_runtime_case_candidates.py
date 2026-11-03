"""Audit saved runtime cases against unchanged v3.5 activation/projection gates."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.evaluation_models import canonical_sha256  # noqa: E402
from ops_agent.runtime_evidence_v3 import FrozenRuntimeRegistry  # noqa: E402
from ops_agent.sequence_evaluator import evaluate_bundle_sequence  # noqa: E402
from scripts.observe_worker_backlog import write_new  # noqa: E402

SOURCE_DIRECTORIES = ("calibration", "negative-control", "recovery-calibration",
    "recovered-calibration", "incident-e2e", "postgres-ha-recovery")
MAX_BYTES = 16 * 1024 * 1024


def read_json(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("candidate source must be a regular file")
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("candidate source exceeds 16 MiB")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def adverse_signals(bundle):
    """Discovery only; values here never establish source validity or causality."""
    signals = []
    for item in bundle.get("evidence", []):
        metric = item.get("metric", {})
        value = metric.get("value")
        if not isinstance(value, dict):
            continue
        if metric.get("name") == "kubernetes_worker_deployment_observation":
            desired, available = value.get("desired_replicas"), value.get("available_replicas")
            if type(desired) is int and type(available) is int and available < desired:
                signals.append("WORKER_REPLICA_SHORTFALL")
        elif metric.get("name") == "application_postgres_runtime_observation":
            checks = (("standby_count", 2), ("sync_standby_count", 1))
            delay = value.get("max_replication_delay_bytes")
            if (value.get("primary_reachable") is False or value.get("ha_mode") is False
                or any(type(value.get(key)) is int and value[key] < floor for key, floor in checks)
                or type(delay) is int and delay > 1048576):
                signals.append("POSTGRES_GUARDRAIL_ADVERSE")
    return sorted(set(signals))


def audit_candidate(path, *, source_root):
    doc, digest = read_json(path)
    result = {"path": path.relative_to(source_root).as_posix(), "file_sha256": digest,
        "bundle_id": doc.get("bundle_id"), "discovered_signals": adverse_signals(doc),
        "eligible": False, "exclusion": None, "activation_state": None,
        "causal_truth_verified": False}
    match = re.fullmatch(r"sample-(\d+)\.json", path.name)
    if not match or int(match[1]) < 2:
        result["exclusion"] = "THREE_CAPTURE_WINDOW_UNAVAILABLE"
        return result
    number, width = int(match[1]), len(match[1])
    inputs = [path.parent / f"sample-{index:0{width}d}.json" for index in range(number - 2, number + 1)]
    if not all(item.is_file() and not item.is_symlink() for item in inputs):
        result["exclusion"] = "THREE_CAPTURE_WINDOW_UNAVAILABLE"
        return result
    try:
        records = [read_json(item) for item in inputs]
        bundles = [item[0] for item in records]
        result["window_sources"] = [{"path": item.relative_to(source_root).as_posix(),
            "file_sha256": record[1], "canonical_sha256": canonical_sha256(record[0])}
            for item, record in zip(inputs, records)]
        evaluation = evaluate_bundle_sequence(bundles)
        core = evaluation.conditions["CORE_BACKLOG_PRESSURE"]
        result.update(activation_state=core.state.value, activation_reason_codes=core.reason_codes,
            sequence_evaluation_id=evaluation.evaluation_id,
            capture_observations=[{"usable": item.required_evidence_usable,
                "lag": item.total_lag_records, "slope": item.lag_slope_60s_records_per_second,
                "reason_codes": item.reason_codes} for item in evaluation.capture_observations])
        if core.state.value != "PRESENT":
            result["exclusion"] = "BACKLOG_ACTIVATION_NOT_PRESENT"
            return result
        source = FrozenRuntimeRegistry(bundles=bundles, evaluation=evaluation)
        tool = ("get_worker_replica_status" if "WORKER_REPLICA_SHORTFALL" in result["discovered_signals"]
                else "get_postgres_health")
        evidence = source.execute(tool)
        result["projection"] = evidence.model_dump(mode="json")
        expected = "WORKER_CAPACITY_SHORTFALL" if tool == "get_worker_replica_status" else "POSTGRES_PATH_DEGRADED"
        result["eligible"] = (evidence.status.value == "OK" and evidence.freshness.status.value == "FRESH"
            and evidence.summary.get("semantic_flag") == expected)
        if not result["eligible"]:
            result["exclusion"] = "ADVERSE_PROJECTION_NOT_USABLE"
    except (ValueError, TypeError, KeyError, OSError) as exc:
        result.update(exclusion="SOURCE_OR_EVALUATION_INVALID", error_type=type(exc).__name__)
    return result


def audit_archive(*, source_root, output_dir):
    source_root, output_dir = Path(source_root), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    write_new(output_dir / "plan.json", {"schema_version": "ops.runtime-case-audit.plan.v1",
        "created_at": datetime.now(timezone.utc).isoformat(), "source_directories": SOURCE_DIRECTORIES,
        "selection": "All discovered adverse snapshots; exactly three adjacent captures ending at each snapshot.",
        "policy": "UNCHANGED", "model_calls": 0, "cluster_writes": 0})
    inventory, candidates, counts, errors = [], [], Counter(), []
    for directory in SOURCE_DIRECTORIES:
        for path in sorted((source_root / directory).rglob("*.json")):
            if "raw" in path.relative_to(source_root).parts:
                continue
            try:
                doc, digest = read_json(path)
            except (ValueError, OSError) as exc:
                errors.append({"path": path.relative_to(source_root).as_posix(), "error_type": type(exc).__name__})
                continue
            if not isinstance(doc, dict) or doc.get("schema_version") != "ops.evidence.v1":
                continue
            counts[directory] += 1
            inventory.append({"path": path.relative_to(source_root).as_posix(), "file_sha256": digest,
                "bundle_id": doc.get("bundle_id")})
            if adverse_signals(doc):
                candidates.append(audit_candidate(path, source_root=source_root))
    summary = {"schema_version": "ops.runtime-case-audit.v1", "bundle_counts": dict(counts),
        "bundle_file_count": len(inventory), "candidate_snapshot_count": len(candidates),
        "candidate_signal_counts": dict(Counter(signal for item in candidates for signal in item["discovered_signals"])),
        "eligible_count": sum(item["eligible"] for item in candidates),
        "exclusion_counts": dict(Counter(item["exclusion"] for item in candidates if item["exclusion"])),
        "read_errors": errors, "model_calls": 0, "cluster_writes": 0,
        "limits": ["Archive observations; not independently established root causes.",
            "No historical activation combined with a later candidate snapshot.",
            "A zero eligible count describes this archive and unchanged gate only."], "candidates": candidates}
    write_new(output_dir / "inventory.json", inventory)
    write_new(output_dir / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT / "results/ops-agent")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = audit_archive(source_root=args.source_root, output_dir=args.output_dir)
    print(json.dumps({key: summary[key] for key in ("bundle_file_count", "candidate_snapshot_count", "eligible_count", "exclusion_counts")}))


if __name__ == "__main__":
    main()
