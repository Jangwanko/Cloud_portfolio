#!/usr/bin/env bash
set -Eeuo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
BRANCH="${BRANCH:-}"
SKIP_PULL="${SKIP_PULL:-false}"
BUILD="${BUILD:-true}"

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log() { echo "[$(timestamp)] $*"; }
err() { echo "[$(timestamp)] ERROR: $*" >&2; }

usage() {
  cat <<EOF
Usage:
  bash scripts/deploy.sh [--branch <name>] [--skip-pull] [--no-build]
Env:
  COMPOSE_FILE=docker-compose.yml
  BRANCH=
  SKIP_PULL=false
  BUILD=true
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch)
      BRANCH="$2"
      shift 2
      ;;
    --skip-pull)
      SKIP_PULL="true"
      shift
      ;;
    --no-build)
      BUILD="false"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      err "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

main() {
  if [[ "${SKIP_PULL}" != "true" ]]; then
    log "Fetching remote updates"
    git fetch --all --prune

    if [[ -n "${BRANCH}" ]]; then
      log "Switching branch: ${BRANCH}"
      git checkout "${BRANCH}"
    fi

    log "Pulling latest code (ff-only)"
    git pull --ff-only
  else
    log "Skipping git pull step"
  fi

  log "Starting deployment"
  if [[ "${BUILD}" == "true" ]]; then
    docker compose -f "${COMPOSE_FILE}" up -d --build --remove-orphans
  else
    docker compose -f "${COMPOSE_FILE}" up -d --remove-orphans
  fi

  log "Running post-deploy health check"
  bash scripts/health_check.sh

  log "Deployment completed"
}

main "$@"
