#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-messaging-app}"
IMAGE_NAME="${IMAGE_NAME:-messaging-portfolio:local}"
HOST_NAME="${HOST_NAME:-}"
BASE_URL="${BASE_URL:-http://${HOST_NAME:-localhost}}"
GRAFANA_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"
GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-1q2w3e4r}"
RUN_SMOKE_TEST="${RUN_SMOKE_TEST:-true}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_VALUES="$ROOT_DIR/k8s/values/postgresql-lite-values.yaml"
LITE_OVERLAY="$ROOT_DIR/k8s/gitops/overlays/demo-lite"
RENDERED_MANIFEST="$ROOT_DIR/.tmp/demo-lite-k3s.yaml"

log() {
  printf '\n==> %s\n' "$1"
}

ok() {
  printf '[ok] %s\n' "$1"
}

fail() {
  printf '\n%s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
  ok "Using $1: $(command -v "$1")"
}

kubectl_cmd() {
  kubectl "$@"
}

docker_cmd() {
  if docker version >/dev/null 2>&1; then
    docker "$@"
    return
  fi

  if command -v sudo >/dev/null 2>&1 && sudo docker version >/dev/null 2>&1; then
    sudo docker "$@"
    return
  fi

  fail "Docker daemon is unavailable, or the current user cannot access Docker. Try: sudo usermod -aG docker \$USER && newgrp docker"
}

helm_chart_source() {
  local pattern="$1"
  local fallback="$2"
  local chart

  chart="$(find "$ROOT_DIR/tools/helm-cache/repository" -maxdepth 1 -name "$pattern" 2>/dev/null | sort -r | head -n 1 || true)"
  if [[ -n "$chart" ]]; then
    printf '%s\n' "$chart"
  else
    printf '%s\n' "$fallback"
  fi
}

create_runtime_secret() {
  local auth_secret
  if command -v openssl >/dev/null 2>&1; then
    auth_secret="$(openssl rand -base64 48 | tr -d '\n')"
  else
    auth_secret="lite-demo-auth-secret-$(date +%s)"
  fi

  kubectl_cmd create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl_cmd apply -f -
  kubectl_cmd create secret generic messaging-runtime-secrets \
    -n "$NAMESPACE" \
    --from-literal="AUTH_SECRET_KEY=$auth_secret" \
    --from-literal="ACCESS_TOKEN_TTL_SECONDS=3600" \
    --from-literal="GRAFANA_ADMIN_USER=$GRAFANA_ADMIN_USER" \
    --from-literal="GRAFANA_ADMIN_PASSWORD=$GRAFANA_ADMIN_PASSWORD" \
    --dry-run=client \
    -o yaml | kubectl_cmd apply -f -
}

create_tls_secret() {
  if ! command -v openssl >/dev/null 2>&1; then
    printf '[warn] openssl is not available. Skipping TLS secret; HTTP ingress still works.\n'
    return 0
  fi

  local cert_host="${HOST_NAME:-localhost}"
  local tmp_dir
  tmp_dir="$(mktemp -d)"

  openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout "$tmp_dir/tls.key" \
    -out "$tmp_dir/tls.crt" \
    -days 365 \
    -subj "/CN=$cert_host" \
    -addext "subjectAltName=DNS:$cert_host,DNS:localhost,IP:127.0.0.1" >/dev/null 2>&1

  kubectl_cmd create secret tls messaging-local-tls \
    -n "$NAMESPACE" \
    --cert="$tmp_dir/tls.crt" \
    --key="$tmp_dir/tls.key" \
    --dry-run=client \
    -o yaml | kubectl_cmd apply -f -

  rm -rf "$tmp_dir"
}

grant_pg_monitor() {
  local encoded_password
  local postgres_password
  local pods

  encoded_password="$(kubectl_cmd -n "$NAMESPACE" get secret messaging-postgresql-ha-postgresql -o jsonpath='{.data.postgres-password}' 2>/dev/null || true)"
  if [[ -z "$encoded_password" ]]; then
    printf '[warn] Unable to read postgres-password. Skipping pg_monitor grant for portfolio.\n'
    return 0
  fi

  postgres_password="$(printf '%s' "$encoded_password" | base64 -d)"
  pods="$(kubectl_cmd -n "$NAMESPACE" get pods -l app.kubernetes.io/component=postgresql -o jsonpath='{.items[*].metadata.name}')"

  for pod in $pods; do
    if kubectl_cmd -n "$NAMESPACE" exec "$pod" -- bash -lc \
      "PGPASSWORD='$postgres_password' /opt/bitnami/postgresql/bin/psql -U postgres -d postgres -At -c 'SELECT NOT pg_is_in_recovery();'" 2>/dev/null | grep -qx 't'; then
      kubectl_cmd -n "$NAMESPACE" exec "$pod" -- bash -lc \
        "PGPASSWORD='$postgres_password' /opt/bitnami/postgresql/bin/psql -U postgres -d postgres -c 'GRANT pg_monitor TO portfolio;'"
      ok "Granted pg_monitor to portfolio on primary pod: $pod"
      return 0
    fi
  done

  printf '[warn] Unable to find PostgreSQL primary pod. Skipping pg_monitor grant for portfolio.\n'
}

