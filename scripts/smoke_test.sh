#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost}"

command -v python3 >/dev/null 2>&1 || {
  echo "Missing required command: python3" >&2
  exit 1
}

python3 - "$BASE_URL" <<'PY'
import json
import random
import sys
import time
import urllib.error
import urllib.request
import uuid

base_url = sys.argv[1].rstrip("/")


def request(
    method,
    path,
    body=None,
    token=None,
    idempotency_key=None,
    timeout=5,
    expected_status=None,
):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key

    req = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        if expected_status is not None and res.status != expected_status:
            raise SystemExit(
                f"Expected HTTP {expected_status} from {method} {path}, got {res.status}"
            )
        raw = res.read().decode()
        return json.loads(raw) if raw else {}


deadline = time.time() + 180
while time.time() < deadline:
    try:
        health = request("GET", "/health/ready")
        if health.get("status") == "ready":
            break
    except Exception:
        pass
    time.sleep(2)
else:
    raise SystemExit("Timed out waiting for API readiness")

suffix = f"{int(time.time() * 1000)}-{random.randint(0, 999999)}"
password = "Password123!"
u1_name = f"u{uuid.uuid4().hex[:12]}"
u2_name = f"u{uuid.uuid4().hex[:12]}"

u1 = request("POST", "/v1/users", {"username": u1_name, "password": password})
u2 = request("POST", "/v1/users", {"username": u2_name, "password": password})
u1_token = request("POST", "/v1/auth/login", {"username": u1_name, "password": password})["access_token"]
u2_token = request("POST", "/v1/auth/login", {"username": u2_name, "password": password})["access_token"]

stream = request(
    "POST",
    "/v1/streams",
    {"name": f"smoke-stream-{suffix}", "member_ids": [u1["id"], u2["id"]]},
    token=u1_token,
)

accepted = request(
    "POST",
    f"/v2/streams/{stream['id']}/events",
    {
        "event_type": "portfolio.smoke.probe",
        "payload": {"message": "hello smoke"},
        "metadata": {"scenario": "smoke-test"},
    },
    token=u1_token,
    idempotency_key=f"smoke-event-{suffix}",
    expected_status=202,
)
if accepted.get("schema_version") != 2 or accepted.get("event_type") != "portfolio.smoke.probe":
    raise SystemExit("Generic event envelope was not accepted as expected")
request_id = accepted["request_id"]

event_id = None
deadline = time.time() + 90
while time.time() < deadline:
    status = request("GET", f"/v2/event-requests/{request_id}", token=u1_token)
    if status.get("status") == "persisted" and status.get("event_id"):
        event_id = status["event_id"]
        break
    time.sleep(0.5)

if event_id is None:
    raise SystemExit("Event was not persisted in time")

events = request("GET", f"/v2/streams/{stream['id']}/events", token=u1_token)
event_items = events.get("items", [])
persisted = [
    item
    for item in event_items
    if item.get("request_id") == request_id
    and item.get("payload", {}).get("message") == "hello smoke"
]
if len(persisted) != 1:
    raise SystemExit("Persisted generic event payload was not returned by the read model")
request("POST", f"/v1/events/{event_id}/read", {}, token=u2_token)
unread = request("GET", f"/v1/streams/{stream['id']}/unread-count/{u2['id']}", token=u2_token)

print(
    f"health={health['status']} event_count={len(event_items)} "
    f"event_source={events['source']} unread={unread['unread']}"
)
PY
