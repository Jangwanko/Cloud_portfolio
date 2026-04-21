#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost}"
NAMESPACE="${NAMESPACE:-messaging-app}"
REDIS_WORKLOAD="${REDIS_WORKLOAD:-messaging-redis-node}"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

workload_ref() {
  local name="$1"
  local ref
  ref="$(kubectl -n "$NAMESPACE" get statefulset "$name" --ignore-not-found -o name)"
  if [[ -n "$ref" ]]; then
    printf '%s\n' "$ref"
    return 0
  fi
  ref="$(kubectl -n "$NAMESPACE" get deployment "$name" --ignore-not-found -o name)"
  if [[ -n "$ref" ]]; then
    printf '%s\n' "$ref"
    return 0
  fi
  echo "Workload not found: $name" >&2
  return 1
}

base_replicas() {
  if [[ "$1" == "messaging-redis-node" ]]; then
    printf '3\n'
  else
    printf '1\n'
  fi
}

wait_ready() {
  local timeout="${1:-180}"
  python3 - "$BASE_URL" "$timeout" <<'PY'
import json
import sys
import time
import urllib.request

base_url = sys.argv[1].rstrip("/")
deadline = time.time() + int(sys.argv[2])

while time.time() < deadline:
    try:
        with urllib.request.urlopen(f"{base_url}/health/ready", timeout=5) as res:
            body = json.loads(res.read().decode())
        if body.get("status") == "ready":
            raise SystemExit(0)
    except Exception:
        pass
    time.sleep(2)

raise SystemExit("Timed out waiting for readiness")
PY
}

require_command kubectl
require_command python3

redis_ref="$(workload_ref "$REDIS_WORKLOAD")"
target_replicas="$(base_replicas "$REDIS_WORKLOAD")"

restore_redis() {
  kubectl -n "$NAMESPACE" scale "$redis_ref" --replicas="$target_replicas" >/dev/null || true
}
trap restore_redis EXIT

wait_ready 180

setup_json="$(python3 - "$BASE_URL" <<'PY'
import json
import random
import sys
import time
import urllib.request
import uuid

base_url = sys.argv[1].rstrip("/")

def request(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as res:
        return json.loads(res.read().decode())

suffix = f"{int(time.time() * 1000)}-{random.randint(0, 999999)}"
password = "Password123!"
u1_name = f"u{uuid.uuid4().hex[:12]}"
u2_name = f"u{uuid.uuid4().hex[:12]}"
u1 = request("POST", "/v1/users", {"username": u1_name, "password": password})
u2 = request("POST", "/v1/users", {"username": u2_name, "password": password})
token = request("POST", "/v1/auth/login", {"username": u1_name, "password": password})["access_token"]
stream = request("POST", "/v1/streams", {"name": f"redisoutage-stream-{suffix}", "member_ids": [u1["id"], u2["id"]]}, token=token)
print(json.dumps({"token": token, "stream_id": stream["id"], "suffix": suffix}))
PY
)"

kubectl -n "$NAMESPACE" scale "$redis_ref" --replicas=0 >/dev/null
sleep 3

python3 - "$BASE_URL" "$setup_json" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

base_url = sys.argv[1].rstrip("/")
setup = json.loads(sys.argv[2])

def request(method, path, body=None, token=None, idempotency_key=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            raw = res.read().decode()
            return res.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        return exc.code, json.loads(raw) if raw else {}
    except urllib.error.URLError:
        return 0, {}

deadline = time.time() + 90
last_health = None
not_ready_seen = False
event_rejected = False

while time.time() < deadline:
    _, health = request("GET", "/health/ready")
    if health:
        last_health = health
        reasons = set(health.get("reason") or [])
        if (
            health.get("status") == "not_ready"
            and "redis_master_unreachable" in reasons
            and health.get("redis", {}).get("master_reachable") is False
        ):
            not_ready_seen = True

    status, body = request(
        "POST",
        f"/v1/streams/{setup['stream_id']}/events",
        {"body": "event while redis down"},
        token=setup["token"],
        idempotency_key=f"redis-down-{setup['suffix']}",
    )
    if status >= 400 or status == 0 or body.get("status") != "accepted":
        event_rejected = True

    if not_ready_seen and event_rejected:
        raise SystemExit(0)

    time.sleep(2)

raise SystemExit(
    "Expected Redis outage to become not_ready and reject event intake. "
    f"not_ready_seen={not_ready_seen}, event_rejected={event_rejected}, last_health={json.dumps(last_health, separators=(',', ':'))}"
)
PY

kubectl -n "$NAMESPACE" scale "$redis_ref" --replicas="$target_replicas" >/dev/null
kubectl -n "$NAMESPACE" rollout status "$redis_ref" --timeout=180s >/dev/null
wait_ready 180

python3 - "$BASE_URL" "$setup_json" <<'PY'
import json
import sys
import urllib.request

base_url = sys.argv[1].rstrip("/")
setup = json.loads(sys.argv[2])

data = json.dumps({"body": "event after redis recovery"}).encode()
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {setup['token']}",
    "X-Idempotency-Key": f"redis-recover-{setup['suffix']}",
}
req = urllib.request.Request(
    f"{base_url}/v1/streams/{setup['stream_id']}/events",
    data=data,
    headers=headers,
    method="POST",
)
with urllib.request.urlopen(req, timeout=5) as res:
    body = json.loads(res.read().decode())
if body.get("status") != "accepted":
    raise SystemExit(f"Expected accepted after Redis recovery, got: {json.dumps(body, separators=(',', ':'))}")
print("Redis outage test passed (k8s/linux): readiness became not_ready, intake failed during full outage, then recovered")
PY
