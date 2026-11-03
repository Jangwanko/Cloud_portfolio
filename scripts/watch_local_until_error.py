"""Observe local-ha until a service signal; retain quality warnings and continue."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ops_agent.controller import collect_bundle
from ops_agent.evaluator import evaluate_bundle
from ops_agent.policies import load_policy
from ops_agent.sequence_evaluator import evaluate_bundle_sequence
from scripts.observe_worker_backlog import write_new


def capture_errors(bundle):
    single = evaluate_bundle(bundle)
    capture = evaluate_bundle_sequence([bundle]).capture_observations[0]
    errors = []
    warnings = []
    db = single.conditions["DB_DEGRADED"]
    if db.state.value == "PRESENT":
        errors.append({"kind": "POSTGRES_READINESS", "reasons": db.reason_codes})
    elif db.state.value == "UNKNOWN":
        warnings.append({"kind": "READINESS_EVIDENCE_UNAVAILABLE", "reasons": db.reason_codes})
    for item in bundle.evidence:
        if item.metric.name == "application_readiness_observation" and isinstance(item.metric.value, dict):
            value = item.metric.value
            http_status = value.get("http_status")
            if type(http_status) is not int:
                warnings.append({"kind": "READINESS_TRANSPORT_UNAVAILABLE"})
            elif http_status >= 500 or value.get("body_status") in ("degraded", "not_ready"):
                errors.append({"kind": "APPLICATION_READINESS", "http_status": value.get("http_status"),
                    "body_status": value.get("body_status")})
    if not capture.required_evidence_usable:
        warnings.append({"kind": "OBSERVATION_QUALITY", "reasons": capture.reason_codes})
    return errors, warnings, capture


def watch(*, output_dir, minutes=30):
    if not 1 <= minutes <= 30:
        raise ValueError("watch budget must be 1 to 30 minutes")
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=False)
    stop = folder / "STOP"
    started = monotonic()
    incident_id = "watch-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    write_new(folder / "plan.json", {"incident_id": incident_id, "minutes": minutes,
        "watch_policy": "SERVICE_SIGNALS_STOP_QUALITY_WARNINGS_CONTINUE_V2",
        "capture_interval_seconds": 15, "max_events_per_batch": 60, "max_rate_per_second": 1,
        "context": "kind-messaging-ha", "model_calls": 0, "remediation": False,
        "code_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (Path(__file__), ROOT / "scripts/run_local_event_probe.py")}})
    process, log = None, None
    batch = 0
    captures = []
    outcome = {"status": "TIME_BUDGET_REACHED", "errors": [], "captures": 0, "warning_count": 0}
    try:
        next_start = started
        while monotonic() - started < minutes * 60:
            if next_start > monotonic():
                sleep(next_start - monotonic())
            if stop.exists():
                outcome["status"] = "STOP_REQUESTED"
                break
            if monotonic() - started >= minutes * 60:
                break
            tick = monotonic()
            bundle = collect_bundle(policy=load_policy("local-ha"), incident_id=incident_id,
                artifact_root=folder / "raw", cluster_context="kind-messaging-ha")
            index = len(captures)
            write_new(folder / "bundles" / f"sample-{index:03d}.json", bundle.model_dump(mode="json"))
            errors, warnings, capture = capture_errors(bundle)
            captures.append(bundle)
            if process is not None and process.poll() is not None and process.returncode != 0:
                errors.append({"kind": "EVENT_PROBE_FAILED", "batch": batch})
            row = {"index": index, "observed_at": bundle.collection.completed_at.isoformat(),
                "required_evidence_usable": capture.required_evidence_usable,
                "lag": capture.total_lag_records, "errors": errors, "warnings": warnings}
            write_new(folder / "checks" / f"check-{index:03d}.json", row)
            print(json.dumps(row), flush=True)
            outcome["captures"] = len(captures)
            outcome["warning_count"] += len(warnings)
            if errors:
                outcome.update(status="SERVICE_SIGNAL_DETECTED", errors=errors, first_error_index=index)
                break
            if process is None or process.poll() is not None:
                if log is not None:
                    log.close()
                batch += 1
                log = (folder / f"traffic-{batch:03d}.log").open("w", encoding="utf-8")
                process = subprocess.Popen([sys.executable, str(ROOT / "scripts/run_local_event_probe.py"),
                    "--output-dir", str(folder / f"traffic-{batch:03d}"), "--count", "60",
                    "--stop-file", str(stop)], cwd=ROOT, stdout=log, stderr=log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            next_start = tick + 15
    except Exception as exc:
        outcome.update(status="WATCH_FAILED", errors=[{"kind": type(exc).__name__}])
    finally:
        stop.touch(exist_ok=True)
        if process is not None:
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
                outcome["probe_forced_stop"] = True
            if process.returncode != 0:
                outcome["errors"].append({"kind": "EVENT_PROBE_EXIT", "exit_code": process.returncode})
                if outcome["status"] != "WATCH_FAILED":
                    outcome["status"] = "SERVICE_SIGNAL_DETECTED"
        if log is not None:
            log.close()
        outcome.update(elapsed_seconds=round(monotonic() - started, 2), batches=batch,
            model_calls=0, automatic_remediation=False)
        write_new(folder / "summary.json", outcome)
        print(json.dumps(outcome), flush=True)
    return outcome


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--minutes", type=int, default=30)
    args = parser.parse_args()
    result = watch(**vars(args))
    raise SystemExit(0 if result["status"] != "WATCH_FAILED" else 1)
