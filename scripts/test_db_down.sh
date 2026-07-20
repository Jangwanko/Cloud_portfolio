#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost}"
NAMESPACE="${NAMESPACE:-messaging-app}"
API_DEPLOYMENT="${API_DEPLOYMENT:-api}"
DB_WORKLOAD="${DB_WORKLOAD:-messaging-postgresql-ha-postgresql}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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

wait_workload_replicas() {
  local workload="$1"
  local expected="$2"
  local timeout="${3:-240}"
  local deadline=$((SECONDS + timeout))
  local desired
  local ready

  while (( SECONDS < deadline )); do
    desired="$(kubectl -n "$NAMESPACE" get "$workload" -o jsonpath='{.spec.replicas}' 2>/dev/null || true)"
    ready="$(kubectl -n "$NAMESPACE" get "$workload" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)"
    ready="${ready:-0}"
    if [[ "$desired" == "$expected" && "$ready" == "$expected" ]]; then
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for $workload replicas (expected=$expected desired=${desired:-unknown} ready=${ready:-unknown})" >&2
  return 1
}

configure_postgres_sync() {
  if [[ "$DB_WORKLOAD" == "messaging-postgresql-ha-postgresql" ]]; then
    NAMESPACE="$NAMESPACE" bash "$ROOT_DIR/scripts/configure_postgres_sync.sh"
  fi
}

require_command kubectl
require_command python3

db_ref="$(workload_ref "$DB_WORKLOAD")"
target_replicas="$(base_replicas "$DB_WORKLOAD")"
db_was_scaled_down=false

restore_db() {
  if [[ "$db_was_scaled_down" == "true" ]]; then
    kubectl -n "$NAMESPACE" scale "$db_ref" --replicas="$target_replicas" >/dev/null || true
    wait_workload_replicas "$db_ref" "$target_replicas" 180 || true
    kubectl -n "$NAMESPACE" rollout status "$db_ref" --timeout=180s >/dev/null || true
    POSTGRES_SYNC_TIMEOUT_SEC=60 configure_postgres_sync >/dev/null 2>&1 || true
  fi
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

for pod in $(kubectl -n "$NAMESPACE" get pods -l "app=$API_DEPLOYMENT" -o jsonpath='{.items[*].metadata.name}'); do
  kubectl -n "$NAMESPACE" exec "$pod" -- env "SETUP_JSON=$setup_json" python - <<'PY' >/dev/null
import json
import os
import urllib.request

setup = json.loads(os.environ["SETUP_JSON"])
payload = json.dumps({
    "event_type": "portfolio.cache-warmup.probe",
    "payload": {"message": f"cache warmup {setup['suffix']}"},
    "metadata": {"scenario": "db-outage-cache-warmup"},
}).encode()
req = urllib.request.Request(
    f"http://127.0.0.1:8000/v2/streams/{setup['stream_id']}/events",
    data=payload,
    headers={
        "Authorization": f"Bearer {setup['token']}",
        "Content-Type": "application/json",
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=10) as res:
    if res.status != 202:
        raise SystemExit(f"Expected HTTP 202 from cache warmup event, got {res.status}")
PY
done

db_was_scaled_down=true
kubectl -n "$NAMESPACE" scale "$db_ref" --replicas=0 >/dev/null
sleep 4

request_id="$(python3 - "$BASE_URL" "$setup_json" <<'PY'
import json
import sys
import urllib.error
import urllib.request

base_url = sys.argv[1].rstrip("/")
setup = json.loads(sys.argv[2])

def request(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
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
status, accepted = request(
    "POST",
    f"/v2/streams/{setup['stream_id']}/events",
    {
        "event_type": "portfolio.db-outage.probe",
        "payload": {"message": "event while db down"},
        "metadata": {"scenario": "db-outage-recovery"},
    },
    token=setup["token"],
)
if status != 202 or accepted.get("status") != "accepted":
    raise SystemExit(f"Expected accepted during DB down, got HTTP {status}: {json.dumps(accepted, separators=(',', ':'))}")
print(accepted["request_id"])
PY
)"

kubectl -n "$NAMESPACE" scale "$db_ref" --replicas="$target_replicas" >/dev/null
wait_workload_replicas "$db_ref" "$target_replicas" 240
kubectl -n "$NAMESPACE" rollout status "$db_ref" --timeout=180s >/dev/null
configure_postgres_sync
db_was_scaled_down=false
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
    status = request("GET", f"/v2/event-requests/{request_id}", token=setup["token"])
    if status.get("status") == "persisted":
        if status.get("payload", {}).get("message") != "event while db down":
            raise SystemExit("Persisted generic payload did not match the outage event")
        print("DB outage test passed (k8s/linux): accepted during DB down and persisted after recovery")
        raise SystemExit(0)
    time.sleep(2)

raise SystemExit("Event request did not become persisted in time")
PY
