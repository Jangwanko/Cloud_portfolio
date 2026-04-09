#!/usr/bin/env bash
set -Eeuo pipefail

LOG_DIR="${LOG_DIR:-./logs}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
MAX_DOCKER_LOG_MB="${MAX_DOCKER_LOG_MB:-200}"

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log() { echo "[$(timestamp)] $*"; }
err() { echo "[$(timestamp)] ERROR: $*" >&2; }

cleanup_project_logs() {
  if [[ ! -d "${LOG_DIR}" ]]; then
    log "LOG_DIR not found, skipping: ${LOG_DIR}"
    return 0
  fi

  log "Cleaning project logs (retention ${RETENTION_DAYS} days): ${LOG_DIR}"
  find "${LOG_DIR}" -type f \( -name "*.log" -o -name "*.log.*" -o -name "*.gz" \) -mtime +"${RETENTION_DAYS}" -print -delete
  log "Project log cleanup completed"
}

cleanup_docker_json_logs() {
  local docker_log_root="/var/lib/docker/containers"
  if [[ ! -d "${docker_log_root}" ]]; then
    log "Docker json log path not found, skipping: ${docker_log_root}"
    return 0
  fi

  if [[ "${EUID}" -ne 0 ]]; then
    log "Root privilege required for Docker json log cleanup, skipping"
    return 0
  fi

  log "Truncating large Docker json logs (>${MAX_DOCKER_LOG_MB}MB)"
  find "${docker_log_root}" -type f -name "*-json.log" -size +"${MAX_DOCKER_LOG_MB}"M -print -exec sh -c ': > "$1"' _ {} \;
  log "Docker json log truncate completed"
}

main() {
  cleanup_project_logs
  cleanup_docker_json_logs
  log "Log cleanup finished"
}

main "$@"
