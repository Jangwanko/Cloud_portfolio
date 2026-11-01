"""Read-only temporal review of adverse archive snapshots; not a diagnosis adapter."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.evaluation_models import canonical_sha256
from ops_agent.models import EvidenceBundle
from ops_agent.recovery_evaluator import evaluate_recovery
from ops_agent.recovery_policies import load_recovery_policy
from ops_agent.sequence_evaluator import evaluate_bundle_sequence
from scripts.audit_runtime_case_candidates import read_json
from scripts.observe_worker_backlog import write_new


def review_sequence(documents):
    """Require an observed activation and uninterrupted, open recovery history.

    UNKNOWN is a review boundary, not proof of closure or of continued health.
    Every prefix is checked because recovery v2's final state alone cannot prove
    that a previous prefix did not already close the incident.
    """
    bundles = [EvidenceBundle.model_validate(item) for item in documents]
    if not 3 <= len(bundles) <= 256:
        raise ValueError("review requires 3 to 256 ordered captures")
    result = {"status": "NO_OBSERVED_ACTIVATION", "reviewable_followup": False,
        "runtime_adapter_eligible": False, "causal_truth_verified": False}
    activation = None
    for end in range(2, len(bundles)):
        candidate = evaluate_bundle_sequence(bundles[end - 2:end + 1])
        if candidate.conditions["CORE_BACKLOG_PRESSURE"].state.value == "PRESENT":
            activation = candidate
            break
    if activation is None:
        return result
    result.update(activation_evaluation_id=activation.evaluation_id,
        activation_end_index=end, candidate_index=len(bundles) - 1,
        activation_bundle_digests=[canonical_sha256(b.model_dump(mode="json")) for b in bundles[end - 2:end + 1]])
    if end == len(bundles) - 1:
        result["status"] = "ACTIVATION_SNAPSHOT_NOT_FOLLOWUP"
        return result
    ids = [b.incident_id for b in bundles[end - 2:end + 1]]
    if len(set(ids)) == 1:
        incident_id = ids[0]
    else:
        parts = [value.rsplit("-", 1) for value in ids]
        if not all(len(p) == 2 and p[1].isdigit() for p in parts) or len({p[0] for p in parts}) != 1:
            result["status"] = "ACTIVATION_IDENTITY_AMBIGUOUS"
            return result
        incident_id = parts[0][0]
    post = bundles[end + 1:]
    digests = [canonical_sha256(b.model_dump(mode="json")) for b in post]
    result["recovery_prefixes"] = []
    for count in range(1, len(post) + 1):
        evaluation = evaluate_recovery(incident_id=incident_id,
            activation_evaluation=activation, bundles=post[:count],
            source_bundle_digests=digests[:count], policy=load_recovery_policy(version="v2"))
        result["recovery_prefixes"].append({"through_index": end + count,
            "evaluation_id": evaluation.recovery_evaluation_id, "state": evaluation.state.value,
            "reason_codes": evaluation.reason_codes})
        if evaluation.state.value == "WORKER_BACKLOG_RECOVERED":
            result["status"] = "INCIDENT_CLOSED_BEFORE_OR_AT_CANDIDATE"
            return result
        if evaluation.state.value == "WORKER_BACKLOG_UNKNOWN":
            result["status"] = "CONTINUITY_UNVERIFIED"
            return result
    result.update(status="OPEN_INCIDENT_FOLLOWUP_REVIEWABLE", reviewable_followup=True)
    return result


def review_candidate(path, source_root):
    result = {"path": path.relative_to(source_root).as_posix(), "reviewable_followup": False,
        "runtime_adapter_eligible": False, "causal_truth_verified": False}
    try:
        number = int(path.stem.removeprefix("sample-"))
        paths = sorted((p for p in path.parent.glob("sample-*.json")
            if p.stem.removeprefix("sample-").isdigit()
            and int(p.stem.removeprefix("sample-")) <= number),
            key=lambda p: int(p.stem.removeprefix("sample-")))
        indexes = [int(p.stem.removeprefix("sample-")) for p in paths]
        if not indexes or indexes[0] not in (0, 1) or indexes != list(range(indexes[0], number + 1)):
            result["status"] = "CAPTURE_SEQUENCE_GAP"
            return result
        if len(paths) < 3:
            result["status"] = "INSUFFICIENT_CAPTURE_HISTORY"
            return result
        if len(paths) > 256:
            result["status"] = "CAPTURE_LIMIT_EXCEEDED"
            return result
        records = [read_json(p) for p in paths]
        result["sources"] = [{"path": p.relative_to(source_root).as_posix(), "file_sha256": record[1]}
            for p, record in zip(paths, records)]
        result.update(review_sequence([record[0] for record in records]))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        result.update(status="SOURCE_OR_EVALUATION_INVALID", error_type=type(exc).__name__)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=ROOT / "results/ops-agent")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    audit, digest = read_json(args.audit)
    root = args.source_root.resolve()
    paths = [(root / item["path"]).resolve() for item in audit["candidates"]]
    if any(not p.is_relative_to(root) for p in paths):
        raise ValueError("candidate escapes source root")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    write_new(args.output_dir / "plan.json", {"audit_sha256": digest,
        "script_sha256": canonical_sha256(Path(__file__).read_text(encoding="utf-8")),
        "selection": "All candidates from input audit; no sampling or model calls",
        "unknown_policy": "Stop at first UNKNOWN; closure is sticky; no reactivation inferred"})
    candidates = [review_candidate(p, root) for p in paths]
    summary = {"schema_version": "ops.incident-followup-audit.v1", "candidate_count": len(candidates),
        "status_counts": dict(Counter(item["status"] for item in candidates)),
        "reviewable_followup_count": sum(item["reviewable_followup"] for item in candidates),
        "model_calls": 0, "cluster_writes": 0, "candidates": candidates,
        "limits": ["Temporal eligibility only; adverse signal validity and causality not established.",
            "Existing runtime adapter still projects activation, not these follow-ups.",
            "UNKNOWN blocks automatic linkage; it does not prove incident absence."]}
    write_new(args.output_dir / "summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "candidates"}))


if __name__ == "__main__":
    main()
