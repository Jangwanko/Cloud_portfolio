#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-messaging-app}"
POSTGRES_STATEFULSET="${POSTGRES_STATEFULSET:-messaging-postgresql-ha-postgresql}"
POSTGRES_SYNC_TIMEOUT_SEC="${POSTGRES_SYNC_TIMEOUT_SEC:-300}"
POSTGRES_PSQL="${POSTGRES_PSQL:-/opt/bitnami/postgresql/bin/psql}"
SYNCHRONOUS_STANDBY_NAMES="ANY 1 (*)"

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

run_postgres_sql() {
  local pod="$1"
  local sql="$2"

  # Read the chart-managed credential only inside the pod. Its value never
  # crosses kubectl stdout or appears in a command argument.
  kubectl -n "$NAMESPACE" exec "$pod" -- \
    bash -ceu '
      password_file="${POSTGRES_POSTGRES_PASSWORD_FILE:-/opt/bitnami/postgresql/secrets/postgres-password}"
      [[ -r "$password_file" ]]
      PGPASSWORD="$(<"$password_file")"
      export PGPASSWORD
      exec "$1" -X -qAt -v ON_ERROR_STOP=1 -U postgres -d postgres -c "$2"
    ' bash "$POSTGRES_PSQL" "$sql"
}

find_primary_once() {
  local pod
  local is_primary

  for pod in $postgres_pods; do
    if is_primary="$(run_postgres_sql "$pod" 'SELECT NOT pg_is_in_recovery();' 2>/dev/null)" && \
      [[ "$is_primary" == "t" ]]; then
      printf '%s\n' "$pod"
      return 0
    fi
  done

  return 1
}

discover_primary() {
  local deadline=$((SECONDS + POSTGRES_SYNC_TIMEOUT_SEC))
  local primary

  while (( SECONDS < deadline )); do
    if primary="$(find_primary_once)"; then
      printf '%s\n' "$primary"
      return 0
    fi
    sleep 2
  done

  return 1
}

require_command kubectl

if ! [[ "$POSTGRES_SYNC_TIMEOUT_SEC" =~ ^[1-9][0-9]*$ ]]; then
  fail "POSTGRES_SYNC_TIMEOUT_SEC must be a positive integer"
fi

expected_replicas="$(kubectl -n "$NAMESPACE" get "statefulset/$POSTGRES_STATEFULSET" \
  -o jsonpath='{.spec.replicas}' 2>/dev/null || true)"
[[ "$expected_replicas" =~ ^[0-9]+$ ]] && (( expected_replicas >= 2 )) || \
  fail "Synchronous replication requires at least two StatefulSet replicas"

deadline=$((SECONDS + POSTGRES_SYNC_TIMEOUT_SEC))
ready_replicas=0
while (( SECONDS < deadline )); do
  desired_replicas="$(kubectl -n "$NAMESPACE" get "statefulset/$POSTGRES_STATEFULSET" \
    -o jsonpath='{.spec.replicas}' 2>/dev/null || true)"
  ready_replicas="$(kubectl -n "$NAMESPACE" get "statefulset/$POSTGRES_STATEFULSET" \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)"
  ready_replicas="${ready_replicas:-0}"
  if [[ "$desired_replicas" == "$expected_replicas" && "$ready_replicas" == "$expected_replicas" ]]; then
    break
  fi
  sleep 2
done
[[ "$ready_replicas" == "$expected_replicas" ]] || \
  fail "Timed out waiting for statefulset/$POSTGRES_STATEFULSET ready replicas: $ready_replicas/$expected_replicas"

postgres_pods=""
for ((index = 0; index < expected_replicas; index++)); do
  postgres_pods+="${POSTGRES_STATEFULSET}-${index} "
done
postgres_pods="${postgres_pods% }"

for pod in $postgres_pods; do
  run_postgres_sql "$pod" \
    "ALTER SYSTEM SET synchronous_standby_names = '$SYNCHRONOUS_STANDBY_NAMES';" >/dev/null
  run_postgres_sql "$pod" "ALTER SYSTEM SET synchronous_commit = 'on';" >/dev/null

  reload_result="$(run_postgres_sql "$pod" 'SELECT pg_reload_conf();')"
  [[ "$reload_result" == "t" ]] || fail "PostgreSQL configuration reload failed on pod/$pod"

  loaded_setting="$(run_postgres_sql "$pod" "SELECT current_setting('synchronous_standby_names');")"
  [[ "$loaded_setting" == "$SYNCHRONOUS_STANDBY_NAMES" ]] || \
    fail "PostgreSQL did not load synchronous_standby_names on pod/$pod"
  loaded_synchronous_commit="$(run_postgres_sql "$pod" "SELECT current_setting('synchronous_commit');")"
  [[ "$loaded_synchronous_commit" == "on" ]] || \
    fail "PostgreSQL did not load synchronous_commit=on on pod/$pod"
  printf '[ok] Persisted synchronous replication configuration on pod: %s\n' "$pod"
done

primary_pod="$(discover_primary)" || fail "Timed out discovering the PostgreSQL primary pod"

deadline=$((SECONDS + POSTGRES_SYNC_TIMEOUT_SEC))
sync_standby_count=0
while (( SECONDS < deadline )); do
  if current_primary="$(find_primary_once)"; then
    primary_pod="$current_primary"
  fi
  if candidate_count="$(run_postgres_sql "$primary_pod" \
    "SELECT count(*) FROM pg_stat_replication WHERE state = 'streaming' AND sync_state IN ('sync', 'quorum');" \
    2>/dev/null)" && [[ "$candidate_count" =~ ^[0-9]+$ ]]; then
    sync_standby_count="$candidate_count"
    if (( sync_standby_count >= 1 )); then
      printf '[ok] PostgreSQL synchronous replication configured on primary pod: %s\n' "$primary_pod"
      printf '[ok] Streaming synchronous standby count: %s\n' "$sync_standby_count"
      exit 0
    fi
  fi
  sleep 2
done

fail "Timed out waiting for a streaming synchronous PostgreSQL standby"