import_image_to_k3s() {
  if ! command -v k3s >/dev/null 2>&1; then
    printf '[warn] k3s command is not available. Assuming cluster can pull %s.\n' "$IMAGE_NAME"
    return 0
  fi

  log "Importing image into k3s containerd"
  docker_cmd save "$IMAGE_NAME" | sudo k3s ctr images import -
}

render_manifest_for_k3s() {
  mkdir -p "$(dirname "$RENDERED_MANIFEST")"
  kubectl_cmd kustomize "$LITE_OVERLAY" > "$RENDERED_MANIFEST"

  python3 - "$RENDERED_MANIFEST" "$HOST_NAME" <<'PY'
import sys
import ipaddress
from pathlib import Path

path = Path(sys.argv[1])
host = sys.argv[2]
text = path.read_text(encoding="utf-8")

text = text.replace("ingressClassName: nginx", "ingressClassName: traefik")
if host:
    is_ip = False
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False

    if is_ip:
        text = text.replace("  rules:\n    - host: localhost\n      http:", "  rules:\n    - http:")
        text = text.replace("  tls:\n    - hosts:\n        - localhost\n      secretName: messaging-local-tls\n", "")
    else:
        text = text.replace("host: localhost", f"host: {host}")
        text = text.replace("- localhost", f"- {host}")

path.write_text(text, encoding="utf-8")
PY
}

wait_deployment() {
  local deployment="$1"
  local namespace="${2:-$NAMESPACE}"
  kubectl_cmd rollout status "deployment/$deployment" -n "$namespace" --timeout=600s
}

wait_url_ready() {
  local url="$1"
  local timeout="${2:-180}"
  local deadline=$((SECONDS + timeout))

  while (( SECONDS < deadline )); do
    if curl -fsS "$url" 2>/dev/null | grep -Eq '"status"[[:space:]]*:[[:space:]]*"(ready|degraded)"'; then
      return 0
    fi
    sleep 2
  done

  fail "Timed out waiting for ready/degraded response from $url"
}

log "Checking prerequisites"
require_command docker
require_command kubectl
require_command helm
require_command curl
require_command python3
docker_cmd version >/dev/null
kubectl_cmd version --client >/dev/null
kubectl_cmd cluster-info >/dev/null

cd "$ROOT_DIR"

log "Building application image"
docker_cmd build -t "$IMAGE_NAME" .
import_image_to_k3s

log "Creating runtime secrets"
create_runtime_secret
create_tls_secret

log "Installing lite PostgreSQL"
helm repo add bitnami https://charts.bitnami.com/bitnami >/dev/null 2>&1 || true
helm repo update
PG_CHART="$(helm_chart_source 'postgresql-ha-*.tgz' 'bitnami/postgresql-ha')"
helm upgrade --install messaging-postgresql-ha "$PG_CHART" \
  -n "$NAMESPACE" \
  -f "$PG_VALUES" \
  --wait --timeout 15m
grant_pg_monitor

log "Installing kube-state-metrics and KEDA"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo add kedacore https://kedacore.github.io/charts >/dev/null 2>&1 || true
helm repo update
helm upgrade --install kube-state-metrics prometheus-community/kube-state-metrics \
  -n "$NAMESPACE" \
  --wait --timeout 10m
kubectl_cmd create namespace keda --dry-run=client -o yaml | kubectl_cmd apply -f -
helm upgrade --install keda kedacore/keda \
  -n keda \
  --wait --timeout 10m

log "Applying demo-lite manifests for k3s"
render_manifest_for_k3s
kubectl_cmd apply -f "$RENDERED_MANIFEST"
kubectl_cmd rollout status statefulset/kafka -n "$NAMESPACE" --timeout=600s
kubectl_cmd wait --for=condition=complete job/kafka-topic-bootstrap -n "$NAMESPACE" --timeout=300s

log "Waiting for deployments"
wait_deployment kube-state-metrics
wait_deployment keda-operator keda
wait_deployment api
wait_deployment worker
wait_deployment prometheus
wait_deployment grafana

log "Waiting for API readiness"
wait_url_ready "$BASE_URL/health/ready" 180

if [[ "$RUN_SMOKE_TEST" == "true" ]]; then
  log "Running smoke test"
  BASE_URL="$BASE_URL" bash "$ROOT_DIR/scripts/smoke_test.sh"
fi

log "Deployment summary"
kubectl_cmd get pods -n "$NAMESPACE"

printf '\nDemo-lite deployment completed.\n'
printf 'Demo UI: %s/demo/order-dashboard.html\n' "$BASE_URL"
printf 'Swagger: %s/docs\n' "$BASE_URL"
printf 'Grafana: %s/grafana/d/messaging-portfolio-overview/messaging-portfolio-operations-overview?orgId=1&refresh=5s\n' "$BASE_URL"
printf 'Readiness: %s/health/ready\n' "$BASE_URL"
