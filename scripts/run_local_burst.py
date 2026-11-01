"""Bounded localhost-only event burst with read-only observation and sampled persistence.

No deployment changes, topic resets or automatic retry of event POSTs.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ops_agent.calibration import summarize_bundle
from ops_agent.controller import collect_bundle
from ops_agent.policies import load_policy
from ops_agent.runbook_assistant import save


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class LocalClient:
    def __init__(self):
        self.token = None
        self.local = threading.local()

    def request(self, method, path, body=None, key=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("only local API paths allowed")
        if not hasattr(self.local, "opener"):
            self.local.opener = build_opener(ProxyHandler({}), NoRedirect())
        headers = {"Host": "localhost", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if key:
            headers["X-Idempotency-Key"] = key
        request = Request("http://127.0.0.1" + path, method=method, headers=headers,
                          data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.local.opener.open(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            try:
                return exc.code, json.load(exc)
            finally:
                exc.close()


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_limits(rate, seconds, workers):
    if not (1 <= rate <= 300 and 1 <= seconds <= 20 and 1 <= workers <= 32 and rate * seconds <= 6000):
        raise ValueError("limit: 300/s, 20 seconds, 32 in flight, 6000 total")


def sampled_ids(receipts, limit=60):
    ids = [r["request_id"] for r in receipts if r["status"] == 202]
    if any(not isinstance(r, str) or not r for r in ids) or len(ids) != len(set(ids)):
        raise ValueError("invalid or duplicate accepted request ID")
    count = min(limit, len(ids))
    return [ids[i * (len(ids) - 1) // max(1, count - 1)] for i in range(count)]


def burst(send, rate, seconds, workers, stop, receipt_path):
    validate_limits(rate, seconds, workers)
    # GetTickCount64-backed monotonic can tick only every 15.625 ms on Windows.
    # perf_counter is monotonic too, but uses the high-resolution performance clock.
    started = time.perf_counter()
    deadline = started + seconds
    next_send = started
    receipts, pending = [], {}
    sent = 0
    reason = "TIME_LIMIT"
    consecutive_errors = 0
    with receipt_path.open("x", encoding="utf-8") as log, ThreadPoolExecutor(max_workers=workers) as pool:
        while pending or (time.perf_counter() < deadline and not stop.is_set() and sent < rate * seconds):
            for future in list(pending):
                if not future.done():
                    continue
                sequence = pending.pop(future)
                try:
                    row = {"sequence": sequence, **future.result()}
                except Exception as exc:
                    row = {"sequence": sequence, "status": None, "error_type": type(exc).__name__, "at": now()}
                receipts.append(row)
                log.write(json.dumps(row) + "\n")
                log.flush()
                consecutive_errors = 0 if row["status"] == 202 else consecutive_errors + 1
                recent = receipts[-50:]
                if consecutive_errors >= 5 or (len(recent) == 50 and sum(r["status"] != 202 for r in recent) >= 10):
                    reason = "REQUEST_ERROR_LIMIT"
                    stop.set()
            current = time.perf_counter()
            if current < deadline and not stop.is_set() and sent < rate * seconds and len(pending) < workers and current >= next_send:
                pending[pool.submit(send, sent)] = sent
                sent += 1
                # No catch-up burst after scheduler stalls.
                next_send = current + 1 / rate
            elif pending or (current < deadline and not stop.is_set()):
                time.sleep(0.001)
        if stop.is_set() and reason == "TIME_LIMIT":
            reason = "EXTERNAL_STOP"
    return receipts, {"submitted":sent, "elapsed_seconds":time.perf_counter()-started,
        "stop_reason":reason, "status_counts":dict(Counter(str(r["status"]) for r in receipts))}


def run(directory, rate=300, seconds=20, workers=32):
    validate_limits(rate, seconds, workers)
    directory.mkdir(parents=True, exist_ok=False)
    client = LocalClient()
    stop = threading.Event()
    samples = []
    incident = "bounded-burst-" + secrets.token_hex(6)
    save(directory / "plan-private.json", {"at":now(), "incident_id":incident,
        "target":"http://127.0.0.1", "host_header":"localhost", "rate_limit":rate,
        "duration_limit_seconds":seconds, "event_limit":rate*seconds, "in_flight_limit":workers,
        "streams":16, "request_timeout_seconds":3, "recovery_observation_limit_seconds":120,
        "stop_conditions":["5 consecutive POST failures", "10 failures in last 50 POSTs",
                           "2 consecutive readiness/collector failures", "STOP file"],
        "persistence_verification":"at most 60 deterministic sampled IDs; not all accepted events",
        "deployment_changes":False, "data_reset":False, "model_calls":0,
        "source_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    (directory / "runner-source.py").write_bytes(Path(__file__).read_bytes())
    policy = load_policy("local-ha")
    def capture(phase):
        index = len(samples)
        bundle = collect_bundle(policy=policy, incident_id=incident, artifact_root=directory / "raw",
                                cluster_context="kind-messaging-ha")
        save(directory / "bundles" / f"{index:03d}.json", bundle.model_dump(mode="json"))
        summary = summarize_bundle(bundle)
        row = {"at":now(), "phase":phase, "bundle":f"bundles/{index:03d}.json", "summary":summary}
        save(directory / "samples" / f"{index:03d}.json", row)
        samples.append(row)
        print(json.dumps({"phase":phase, "sample":index, "lag":summary["kafka"]["total_lag"],
                          "readiness":summary["postgres"]["readiness_body_status"]}), flush=True)
        return summary
    baseline = capture("baseline")
    code, health = client.request("GET", "/health/ready")
    if (code != 200 or health.get("status") != "ready" or baseline["kafka"]["anomalies"]
            or baseline["kafka"]["total_lag"] != 0
            or baseline["worker"]["available_replicas"] != baseline["worker"]["desired_replicas"]):
        save(directory / "summary.json", {"status":"BASELINE_NOT_READY", "traffic_sent":False})
        return 1
    username, password = "burst" + secrets.token_hex(6), secrets.token_urlsafe(24)
    code, user = client.request("POST", "/v1/users", {"username":username,"password":password})
    if code not in (200,201): raise ValueError("user setup failed")
    code, login = client.request("POST", "/v1/auth/login", {"username":username,"password":password})
    if code != 200: raise ValueError("login failed")
    client.token = login["access_token"]
    streams = []
    for i in range(16):
        code, stream = client.request("POST", "/v1/streams", {"name":incident+f"-{i}","member_ids":[user["id"]]})
        if code not in (200,201): raise ValueError("stream setup failed")
        streams.append(stream["id"])
    save(directory / "stream-ids.json", streams)
    def send(sequence):
        start = time.perf_counter()
        code, ack = client.request("POST", f"/v2/streams/{streams[sequence % len(streams)]}/events",
            {"event_type":"portfolio.burst.probe", "payload":{"sequence":sequence},
             "metadata":{"scenario":incident}}, key=f"{incident}-{sequence}")
        if code == 202 and (ack.get("schema_version") != 2 or ack.get("event_type") != "portfolio.burst.probe"):
            raise ValueError("accepted envelope mismatch")
        return {"status":code, "request_id":ack.get("request_id") if code == 202 else None,
                "latency_seconds":time.perf_counter()-start, "at":now()}
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(burst, send, rate, seconds, workers, stop, directory / "receipts.jsonl")
        unhealthy = 0
        while not task.done():
            if (directory / "STOP").exists(): stop.set()
            try:
                summary = capture("load")
                unhealthy = 0 if summary["postgres"]["readiness_body_status"] == "ready" else unhealthy+1
            except Exception as exc:
                unhealthy += 1
                save(directory / f"observation-error-{time.time_ns()}.json", {"at":now(),"error_type":type(exc).__name__})
            if unhealthy >= 2: stop.set()
            if not task.done(): time.sleep(5)
        receipts, load = task.result()
    save(directory / "load-summary.json", load)
    selected = sampled_ids(sorted(receipts,key=lambda r:r["sequence"]))
    save(directory / "sampled-request-ids.json", selected)
    deadline = time.perf_counter()+120
    stable = 0
    while time.perf_counter() < deadline:
        summary = capture("recovery")
        healthy = (not summary["kafka"]["anomalies"] and summary["kafka"]["total_lag"] == 0
                   and summary["postgres"]["readiness_body_status"] == "ready"
                   and summary["worker"]["available_replicas"] == summary["worker"]["desired_replicas"])
        stable = stable+1 if healthy else 0
        if stable >= 3: break
        time.sleep(5)
    polls = []
    for rid in selected:
        try:
            code, state = client.request("GET", f"/v2/event-requests/{rid}")
            polls.append({"request_id":rid,"http_status":code,"at":now(),
                "state":{k:state.get(k) for k in ("request_id","status","event_id")}})
        except Exception as exc:
            polls.append({"request_id":rid,"http_status":None,"at":now(),"error_type":type(exc).__name__})
        time.sleep(0.05)
    save(directory / "persistence-samples.json", polls)
    event_ids = [r.get("state",{}).get("event_id") for r in polls]
    sample_ok = bool(polls) and all(r["http_status"] == 200 and r["state"]["status"] == "persisted"
        and r["state"].get("request_id") in (None,r["request_id"]) and r["state"].get("event_id") for r in polls)
    sample_ok = sample_ok and len(set(event_ids)) == len(event_ids)
    peak = max((r["summary"]["kafka"]["total_lag"] for r in samples if r["summary"]["kafka"]["total_lag"] is not None),default=None)
    result = {"status":"OBSERVATION_COMPLETED" if stable>=3 and sample_ok else "INCOMPLETE",
        "load":load,"accepted":sum(r["status"]==202 for r in receipts),"observed_peak_lag":peak,
        "consecutive_recovery_samples":stable,"persistence_sample_count":len(polls),"persistence_samples_pass":sample_ok,
        "all_accepted_persistence_verified":False,"samples":len(samples),"model_calls":0,
        "deployment_configuration_changes":False,"created_test_users":1,"created_test_streams":len(streams),
        "interpretation":"Short local workload observation; no DDoS resilience, general capacity or losslessness claim."}
    save(directory / "summary.json", result)
    print(json.dumps(result),flush=True)
    return 0 if result["status"] == "OBSERVATION_COMPLETED" else 1


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--rate",type=int,default=300)
    parser.add_argument("--seconds",type=int,default=20)
    parser.add_argument("--workers",type=int,default=32)
    args=parser.parse_args()
    raise SystemExit(run(args.output_dir,args.rate,args.seconds,args.workers))
