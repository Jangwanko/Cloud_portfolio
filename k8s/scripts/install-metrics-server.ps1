param(
  [int]$TimeoutSec = 240
)

$ErrorActionPreference = "Stop"

$manifestPath = Join-Path $PSScriptRoot "..\metrics-server-components.yaml"

if (-not (Test-Path $manifestPath)) {
  throw "metrics-server manifest not found: $manifestPath"
}

function Wait-MetricsApi([int]$WaitSec) {
  $deadline = (Get-Date).AddSeconds($WaitSec)
  while ((Get-Date) -lt $deadline) {
    try {
      kubectl top nodes | Out-Null
      return
    } catch {}
    Start-Sleep -Seconds 3
  }

  throw "Timed out waiting for metrics-server API to become available."
}

kubectl apply -f $manifestPath | Out-Host
kubectl rollout status deployment/metrics-server -n kube-system --timeout="$($TimeoutSec)s" | Out-Host
Wait-MetricsApi -WaitSec $TimeoutSec
Write-Host "metrics-server is installed and metrics API is available."
