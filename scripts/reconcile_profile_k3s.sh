#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Jangwanko/Cloud_portfolio.git}"
ARGO_NAMESPACE="${ARGO_NAMESPACE:-argocd}"
PROJECT_NAME="${PROJECT_NAME:-messaging-portfolio}"
LOCAL_HA_MIN_MILLICORES="${LOCAL_HA_MIN_MILLICORES:-4000}"
LOCAL_HA_MIN_MEMORY_MIB="${LOCAL_HA_MIN_MEMORY_MIB:-14336}"
MIN_DISK_AVAILABLE_MIB="${MIN_DISK_AVAILABLE_MIB:-15360}"
APPLY=true
PROFILE_OVERRIDE=""

usage() {
  cat <<'EOF'
Usage: scripts/reconcile_profile_k3s.sh [--dry-run] [--profile demo-lite|local-ha]

Detects the current k3s node CPU and memory, selects a deployment profile, and
reconciles the Argo CD Application revision/path to that profile.

Profiles:
  demo-lite  2-core class demo host. Kafka RF 1, PostgreSQL standby check off.
  local-ha   larger local HA profile. Kafka RF 3, PostgreSQL standby checks on.

Environment:
  REPO_URL                    Git repository URL used by Argo CD.
  LOCAL_HA_MIN_MILLICORES     CPU threshold for local-ha. Default: 4000.
  LOCAL_HA_MIN_MEMORY_MIB     Memory threshold for local-ha. Default: 14336.
  MIN_DISK_AVAILABLE_MIB      Disk warning threshold. Default: 15360.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      APPLY=false
      shift
      ;;
    --profile)
      PROFILE_OVERRIDE="${2:-}"
      if [[ "$PROFILE_OVERRIDE" != "demo-lite" && "$PROFILE_OVERRIDE" != "local-ha" ]]; then
        printf 'Invalid --profile value: %s\n' "$PROFILE_OVERRIDE" >&2
        exit 1
      fi
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage
      exit 1
      ;;
  esac
done

log() {
  printf '\n==> %s\n' "$1"
}

ok() {
  printf '[ok] %s\n' "$1"
}

warn() {
  printf '[warn] %s\n' "$1"
}

fail() {
  printf '\n%s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

cpu_to_millicores() {
  local raw="$1"
  if [[ "$raw" == *m ]]; then
    printf '%s\n' "${raw%m}"
  else
    printf '%s\n' $((raw * 1000))
  fi
}

memory_to_mib() {
  local raw="$1"
  case "$raw" in
    *Ki) printf '%s\n' $(( ${raw%Ki} / 1024 )) ;;
    *Mi) printf '%s\n' "${raw%Mi}" ;;
    *Gi) printf '%s\n' $(( ${raw%Gi} * 1024 )) ;;
    *) printf '%s\n' $(( raw / 1024 / 1024 )) ;;
  esac
}

detect_node_profile() {
  local cpu_raw memory_raw disk_mib
  cpu_raw="$(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.cpu}')"
  memory_raw="$(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.memory}')"
  NODE_MILLICORES="$(cpu_to_millicores "$cpu_raw")"
  NODE_MEMORY_MIB="$(memory_to_mib "$memory_raw")"
  DISK_AVAILABLE_MIB="$(df -Pm . | awk 'NR==2 {print $4}')"

  if [[ -n "$PROFILE_OVERRIDE" ]]; then
    SELECTED_PROFILE="$PROFILE_OVERRIDE"
    PROFILE_REASON="manual override"
  elif (( NODE_MILLICORES >= LOCAL_HA_MIN_MILLICORES && NODE_MEMORY_MIB >= LOCAL_HA_MIN_MEMORY_MIB )); then
    SELECTED_PROFILE="local-ha"
    PROFILE_REASON="CPU and memory meet local-ha threshold"
  else
    SELECTED_PROFILE="demo-lite"
    PROFILE_REASON="CPU or memory is below local-ha threshold"
  fi

  if (( DISK_AVAILABLE_MIB < MIN_DISK_AVAILABLE_MIB )); then
    DISK_WARNING="disk below recommended free space"
  else
    DISK_WARNING="disk free space is acceptable"
  fi
}

profile_revision() {
  case "$1" in
    demo-lite) printf 'demo-lite\n' ;;
    local-ha) printf 'master\n' ;;
  esac
}

profile_path() {
  case "$1" in
    demo-lite) printf 'k8s/gitops/overlays/demo-lite-k3s\n' ;;
    local-ha) printf 'k8s/gitops/overlays/local-ha\n' ;;
  esac
}

profile_default_app_name() {
  case "$1" in
    demo-lite) printf 'messaging-portfolio-demo-lite\n' ;;
    local-ha) printf 'messaging-portfolio-local-ha\n' ;;
  esac
}

