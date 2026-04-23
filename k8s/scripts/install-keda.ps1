param(
  [string]$Namespace = "keda"
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

& $helm repo add kedacore https://kedacore.github.io/charts | Out-Null
& $helm repo update | Out-Null

kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f - | Out-Null

& $helm upgrade --install keda kedacore/keda `
  -n $Namespace `
  --wait --timeout 10m

kubectl rollout status deployment/keda-operator -n $Namespace --timeout=300s | Out-Host
