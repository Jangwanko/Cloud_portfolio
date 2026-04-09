#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-http://localhost/api}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
SERVICES="${SERVICES:-api worker}"
CURL_TIMEOUT="${CURL_TIMEOUT:-5}"

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log() { echo "[$(timestamp)] $*"; }
err() { echo "[$(timestamp)] ERROR: $*" >&2; }

check_api() {
  local body
  body="$(curl -fsS --max-time "${CURL_TIMEOUT}" "${BASE_URL}/health/ready")" || return 1
  if [[ "${body}" != *"\"status\":\"ready\""* ]]; then
    err "API readiness is not ready: ${body}"
    return 1
  fi
  log "API readiness healthy: ${body}"
}

check_service_running() {
  local service="$1"
  if ! docker compose -f "${COMPOSE_FILE}" ps --services --status running | grep -qx "${service}"; then
    err "Service is not running: ${service}"
    return 1
  fi
  log "Service running: ${service}"
}

check_service_health_if_defined() {
  local service="$1"
  local cid health
  cid="$(docker compose -f "${COMPOSE_FILE}" ps -q "${service}" || true)"
  [[ -z "${cid}" ]] && return 0

  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${cid}" 2>/dev/null || true)"
  if [[ "${health}" == "unhealthy" ]]; then
    err "Service health is unhealthy: ${service}"
    return 1
  fi
  log "Service health: ${service}=${health}"
}

main() {
  local failed=0

  check_api || failed=1

  for svc in ${SERVICES}; do
    check_service_running "${svc}" || failed=1
    check_service_health_if_defined "${svc}" || failed=1
  done

  if [[ "${failed}" -ne 0 ]]; then
    err "health check failed"
    exit 1
  fi

  log "health check passed"
}

main "$@"
