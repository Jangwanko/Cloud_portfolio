#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-messaging-ha}"
NAMESPACE="${NAMESPACE:-messaging-app}"
BASE_URL="${BASE_URL:-http://localhost}"
IMAGE_NAME="${IMAGE_NAME:-messaging-portfolio:local}"
GRAFANA_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"
GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-}"
RECREATE_CLUSTER="${RECREATE_CLUSTER:-true}"
RUN_SMOKE_TEST="${RUN_SMOKE_TEST:-true}"
RUN_FAILURE_TESTS="${RUN_FAILURE_TESTS:-false}"
POSTGRESQL_HA_CHART_VERSION="${POSTGRESQL_HA_CHART_VERSION:-16.3.2}"
KUBE_STATE_METRICS_CHART_VERSION="${KUBE_STATE_METRICS_CHART_VERSION:-5.31.0}"
KEDA_CHART_VERSION="${KEDA_CHART_VERSION:-2.17.2}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KIND_CONFIG="$ROOT_DIR/k8s/kind-config.yaml"
PG_VALUES="$ROOT_DIR/k8s/values/postgresql-ha-values.yaml"
APP_MANIFEST="$ROOT_DIR/k8s/app/manifests-ha.yaml"
METRICS_SERVER_MANIFEST="$ROOT_DIR/k8s/metrics-server-components.yaml"
INGRESS_NGINX_MANIFEST="https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/kind/deploy.yaml"

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

check_docker() {
  docker version >/dev/null 2>&1 || fail "Docker is not running or Docker CLI is unavailable."
  ok "Docker is running"
}

