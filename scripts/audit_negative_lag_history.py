"""Reconcile archived Kafka ranges and replay recovery without repairing evidence."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.evaluation_models import canonical_sha256
from ops_agent.incident_models import IncidentRecord
from ops_agent.models import EvidenceBundle
from ops_agent.recovery_evaluator import evaluate_recovery
from ops_agent.recovery_policies import load_recovery_policy
from ops_agent.sequence_evaluator import evaluate_bundle_sequence
from scripts.audit_runtime_case_candidates import read_json
from scripts.observe_worker_backlog import write_new

METRICS = ("kafka_topic_partition_current_offset", "kafka_consumergroup_current_offset", "kafka_consumergroup_lag")


def reconcile(bundle, raw):
    """Match labels and samples exactly; arithmetic is context, never replacement lag."""
    items = [e for e in bundle["evidence"] if e["metric"]["name"] in METRICS]
    points, series = [], {}
    for item in items:
        name = item["metric"]["name"]
        matches = [s for q in raw["queries"] if q["metric_name"] == name
            for s in q["series"] if s["labels"] == item["labels"]]
        if len(matches) != 1 or matches[0]["samples"] != item["metric"]["value"]:
            raise ValueError("raw/normalized range mismatch")
        partition = item["labels"]["partition"]
        key = (name, partition)
        if key in series:
            raise ValueError("duplicate partition series")
        series[key] = {p["timestamp"]: int(p["value"]) for p in matches[0]["samples"]}
    for (name, partition), samples in series.items():
        if name != METRICS[2]:
            continue
        for timestamp, lag in samples.items():
            if lag >= 0:
                continue
            end = series.get((METRICS[0], partition), {}).get(timestamp)
            committed = series.get((METRICS[1], partition), {}).get(timestamp)
            points.append({"partition": partition, "range_evaluation_timestamp": timestamp,
                "exporter_lag": lag, "end_offset": end, "committed_offset": committed,
                "end_minus_committed": None if end is None or committed is None else end - committed})
    return {"matched_series_count": len(items), "negative_points": points,
        "latest_source_timestamps": sorted({e["source_timestamp"] for e in items if e["source_timestamp"]}),
        "offset_decreases_within_range": [name + ":" + partition for (name, partition), samples in series.items()
            if name != METRICS[2] and any(b < a for a, b in zip(list(samples.values()), list(samples.values())[1:]))]}


def audit_run(run, *, root, activation_end, boundary, candidates):
    paths = sorted((run / "bundles").glob("sample-*.json"))
    indexes = [int(p.stem.split("-")[-1]) for p in paths]
    if indexes != list(range(len(paths))) or not 3 <= len(paths) <= 256:
        raise ValueError("complete zero-based bounded capture history required")
    records = [read_json(p) for p in paths]
    bundles = [EvidenceBundle.model_validate(d) for d, _ in records]
    activation = evaluate_bundle_sequence(bundles[activation_end - 2:activation_end + 1])
    if activation.conditions["CORE_BACKLOG_PRESSURE"].state.value != "PRESENT":
        raise ValueError("recorded activation no longer reproduces")
    ids = [b.incident_id for b in bundles[activation_end - 2:activation_end + 1]]
    incident_id = ids[0] if len(set(ids)) == 1 else ids[0].rsplit("-", 1)[0]
    rows, inventory = [], []
    for index, (path, (doc, digest)) in enumerate(zip(paths, records)):
        refs = {(e["raw_ref"], e["raw_sha256"]) for e in doc["evidence"] if e["metric"]["name"] in METRICS}
        if len(refs) != 1:
            raise ValueError("Kafka raw provenance is not unique")
        ref, expected = next(iter(refs))
        raw_path = Path(ref)
        if not raw_path.resolve().is_relative_to((run / "raw").resolve()):
            raise ValueError("raw reference escapes run")
        raw, actual = read_json(raw_path)
        if expected != actual:
            raise ValueError("raw digest mismatch")
        row = {"index": index, "collected_at": doc["collection"]["completed_at"], **reconcile(doc, raw)}
        rows.append(row)
        inventory.append({"bundle": path.relative_to(root).as_posix(), "bundle_sha256": digest,
            "raw": raw_path.relative_to(root).as_posix(), "raw_sha256": actual})
    post = bundles[activation_end + 1:]
    digests = [canonical_sha256(b.model_dump(mode="json")) for b in post]
    policy = load_recovery_policy(version="v2")
    first_closed = None
    states = []
    for count in range(1, len(post) + 1):
        ev = evaluate_recovery(incident_id=incident_id, activation_evaluation=activation,
            bundles=post[:count], source_bundle_digests=digests[:count], policy=policy)
        index = activation_end + count
        if ev.state.value == "WORKER_BACKLOG_RECOVERED" and first_closed is None:
            first_closed = index
        states.append({"index": index, "state": ev.state.value, "reason_codes": ev.reason_codes,
            "evaluation_id": ev.recovery_evaluation_id,
            "closure_already_observed": first_closed is not None})
    saved = []
    for p in sorted(run.glob("incident*.json")):
        doc, digest = read_json(p)
        record = IncidentRecord.model_validate(doc)
        saved.append({"path": p.relative_to(root).as_posix(), "sha256": digest,
            "lifecycle_state": record.lifecycle_state.value, "closed_at": doc["closed_at"],
            "last_observed_at": doc["timeline"][-1]["observed_at"],
            "recovery_evaluation_count": len(doc["recovery"]["evaluations"])})
    points = [p for row in rows for p in row["negative_points"]]
    summary = {"run": run.relative_to(root).as_posix(), "capture_count": len(rows),
        "activation_end_index": activation_end, "first_unknown_index_from_prior_audit": boundary,
        "negative_capture_count": sum(bool(r["negative_points"]) for r in rows),
        "negative_range_point_count": len(points), "negative_values": dict(Counter(p["exporter_lag"] for p in points)),
        "negative_points_matching_offset_difference": sum(p["exporter_lag"] == p["end_minus_committed"] for p in points),
        "offset_decrease_capture_count": sum(bool(r["offset_decreases_within_range"]) for r in rows),
        "first_recovered_prefix_index": first_closed, "saved_incidents": saved,
        "boundary_neighbors": rows[max(0, boundary - 1):boundary + 2],
        "candidate_states": [s for s in states if s["index"] in candidates],
        "first_usable_after_boundary": next((s for s in states if s["index"] > boundary and s["state"] != "WORKER_BACKLOG_UNKNOWN"), None),
        "automatic_relink_allowed": False, "exporter_root_cause_verified": False}
    return summary, {"inventory": inventory, "captures": rows, "recovery_prefixes": states}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    prior, prior_digest = read_json(args.input)
    root = ROOT / "results/ops-agent"
    groups = {}
    for c in prior["candidates"]:
        if c["status"] != "CONTINUITY_UNVERIFIED":
            continue
        run = (root / c["path"]).parent.parent
        if not run.resolve().is_relative_to(root.resolve()):
            raise ValueError("run escapes root")
        group = groups.setdefault(run, {"activation_end": c["activation_end_index"],
            "boundary": c["first_boundary"][0]["through_index"], "candidates": []})
        group["candidates"].append(c["candidate_index"])
    args.output_dir.mkdir(parents=True, exist_ok=False)
    write_new(args.output_dir / "plan.json", {"input_sha256": prior_digest,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "run_count": len(groups), "model_calls": 0, "cluster_writes": 0})
    summaries = []
    for i, (run, group) in enumerate(groups.items()):
        summary, detail = audit_run(run, root=root, **group)
        write_new(args.output_dir / f"run-{i + 1}.json", detail)
        summaries.append(summary)
        print(json.dumps({k: v for k, v in summary.items() if k not in ("boundary_neighbors", "candidate_states", "saved_incidents")}), flush=True)
    write_new(args.output_dir / "summary.json", {"schema_version": "ops.negative-lag-history-audit.v1",
        "runs": summaries, "model_calls": 0, "cluster_writes": 0,
        "limits": ["Range evaluation timestamps do not establish per-point scrape simultaneity.",
            "Overlapping range points are not independent exporter events.",
            "Recovery prefix replay is retrospective; saved incident records are not rewritten."]})


if __name__ == "__main__":
    main()
