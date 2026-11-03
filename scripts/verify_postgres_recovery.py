"""Read-only sampled recovery verification. Never patches or restarts a resource."""
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ops_agent.experiment_validation import RecoveryWindow
from scripts.run_worker_fault_diagnosis import kube, now, pod_projection, save

ST = "messaging-postgresql-ha-postgresql"


def run(directory, primary_uid, primary_restarts, duration=300):
    if not primary_uid or primary_restarts < 0 or duration < 180:
        raise ValueError("require pre-intervention primary UID/restarts and at least 180 seconds")
    directory.mkdir(parents=True, exist_ok=False)
    names = [f"{ST}-{i}" for i in range(3)]
    window = RecoveryWindow(names, names[0], primary_uid, primary_restarts)
    save(directory / "plan.json", {"at": now(), "expected_pods": names,
        "protected_primary_uid": primary_uid, "protected_primary_restarts": primary_restarts,
        "sample_interval_seconds": 15, "max_gap_seconds": 21, "required_sampled_span_seconds": 180,
        "duration_seconds": duration, "read_only": True, "traffic_verified": False})
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + duration
    index = 0
    complete = False
    while time.monotonic() < deadline:
        started = time.monotonic()
        pods, code, health, errors = [], None, {}, []
        try:
            pods = [pod_projection(p) for p in kube("get", "pods", "-l",
                "app.kubernetes.io/component=postgresql", "-o", "json")["items"]]
        except Exception as exc:
            errors.append(type(exc).__name__)
        try:
            with opener.open(Request("http://127.0.0.1/health/ready", headers={"Host": "localhost"}), timeout=5) as response:
                code, health = response.status, json.load(response)
            if not isinstance(health, dict):
                health = {}
                errors.append("InvalidHealthBody")
        except HTTPError as exc:
            code = exc.code
            exc.close()
        except (URLError, TimeoutError, ValueError):
            errors.append("HealthUnavailable")
        at = now()
        verdict = window.observe(pods, code, health, at)
        save(directory / "samples" / f"{index:03}.json", {"at": at, "pods": pods,
            "http_status": code, "health": health, "errors": errors, **verdict})
        index += 1
        if verdict["complete"]:
            complete = True
            break
        time.sleep(max(0, min(15 - (time.monotonic() - started), deadline - time.monotonic())))
    summary = {"at": now(), "status": "COMPLETE" if complete else "INCOMPLETE", "samples": index,
        "meaning": "sampled pod/readiness stability only; no traffic, failover or uninterrupted availability claim"}
    save(directory / "summary.json", summary)
    print(json.dumps(summary))
    return 0 if complete else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--primary-uid", required=True)
    parser.add_argument("--primary-restarts", type=int, required=True)
    parser.add_argument("--duration", type=int, default=300)
    args = parser.parse_args()
    raise SystemExit(run(args.output_dir, args.primary_uid, args.primary_restarts, args.duration))
