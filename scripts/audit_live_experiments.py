"""Offline audit of recorded experiments and non-mutating harness counterexamples.

Does not call a model, kubectl, or any HTTP endpoint. It preserves all source artifacts.
Counterexamples describe existing bugs; their reproduction is not a passing safety gate.
"""
import argparse
import ast
from datetime import datetime
import hashlib
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_worker_fault_diagnosis as drill
from scripts import watch_local_until_error as watcher
from scripts import run_local_event_probe as probe
from ops_agent.diagnosis_agent import ModelResponse
from ops_agent.models import EvidenceBundle
from ops_agent.experiment_validation import require_stalled_requests, RecoveryWindow


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def hashes(summary, field, root):
    problems = []
    for name, expected in summary[field].items():
        path = root / name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            problems.append(name)
    return {"checked": len(summary[field]), "mismatches": problems}


def wire_audit(folder):
    evidence = read(folder / "evidence.json")
    calls = {}
    delivered = []
    mismatches = []
    files = sorted((folder / "model").glob("*-response.json"))
    for file in files:
        for item in read(file)["output"]:
            if item.get("type") == "function_call":
                calls[item["call_id"]] = item["name"]
    for file in sorted((folder / "model").glob("*-request.json")):
        for item in read(file)["input"]:
            if item.get("type") == "function_call_output":
                name = calls[item["call_id"]]
                delivered.append(name)
                if json.loads(item["output"]) != evidence[name]:
                    mismatches.append(name)
    initial = (folder / "model/0-request.json").read_text(encoding="utf-8")
    return {"requests": len(files), "delivered_tools": delivered,
            "tool_output_mismatches": mismatches,
            "initial_input_contains_worker_fault_module": "worker.consumer" in initial,
            "contains_private_plan_filename": "plan-private.json" in initial}


def reproduce(folder):
    findings = {}
    # Execute the actual watcher branch; mock only upstream evidence evaluators.
    single = SimpleNamespace(conditions={"DB_DEGRADED": SimpleNamespace(
        state=SimpleNamespace(value="UNKNOWN"), reason_codes=[])})
    capture = SimpleNamespace(required_evidence_usable=False, reason_codes=[])
    bundle = SimpleNamespace(evidence=[SimpleNamespace(metric=SimpleNamespace(
        name="application_readiness_observation", value={"http_status": None}))])
    with patch.object(watcher, "evaluate_bundle", return_value=single), patch.object(
            watcher, "evaluate_bundle_sequence", return_value=SimpleNamespace(capture_observations=[capture])):
        try:
            watcher.capture_errors(bundle)
            findings["null_http_status"] = "NO_EXCEPTION"
        except Exception as exc:
            findings["null_http_status"] = type(exc).__name__
    # Repeat against a real saved bundle and the actual evaluators, changing
    # only the HTTP status to the value emitted for an unreachable endpoint.
    document = read(ROOT / "results/ops-agent/shadow/20261031-service-watch-30min/bundles/sample-000.json")
    item = next(e for e in document["evidence"] if e["metric"]["name"] == "application_readiness_observation")
    item["metric"]["value"]["http_status"] = None
    try:
        watcher.capture_errors(EvidenceBundle.model_validate(document))
        findings["null_http_status_real_evaluators"] = "NO_EXCEPTION"
    except Exception as exc:
        findings["null_http_status_real_evaluators"] = type(exc).__name__
    # Execute the actual model loop with empty output and no tool investigation.
    fake = SimpleNamespace(create=lambda _: ModelResponse("empty", [], None, 0, 0, 0))
    try:
        drill.diagnose(folder / "empty-model", {}, {}, fake, "offline-test")
    except ValueError:
        pass
    findings["empty_model_output_recorded_as_completed"] = (folder / "empty-model/model-result.json").exists()
    findings["stall_gate_rejects_by_http_status"] = {}
    for status in (401, 404, 503):
        try:
            require_stalled_requests(["r"], [{"request_id": "r", "http_status": status,
                "body": {"detail": "Request not found" if status == 404 else "unavailable"}}])
            rejected = False
        except ValueError:
            rejected = True
        findings["stall_gate_rejects_by_http_status"][str(status)] = rejected
    # Compile the exact PG success predicate from its frozen verification source.
    pg_source = ROOT / "results/ops-agent/shadow/20261101-postgres-restarts-01/verify_recovery.py"
    pg_tree = ast.parse(pg_source.read_text(encoding="utf-8"))
    predicate = next(n.value for n in ast.walk(pg_tree) if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "healthy" for t in n.targets))
    findings["frozen_old_pg_gate_accepts_only_primary_with_ready_health"] = bool(eval(
        compile(ast.Expression(predicate), "actual_pg_gate", "eval"),
        {"protected": True, "health": {"status": "ready"}, "pods": [
            {"status": {"containerStatuses": [{"ready": True}]}}]}))
    window = RecoveryWindow(["pg-0", "pg-1", "pg-2"], "pg-0", "uid", 0)
    findings["current_pg_gate_accepts_only_primary"] = window.observe([
        {"name": "pg-0", "uid": "uid", "containers": [{"name": "pg", "ready": True, "restartCount": 0}]}],
        200, {"status": "ready"}, "2026-11-01T00:00:00+00:00")["healthy"]
    # Simulate successful commands but failed field comparison; never call kubectl.
    rollback = {"rollback_deadline_epoch": 0}
    watchdir = folder / "watchdog-noop"
    drill.save(watchdir / "rollback.json", rollback)
    with patch.object(drill, "restore", return_value=None), patch.object(drill, "restore_policy", return_value=None), patch.object(
            drill, "verify_restored", side_effect=ValueError("drift")), patch.object(drill.time, "sleep"):
        drill.watchdog(watchdir)
    findings["watchdog_marks_restored_without_field_verification"] = (watchdir / "watchdog-restored.json").exists()
    class Response(StringIO):
        def __init__(self, body, status=200):
            super().__init__(json.dumps(body))
            self.status = status

    class DuplicateAckOpener:
        def open(self, request, timeout):
            url = request.full_url
            if url.endswith("/health/ready"):
                return Response({"status": "ready"})
            if url.endswith("/v1/users"):
                return Response({"id": 1})
            if url.endswith("/v1/auth/login"):
                return Response({"access_token": "offline-placeholder"})
            if url.endswith("/v1/streams"):
                return Response({"id": 2})
            if url.endswith("/events"):
                return Response({"request_id": "same-id-for-three-distinct-events", "schema_version": 2,
                    "event_type": "portfolio.observation.probe"}, 202)
            return Response({"status": "persisted", "event_id": 123})

    with patch.object(probe, "build_opener", return_value=DuplicateAckOpener()), patch.object(
            probe, "sleep", return_value=None), redirect_stdout(StringIO()):
        exit_code = probe.run(folder / "duplicate-ack", count=3)
    findings["duplicate_request_id_probe"] = {"exit_code": exit_code,
        "summary": read(folder / "duplicate-ack/summary.json")}
    return findings


