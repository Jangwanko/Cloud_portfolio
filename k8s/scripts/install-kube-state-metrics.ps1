param(
  [string]$Namespace = "messaging-app"
)

$ErrorActionPreference = "Stop"

function Resolve-HelmPath {
  $cmd = Get-Command helm -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }

  $local = Join-Path $PSScriptRoot "..\..\tools\helm\windows-amd64\helm.exe"
  $resolved = Resolve-Path $local -ErrorAction SilentlyContinue
  if ($resolved) {
    return $resolved.Path
  }

  throw "helm executable not found. Install helm or place tools/helm/windows-amd64/helm.exe"
}

$helm = Resolve-HelmPath

& $helm repo add prometheus-community https://prometheus-community.github.io/helm-charts | Out-Null
& $helm repo update | Out-Null

kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f - | Out-Null

& $helm upgrade --install kube-state-metrics prometheus-community/kube-state-metrics `
  -n $Namespace `
  --wait --timeout 10m

kubectl rollout status deployment/kube-state-metrics -n $Namespace --timeout=300s | Out-Host
