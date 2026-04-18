param(
  [string]$Namespace = "argocd"
)

$ErrorActionPreference = "Stop"

function Wait-Deployment([string]$Name, [string]$NamespaceToUse, [int]$TimeoutSec = 600) {
  kubectl rollout status "deployment/$Name" -n $NamespaceToUse --timeout="$($TimeoutSec)s"
}

function Wait-StatefulSet([string]$Name, [string]$NamespaceToUse, [int]$TimeoutSec = 600) {
  kubectl rollout status "statefulset/$Name" -n $NamespaceToUse --timeout="$($TimeoutSec)s"
}

kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n $Namespace -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

Wait-Deployment -Name "argocd-server" -NamespaceToUse $Namespace
Wait-Deployment -Name "argocd-repo-server" -NamespaceToUse $Namespace
Wait-StatefulSet -Name "argocd-application-controller" -NamespaceToUse $Namespace

Write-Host "Argo CD installed in namespace $Namespace."
