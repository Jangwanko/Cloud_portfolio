"""Capture three read-only bundles and persist the unmodified activation gate."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import monotonic, sleep

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops_agent.controller import collect_bundle  # noqa: E402
from ops_agent.observation import summarize_observation  # noqa: E402
from ops_agent.policies import load_policy  # noqa: E402


def write_new(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, indent=2) + "\n")


def capture_observation(*, output_dir, context="kind-messaging-ha", interval_seconds=15,
                        collector=None, clock=monotonic, wait=sleep):
    """Bounded three-capture run. Keep partial files on failure; never overwrite."""
    if not 9 <= interval_seconds <= 30:
        raise ValueError("capture interval must be between 9 and 30 seconds")
    if context != "kind-messaging-ha":
        raise ValueError("the calibrated observation profile requires kind-messaging-ha")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    collector = collector or collect_bundle
    policy = load_policy("local-ha")
    incident = "observation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    write_new(output_dir / "plan.json", {
        "schema_version": "ops.observation.plan.v1", "incident_id": incident,
        "capture_count": 3, "interval_seconds": interval_seconds,
        "context": context, "policy": policy, "model_calls": 0,
        "source_sha256": {str(path.relative_to(ROOT)).replace("\\", "/"):
            hashlib.sha256(path.read_bytes()).hexdigest() for path in (
                Path(__file__), ROOT / "ops_agent/observation.py")},
    })
    bundles = []
    try:
        next_start = clock()
        for index in range(3):
            delay = next_start - clock()
            if delay > 0:
                wait(delay)
            started = clock()
            bundle = collector(policy=policy, incident_id=incident,
                artifact_root=output_dir / "raw", cluster_context=context)
            write_new(output_dir / "bundles" / f"sample-{index + 1:03d}.json",
                      bundle.model_dump(mode="json"))
            bundles.append(bundle)
            next_start = started + interval_seconds
        summary, sequence = summarize_observation(bundles)
        write_new(output_dir / "conditions.json", sequence)
        write_new(output_dir / "summary.json", summary)
    except Exception as exc:
        # No arbitrary transport error text, environment values or credentials.
        write_new(output_dir / "failure.json", {"status": "FAILED",
            "completed_captures": len(bundles), "error_type": type(exc).__name__})
        raise
    write_new(output_dir / "inventory.json", {
        "schema_version": "ops.observation.inventory.v1", "status": "COMPLETED",
        "files": {path.relative_to(output_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted(output_dir.rglob("*")) if path.is_file()},
    })
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--context", default="kind-messaging-ha")
    parser.add_argument("--interval-seconds", type=float, default=15)
    args = parser.parse_args(argv)
    summary = capture_observation(output_dir=args.output_dir, context=args.context,
        interval_seconds=args.interval_seconds)
    print(json.dumps({"output_directory": str(args.output_dir),
        "activation_state": summary["activation_state"], "diagnosis_gate": summary["diagnosis_gate"],
        "model_api_requests": summary["model"]["api_request_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
