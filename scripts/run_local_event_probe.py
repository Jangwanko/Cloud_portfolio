"""Bounded local v2 traffic probe; one unique user/stream, no reset or credentials on disk."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sys
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.observe_worker_backlog import write_new


def run(output_dir, count=60, stop_file=None):
    if not 1 <= count <= 60:
        raise ValueError("probe accepts 1 to 60 events")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    opener = build_opener(ProxyHandler({}))
    token = None
    run_id = "observation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    accepted = []
    summary = {"run_id": run_id, "target": "http://127.0.0.1", "max_events": count,
        "maximum_rate_per_second": 1, "status": "STARTED", "accepted_count": 0,
        "persisted_count": 0, "model_calls": 0, "data_reset": False}
    write_new(output_dir / "plan.json", summary)

    def request(method, path, body=None, extra=None, timeout=10):
        headers = {"Host": "localhost", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        headers.update(extra or {})
        req = Request("http://127.0.0.1" + path,
            data=None if body is None else json.dumps(body).encode(), method=method, headers=headers)
        with opener.open(req, timeout=timeout) as response:
            return response.status, json.load(response)

    try:
        _, health = request("GET", "/health/ready")
        if health.get("status") != "ready":
            raise ValueError("ready health required")
        username = "obs" + secrets.token_hex(6)
        password = secrets.token_urlsafe(24)
        _, user = request("POST", "/v1/users", {"username": username, "password": password})
        _, login = request("POST", "/v1/auth/login", {"username": username, "password": password})
        token = login["access_token"]
        _, stream = request("POST", "/v1/streams", {"name": run_id, "member_ids": [user["id"]]})
        summary["stream_id"] = stream["id"]
        for index in range(count):
            if stop_file is not None and Path(stop_file).exists():
                break
            started = monotonic()
            status, ack = request("POST", f"/v2/streams/{stream['id']}/events",
                {"event_type": "portfolio.observation.probe", "payload": {"sequence": index},
                 "metadata": {"scenario": run_id}}, {"X-Idempotency-Key": f"{run_id}-{index}"})
            if status != 202 or ack.get("schema_version") != 2 or ack.get("event_type") != "portfolio.observation.probe":
                raise ValueError("v2 accepted envelope mismatch")
            accepted.append({"sequence": index, "request_id": ack["request_id"]})
            write_new(output_dir / "accepted" / f"{index:03d}.json", accepted[-1])
            summary["accepted_count"] = len(accepted)
            if not isinstance(ack["request_id"], str) or not ack["request_id"] or len({a["request_id"] for a in accepted}) != len(accepted):
                raise ValueError("duplicate or invalid accepted request ID")
            sleep(max(0, 1 - (monotonic() - started)))
        pending = {item["request_id"] for item in accepted}
        deadline = monotonic() + 30
        poll_index = 0
        event_ids = set()
        while pending and monotonic() < deadline:
            for request_id in list(pending):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                try:
                    status, state = request("GET", f"/v2/event-requests/{request_id}", timeout=min(10, remaining))
                except HTTPError as exc:
                    write_new(output_dir / "polls" / f"{poll_index:05d}.json", {
                        "request_id": request_id, "http_status": exc.code, "observed_at": datetime.now(timezone.utc).isoformat()})
                    poll_index += 1
                    # Accepted Kafka records have no durable request row until
                    # Worker persistence. Only known accepted IDs may retry 404.
                    if exc.code == 404:
                        exc.close()
                        continue
                    raise
                write_new(output_dir / "polls" / f"{poll_index:05d}.json", {
                    "request_id": request_id, "http_status": status, "observed_at": datetime.now(timezone.utc).isoformat(),
                    "state": {k: state.get(k) for k in ("request_id", "status", "event_id", "schema_version")}})
                poll_index += 1
                if status == 200 and state.get("status") == "persisted" and state.get("event_id"):
                    if state.get("request_id", request_id) != request_id or state["event_id"] in event_ids:
                        raise ValueError("persistence ID mismatch or duplicate event ID")
                    event_ids.add(state["event_id"])
                    pending.remove(request_id)
                    summary["persisted_count"] += 1
            if pending:
                sleep(1)
        if pending or summary["persisted_count"] != summary["accepted_count"]:
            raise ValueError("persistence not confirmed for every accepted event")
        summary["status"] = "COMPLETE" if len(accepted) == count else "STOPPED"
    except Exception as exc:
        summary.update(status="FAILED", error_type=type(exc).__name__)
        if isinstance(exc, HTTPError):
            summary["http_status"] = exc.code
    write_new(output_dir / "summary.json", summary)
    print(json.dumps(summary))
    return 0 if summary["status"] in ("COMPLETE", "STOPPED") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--stop-file", type=Path)
    args = parser.parse_args()
    raise SystemExit(run(**vars(args)))
