"""Sequential offline follow-up projection and fixed Runbook; no API option."""

import argparse
from collections import Counter
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.diagnosis_quality_v3 import fixed_decision_v3
from ops_agent.diagnosis_v3_validator import validate_decision_v3
from ops_agent.followup_replay import FollowupReplay, VERSION
from ops_agent.runtime_evidence_v3 import RUNTIME_TOOLS
from scripts.audit_runtime_case_candidates import read_json
from scripts.observe_worker_backlog import write_new


def replay_run(*, run_dir, activation_end, output_dir, through=None):
    run_dir, output_dir = Path(run_dir), Path(output_dir)
    if activation_end < 2:
        raise ValueError("activation end must include three captures")
    if through is None:
        through = max(int(p.stem.split("-")[-1]) for p in (run_dir / "bundles").glob("sample-*.json"))
    if not activation_end < through <= activation_end + 253:
        raise ValueError("one to 253 follow-up captures required")
    activation, activation_sha = read_json(run_dir / "conditions.v2.activation.json")
    originals = [read_json(run_dir / "bundles" / f"sample-{i:03d}.json")
        for i in range(activation_end - 2, activation_end + 1)]
    replay = FollowupReplay(activation_bundles=[d for d, _ in originals], activation=activation)
    output_dir.mkdir(parents=True, exist_ok=False)
    write_new(output_dir / "plan.json", {"version": VERSION, "activation_end": activation_end,
        "requested_through": through, "activation_file_sha256": activation_sha,
        "activation_bundle_file_sha256": [h for _, h in originals],
        "model_calls": 0, "cluster_writes": 0,
        "source_code_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [ROOT / "ops_agent/followup_replay.py", ROOT / "ops_agent/runtime_evidence_v3.py",
                ROOT / "ops_agent/recovery_evaluator.py", Path(__file__)]}})
    counts, rows = Counter(), []
    for index in range(activation_end + 1, through + 1):
        try:
            # Only this capture is read. No future values or terminal summary enter the session.
            path = run_dir / "bundles" / f"sample-{index:03d}.json"
            doc, digest = read_json(path)
            step = replay.append(doc)
            result = {"source_index": index, "source_file_sha256": digest, "step": step,
                "projection": [], "fixed_runbook": None, "fixed_contract_valid": None}
            if step["status"] == "ELIGIBLE":
                registry = replay.registry()
                evidence = [registry.execute(tool) for tool in RUNTIME_TOOLS]
                decision = fixed_decision_v3(activation=registry.activation, evidence=evidence)
                validate_decision_v3(decision, initial_evidence_ids=registry.activation["evidence_ids"],
                    acquired_evidence=evidence)
                result.update(projection=[e.model_dump(mode="json") for e in evidence],
                    fixed_runbook=decision.model_dump(mode="json"), fixed_contract_valid=True)
            write_new(output_dir / f"step-{index:03d}.json", result)
            counts[step["status"]] += 1
            rows.append({"source_index": index, "status": step["status"],
                "step_sha256": step["step_sha256"], "source_file_sha256": digest,
                "fixed_contract_valid": result["fixed_contract_valid"]})
        except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
            write_new(output_dir / "failure.json", {"source_index": index,
                "error_type": type(exc).__name__, "completed_steps": len(rows)})
            raise
    summary = {"version": VERSION, "status_counts": dict(counts), "steps": rows,
        "first_boundary": step["first_boundary"], "model_calls": 0, "cluster_writes": 0,
        "interpretation": "Offline prefix-only projection; fixed contract validity is not causal truth or LLM value."}
    write_new(output_dir / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--activation-end", type=int, required=True)
    parser.add_argument("--through", type=int)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = replay_run(**vars(args))
    print(summary["status_counts"])


if __name__ == "__main__":
    main()
