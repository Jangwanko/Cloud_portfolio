#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost}"
NAMESPACE="${NAMESPACE:-messaging-app}"
API_DEPLOYMENT="${API_DEPLOYMENT:-api}"
DB_WORKLOAD="${DB_WORKLOAD:-messaging-postgresql-ha-postgresql}"

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
  if [[ "$1" == "messaging-postgresql-ha-postgresql" ]]; then
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

wait_db_query() {
  local timeout="${1:-180}"
  local deadline=$((SECONDS + timeout))
  local successes=0

  while (( SECONDS < deadline )); do
    if kubectl -n "$NAMESPACE" exec "deploy/$API_DEPLOYMENT" -- python -c "from portfolio.db import get_conn; cm = get_conn(); conn = cm.__enter__(); cur = conn.cursor(); cur.execute('SELECT 1'); cur.fetchone(); cm.__exit__(None, None, None)" >/dev/null 2>&1; then
      successes=$((successes + 1))
      if (( successes >= 3 )); then
        return 0
      fi
    else
      successes=0
    fi
    sleep 2
  done

  echo "Timed out waiting for DB query readiness" >&2
  return 1
}

run_migrations() {
  kubectl -n "$NAMESPACE" exec "deploy/$API_DEPLOYMENT" -- python -c "from portfolio.db import run_alembic_migrations; run_alembic_migrations()" >/dev/null
}

require_command kubectl
require_command python3

db_ref="$(workload_ref "$DB_WORKLOAD")"
target_replicas="$(base_replicas "$DB_WORKLOAD")"

restore_db() {
  kubectl -n "$NAMESPACE" scale "$db_ref" --replicas="$target_replicas" >/dev/null || true
}
trap restore_db EXIT

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
stream = request("POST", "/v1/streams", {"name": f"dbtest-stream-{suffix}", "member_ids": [u1["id"], u2["id"]]}, token=token)
print(json.dumps({"token": token, "stream_id": stream["id"], "suffix": suffix}))
PY
)"

kubectl -n "$NAMESPACE" scale "$db_ref" --replicas=0 >/dev/null
sleep 4

request_id="$(python3 - "$BASE_URL" "$setup_json" <<'PY'
import json
import sys
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

_, health = request("GET", "/health/ready")
reasons = set(health.get("reason") or [])
if health.get("status") != "degraded":
    raise SystemExit(f"Expected degraded while DB is down, got: {json.dumps(health, separators=(',', ':'))}")
if "postgres_primary_unreachable" not in reasons:
    raise SystemExit(f"Expected postgres_primary_unreachable, got: {json.dumps(health, separators=(',', ':'))}")
if health.get("postgres", {}).get("primary_reachable") is not False:
    raise SystemExit(f"Expected postgres.primary_reachable=false, got: {json.dumps(health, separators=(',', ':'))}")
if health.get("redis", {}).get("master_reachable") is not True:
    raise SystemExit(f"Expected redis.master_reachable=true, got: {json.dumps(health, separators=(',', ':'))}")

status, accepted = request(
    "POST",
    f"/v1/streams/{setup['stream_id']}/events",
    {"body": "event while db down"},
    token=setup["token"],
    idempotency_key=f"db-down-{setup['suffix']}",
)
if status >= 300 or accepted.get("status") != "accepted":
    raise SystemExit(f"Expected accepted during DB down, got HTTP {status}: {json.dumps(accepted, separators=(',', ':'))}")
print(accepted["request_id"])
PY
)"

kubectl -n "$NAMESPACE" scale "$db_ref" --replicas="$target_replicas" >/dev/null
kubectl -n "$NAMESPACE" rollout status "$db_ref" --timeout=180s >/dev/null
wait_db_query 240
run_migrations
wait_ready 180

python3 - "$BASE_URL" "$setup_json" "$request_id" <<'PY'
import json
import sys
import time
import urllib.request

base_url = sys.argv[1].rstrip("/")
setup = json.loads(sys.argv[2])
request_id = sys.argv[3]

def request(method, path, token=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base_url}{path}", headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as res:
        return json.loads(res.read().decode())

deadline = time.time() + 300
while time.time() < deadline:
    status = request("GET", f"/v1/event-requests/{request_id}", token=setup["token"])
    if status.get("status") == "persisted":
        print("DB outage test passed (k8s/linux): accepted during DB down and persisted after recovery")
        raise SystemExit(0)
    time.sleep(2)

raise SystemExit("Event request did not become persisted in time")
PY