check_port() {
  local port="$1"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("127.0.0.1", port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
  elif command -v ss >/dev/null 2>&1; then
    if ss -ltn | awk '{print $4}' | grep -Eq "[:.]${port}$"; then
      return 1
    fi
    return 0
  else
    return 0
  fi
}

wait_url_ready() {
  local url="$1"
  local timeout="${2:-180}"
  local deadline=$((SECONDS + timeout))

  while (( SECONDS < deadline )); do
    if curl -fsS "$url" 2>/dev/null | grep -q '"status"[[:space:]]*:[[:space:]]*"ready"'; then
      return 0
    fi
    sleep 2
  done

  fail "Timed out waiting for ready response from $url"
}

wait_deployment() {
  local deployment="$1"
  local namespace="${2:-$NAMESPACE}"
  kubectl rollout status "deployment/$deployment" -n "$namespace" --timeout=600s
}

create_runtime_secret() {
  local auth_secret
  auth_secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  if [[ -z "$GRAFANA_ADMIN_PASSWORD" ]]; then
    GRAFANA_ADMIN_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  fi

  kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
  kubectl create secret generic messaging-runtime-secrets \
    -n "$NAMESPACE" \
    --from-literal="AUTH_SECRET_KEY=$auth_secret" \
    --from-literal="ACCESS_TOKEN_TTL_SECONDS=3600" \
    --from-literal="GRAFANA_ADMIN_USER=$GRAFANA_ADMIN_USER" \
    --from-literal="GRAFANA_ADMIN_PASSWORD=$GRAFANA_ADMIN_PASSWORD" \
    --dry-run=client \
    -o yaml | kubectl apply -f -
}

create_local_tls_secret() {
  if ! command -v openssl >/dev/null 2>&1; then
    printf '[warn] openssl is not available. Skipping local TLS secret; HTTP ingress still works.\n'
    return 0
  fi

  local tmp_dir
  tmp_dir="$(mktemp -d)"

  openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout "$tmp_dir/tls.key" \
    -out "$tmp_dir/tls.crt" \
    -days 365 \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" >/dev/null 2>&1

  kubectl create secret tls messaging-local-tls \
    -n "$NAMESPACE" \
    --cert="$tmp_dir/tls.crt" \
    --key="$tmp_dir/tls.key" \
    --dry-run=client \
    -o yaml | kubectl apply -f -

  rm -rf "$tmp_dir"
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

grant_pg_monitor() {
  local pods
  local pod

  pods="$(kubectl -n "$NAMESPACE" get pods -l app.kubernetes.io/component=postgresql -o jsonpath='{.items[*].metadata.name}')"

  for pod in $pods; do
    if kubectl -n "$NAMESPACE" exec "$pod" -- bash -ceu '
      password_file="${POSTGRES_POSTGRES_PASSWORD_FILE:-/opt/bitnami/postgresql/secrets/postgres-password}"
      [[ -r "$password_file" ]]
      PGPASSWORD="$(<"$password_file")"
      export PGPASSWORD
      is_primary="$("$1" -X -qAt -v ON_ERROR_STOP=1 -U postgres -d postgres -c "SELECT NOT pg_is_in_recovery();")"
      [[ "$is_primary" == "t" ]]
      exec "$1" -X -q -v ON_ERROR_STOP=1 -U postgres -d postgres -c "GRANT pg_monitor TO portfolio;"
    ' bash /opt/bitnami/postgresql/bin/psql >/dev/null 2>&1; then
      ok "Granted pg_monitor to portfolio on primary pod: $pod"
      return 0
    fi
  done

  printf '[warn] Unable to find PostgreSQL primary pod. Skipping pg_monitor grant for portfolio.\n'
}

log "Checking prerequisites"
require_command docker
require_command kind
require_command kubectl
require_command helm
require_command curl
require_command python3
check_docker

cd "$ROOT_DIR"

if [[ "$RECREATE_CLUSTER" == "true" ]] && kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  log "Removing previous kind cluster"
  kind delete cluster --name "$CLUSTER_NAME"
fi

log "Validating local ports"
check_port 80 || fail "Local port 80 is already in use."
check_port 443 || fail "Local port 443 is already in use."
ok "Ports 80 and 443 are available"

log "Building application image"
docker build -t "$IMAGE_NAME" .

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  log "Creating kind cluster"
  kind create cluster --name "$CLUSTER_NAME" --config "$KIND_CONFIG"
else
  ok "Using existing kind cluster: $CLUSTER_NAME"
fi

log "Creating namespace and runtime secrets"
create_runtime_secret
create_local_tls_secret

log "Loading application image into kind"
kind load docker-image "$IMAGE_NAME" --name "$CLUSTER_NAME"

log "Installing metrics-server"
kubectl apply -f "$METRICS_SERVER_MANIFEST"
wait_deployment metrics-server kube-system

log "Installing ingress-nginx"
kubectl apply -f "$INGRESS_NGINX_MANIFEST"
wait_deployment ingress-nginx-controller ingress-nginx

log "Installing Helm charts"
if [[ ! -d "$ROOT_DIR/tools/helm-cache/repository" ]]; then
  helm repo add bitnami https://charts.bitnami.com/bitnami >/dev/null 2>&1 || true
  helm repo update
fi

PG_CHART="$(helm_chart_source 'postgresql-ha-*.tgz' 'bitnami/postgresql-ha')"
PG_VERSION_ARGS=()

if [[ "$PG_CHART" == bitnami/* ]]; then
  helm repo add bitnami https://charts.bitnami.com/bitnami >/dev/null 2>&1 || true
  helm repo update
  PG_VERSION_ARGS=(--version "$POSTGRESQL_HA_CHART_VERSION")
fi

helm upgrade --install messaging-postgresql-ha "$PG_CHART" \
  -n "$NAMESPACE" \
  "${PG_VERSION_ARGS[@]}" \
  -f "$PG_VALUES" \
  --wait --timeout 15m

log "Configuring PostgreSQL synchronous replication"
NAMESPACE="$NAMESPACE" bash "$ROOT_DIR/scripts/configure_postgres_sync.sh"

grant_pg_monitor

log "Installing kube-state-metrics"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update
helm upgrade --install kube-state-metrics prometheus-community/kube-state-metrics \
  -n "$NAMESPACE" \
  --version "$KUBE_STATE_METRICS_CHART_VERSION" \
  --wait --timeout 10m
wait_deployment kube-state-metrics

log "Installing KEDA"
helm repo add kedacore https://kedacore.github.io/charts >/dev/null 2>&1 || true
helm repo update
kubectl create namespace keda --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install keda kedacore/keda \
  -n keda \
  --version "$KEDA_CHART_VERSION" \
  --wait --timeout 10m
wait_deployment keda-operator keda

log "Applying Kafka runtime"
kubectl apply -f "$ROOT_DIR/k8s/gitops/base/kafka-ha.yaml"
kubectl rollout status statefulset/kafka -n "$NAMESPACE" --timeout=600s
kubectl wait --for=condition=complete job/kafka-topic-bootstrap -n "$NAMESPACE" --timeout=300s

log "Applying application manifests"
kubectl apply -f "$APP_MANIFEST"

log "Waiting for application deployments"
wait_deployment worker
kubectl set env deployment/api -n "$NAMESPACE" GENERIC_EVENTS_V2_ENABLED=true
wait_deployment api
wait_deployment notification-worker
wait_deployment dlq-replayer
wait_deployment kafka-exporter
wait_deployment prometheus
wait_deployment grafana

log "Waiting for API readiness"
wait_url_ready "$BASE_URL/health/ready" 180

if [[ "$RUN_SMOKE_TEST" == "true" ]]; then
  log "Running smoke test"
  BASE_URL="$BASE_URL" bash "$ROOT_DIR/scripts/smoke_test.sh"
fi

if [[ "$RUN_FAILURE_TESTS" == "true" ]]; then
  log "Running DB outage test"
  BASE_URL="$BASE_URL" NAMESPACE="$NAMESPACE" bash "$ROOT_DIR/scripts/test_db_down.sh"
fi

log "Deployment summary"
kubectl get pods -n "$NAMESPACE"

printf '\nQuick Start completed successfully.\n'
printf 'API URL: %s\n' "$BASE_URL"
printf 'Grafana URL: http://localhost/grafana\n'
printf 'Grafana anonymous viewer: enabled\n'
printf 'Grafana admin user: %s\n' "$GRAFANA_ADMIN_USER"
printf 'Grafana admin password: stored in secret messaging-runtime-secrets\n'
printf 'Prometheus URL: http://localhost/prometheus\n'
printf 'Failure tests: RUN_FAILURE_TESTS=true bash scripts/quick_start_all.sh\n'
printf '\nUseful checks:\n'
printf '  curl %s/health/ready\n' "$BASE_URL"
printf '  kubectl get pods -n %s\n' "$NAMESPACE"
