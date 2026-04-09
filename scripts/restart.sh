#!/usr/bin/env bash
set -Eeuo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
SERVICES="${SERVICES:-api worker dlq_replayer nginx}"

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log() { echo "[$(timestamp)] $*"; }
err() { echo "[$(timestamp)] ERROR: $*" >&2; }

is_running() {
  local service="$1"
  docker compose -f "${COMPOSE_FILE}" ps --services --status running | grep -qx "${service}"
}

health_status() {
  local service="$1"
  local cid
  cid="$(docker compose -f "${COMPOSE_FILE}" ps -q "${service}" || true)"
  [[ -z "${cid}" ]] && { echo "none"; return; }
  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${cid}" 2>/dev/null || echo "none"
}

restart_service() {
  local service="$1"
  log "Restarting service: ${service}"
  docker compose -f "${COMPOSE_FILE}" restart "${service}" >/dev/null
}

main() {
  local restarted=0

  for svc in ${SERVICES}; do
    if ! is_running "${svc}"; then
      err "Service not running, restarting: ${svc}"
      restart_service "${svc}"
      restarted=1
      continue
    fi

    case "$(health_status "${svc}")" in
      unhealthy)
        err "Service unhealthy, restarting: ${svc}"
        restart_service "${svc}"
        restarted=1
        ;;
      *)
        log "Service healthy: ${svc}"
        ;;
    esac
  done

  if [[ "${restarted}" -eq 1 ]]; then
    log "Restart operations completed"
  else
    log "No restart required"
  fi
}

main "$@"