current_application_name() {
  local explicit="${APP_NAME:-}"
  if [[ -n "$explicit" ]]; then
    printf '%s\n' "$explicit"
    return
  fi

  for candidate in messaging-portfolio-auto messaging-portfolio-demo-lite messaging-portfolio-local-ha; do
    if kubectl get application "$candidate" -n "$ARGO_NAMESPACE" >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return
    fi
  done

  profile_default_app_name "$SELECTED_PROFILE"
}

install_argocd_if_needed() {
  kubectl create namespace "$ARGO_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
  if kubectl get crd applications.argoproj.io >/dev/null 2>&1 &&
     kubectl get deployment argocd-server -n "$ARGO_NAMESPACE" >/dev/null 2>&1 &&
     kubectl get deployment argocd-repo-server -n "$ARGO_NAMESPACE" >/dev/null 2>&1 &&
     kubectl get statefulset argocd-application-controller -n "$ARGO_NAMESPACE" >/dev/null 2>&1; then
    ok "Argo CD is already installed"
    return
  fi

  log "Installing Argo CD"
  kubectl apply --server-side -n "$ARGO_NAMESPACE" -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
  kubectl rollout status deployment/argocd-server -n "$ARGO_NAMESPACE" --timeout=600s
  kubectl rollout status deployment/argocd-repo-server -n "$ARGO_NAMESPACE" --timeout=600s
  kubectl rollout status statefulset/argocd-application-controller -n "$ARGO_NAMESPACE" --timeout=600s
}

current_argocd_path() {
  kubectl get application "$APP_NAME_RESOLVED" -n "$ARGO_NAMESPACE" \
    -o jsonpath='{.spec.source.path}' 2>/dev/null || true
}

apply_application() {
  local revision="$1"
  local manifest_path="$2"

  cat <<YAML | kubectl apply -f -
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: ${APP_NAME_RESOLVED}
  namespace: ${ARGO_NAMESPACE}
  labels:
    messaging-portfolio/profile: ${SELECTED_PROFILE}
spec:
  project: ${PROJECT_NAME}
  source:
    repoURL: ${REPO_URL}
    targetRevision: ${revision}
    path: ${manifest_path}
  destination:
    server: https://kubernetes.default.svc
    namespace: messaging-app
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - RespectIgnoreDifferences=true
  ignoreDifferences:
    - group: apps
      kind: Deployment
      jsonPointers:
        - /spec/replicas
YAML
}

require_command kubectl
require_command awk
require_command df

detect_node_profile
TARGET_REVISION="$(profile_revision "$SELECTED_PROFILE")"
TARGET_PATH="$(profile_path "$SELECTED_PROFILE")"
APP_NAME_RESOLVED="$(current_application_name)"
CURRENT_PATH="$(current_argocd_path)"

log "Detected server"
printf -- "- CPU: %s millicores\n" "$NODE_MILLICORES"
printf -- "- Memory: %s MiB\n" "$NODE_MEMORY_MIB"
printf -- "- Disk available: %s MiB (%s)\n" "$DISK_AVAILABLE_MIB" "$DISK_WARNING"

log "Recommended profile"
printf -- "- Profile: %s\n" "$SELECTED_PROFILE"
printf -- "- Reason: %s\n" "$PROFILE_REASON"
printf -- "- GitOps revision: %s\n" "$TARGET_REVISION"
printf -- "- GitOps path: %s\n" "$TARGET_PATH"
printf -- "- Argo CD application: %s\n" "$APP_NAME_RESOLVED"
printf -- "- Current Argo CD path: %s\n" "${CURRENT_PATH:-not-created}"

if [[ "$SELECTED_PROFILE" == "demo-lite" ]]; then
  printf -- "- Kafka topic shape: partitions 3, replication factor 1, min ISR 1\n"
  printf -- "- PostgreSQL standby check: disabled\n"
  printf -- "- Worker scale range: 1..2\n"
else
  printf -- "- Kafka topic shape: partitions 8, replication factor 3, min ISR 2\n"
  printf -- "- PostgreSQL standby check: enabled\n"
  printf -- "- Worker scale range: 2..8\n"
fi

if [[ "$APPLY" != "true" ]]; then
  log "Dry run complete"
  exit 0
fi

install_argocd_if_needed
log "Applying Argo CD project"
kubectl apply -f "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/k8s/argocd/project-messaging-portfolio.yaml"

log "Reconciling Argo CD Application"
apply_application "$TARGET_REVISION" "$TARGET_PATH"
kubectl get application "$APP_NAME_RESOLVED" -n "$ARGO_NAMESPACE"
