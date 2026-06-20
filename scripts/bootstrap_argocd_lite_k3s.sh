#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Jangwanko/Cloud_portfolio.git}"
REVISION="${REVISION:-demo-lite}"
APP_NAME="${APP_NAME:-messaging-portfolio-demo-lite}"
ARGO_NAMESPACE="${ARGO_NAMESPACE:-argocd}"
PROJECT_NAME="${PROJECT_NAME:-messaging-portfolio}"
MANIFEST_PATH="${MANIFEST_PATH:-k8s/gitops/overlays/demo-lite-k3s}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() {
  printf '\n==> %s\n' "$1"
}

fail() {
  printf '\n%s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

require_command kubectl

log "Installing Argo CD if needed"
kubectl create namespace "$ARGO_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n "$ARGO_NAMESPACE" -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl rollout status deployment/argocd-server -n "$ARGO_NAMESPACE" --timeout=600s
kubectl rollout status deployment/argocd-repo-server -n "$ARGO_NAMESPACE" --timeout=600s
kubectl rollout status statefulset/argocd-application-controller -n "$ARGO_NAMESPACE" --timeout=600s

log "Applying Argo CD project"
kubectl apply -f "$ROOT_DIR/k8s/argocd/project-messaging-portfolio.yaml"

log "Creating demo-lite Application"
cat <<YAML | kubectl apply -f -
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: ${APP_NAME}
  namespace: ${ARGO_NAMESPACE}
spec:
  project: ${PROJECT_NAME}
  source:
    repoURL: ${REPO_URL}
    targetRevision: ${REVISION}
    path: ${MANIFEST_PATH}
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

log "Argo CD Application created"
kubectl get application "$APP_NAME" -n "$ARGO_NAMESPACE"