def audit(output):
    output.mkdir(parents=True, exist_ok=False)
    base = ROOT / "results/ops-agent"
    worker = base / "shadow/20261101-worker-startup-fault-01"
    pg = base / "shadow/20261101-postgres-restarts-01"
    worker_summary = read(base / "diagnosis/worker-startup-fault-20261101-summary.json")
    pg_summary = read(base / "diagnosis/postgres-restarts-20261101-summary.json")
    accepted = [read(f)["request_id"] for f in (worker / "accepted").glob("*.json")]
    recovered = read(worker / "recovery.json")["requests"]
    observed = read(worker / "symptom.json")["requests"]
    pg_accepted = [read(f)["request_id"] for f in (pg / "recovery-traffic/accepted").glob("*.json")]
    rows = [read(f) for f in sorted((pg / "verification").glob("*.json"))]
    # Derive the last contiguous healthy suffix; do not select a known count.
    stable = []
    for row in reversed(rows):
        if not row["healthy"]:
            break
        stable.append(row)
        if row["stable_seconds"] == 0:
            break
    stable.reverse()
    times = [datetime.fromisoformat(r["at"]) for r in stable]
    watchdir = base / "shadow/20261031-service-watch-30min"
    checks = [read(f) for f in sorted((watchdir / "checks").glob("*.json"))]
    batches = [read(f) for f in watchdir.glob("traffic-*/summary.json")]
    result = {
        "scope": "offline review of live watch, Worker fault and PostgreSQL recovery; historical synthetic model scores not rerun",
        "cluster_mutations": 0, "model_calls": 0,
        "worker": {"hash_integrity": hashes(worker_summary, "hashes", worker), "wire": wire_audit(worker),
            "accepted_unique": len(set(accepted)), "observed_http_statuses": sorted({s["http_status"] for s in observed}),
            "recovered_request_ids_match": set(accepted) == {r["request_id"] for r in recovered},
            "recovered_unique_event_ids": len({r["body"].get("event_id") for r in recovered}),
            "recovered_statuses": sorted({r["body"].get("status") for r in recovered})},
        "postgres": {"hash_integrity": hashes(pg_summary, "artifact_sha256", pg), "wire": wire_audit(pg),
            "samples": len(rows), "healthy_suffix_samples": len(stable),
            "healthy_suffix_all_healthy": all(r["healthy"] for r in stable),
            "sampled_span_seconds": (times[-1] - times[0]).total_seconds(),
            "largest_sample_gap_seconds": max((b-a).total_seconds() for a,b in zip(times,times[1:])),
            "pod_uids_saved_in_samples": any("uid" in pod for row in rows for pod in row["pods"]),
            "pre_correction_timestamp": read(pg / "before-correction.json")["at"],
            "verification_plan_timestamp": read(pg / "verification-plan.json")["at"],
            "raw_traffic_poll_responses_saved": False,
            "accepted_files": len(pg_accepted), "accepted_unique": len(set(pg_accepted)),
            "traffic_summary": read(pg / "recovery-traffic/summary.json")},
        "unfinished_watch": {"checks": len(checks), "first": checks[0]["observed_at"], "last": checks[-1]["observed_at"],
            "summary_exists": (watchdir / "summary.json").exists(), "completed_batches": len(batches),
            "persisted_in_completed_batches": sum(b["persisted_count"] for b in batches),
            "accepted_files": len(list(watchdir.glob("traffic-*/accepted/*.json")))},
        "counterexamples": reproduce(output / "counterexamples"),
        "source_sha256": {str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [Path(__file__), ROOT / "scripts/run_worker_fault_diagnosis.py", ROOT / "scripts/watch_local_until_error.py", ROOT / "scripts/run_local_event_probe.py"]}}
    drill.save(output / "summary.json", result)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    audit(parser.parse_args().output_dir)
