param(
  [string]$BaseUrl = "http://localhost",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [switch]$SkipK6,
  [switch]$RunIsolatedExperiments,
  [string]$Context = ""
)

$ErrorActionPreference = "Stop"

function Invoke-Step([string]$Message, [scriptblock]$Action) {
  Write-Host ""
  Write-Host "==> $Message"
  $start = Get-Date
  try {
    & $Action
  } finally {
    $elapsed = (Get-Date) - $start
    Write-Host ("Elapsed: {0}s" -f ([math]::Round($elapsed.TotalSeconds, 2)))
  }
}

function Reset-State() {
  & "$PSScriptRoot/reset_k8s_state.ps1" `
    -BaseUrl $BaseUrl `
    -Namespace $Namespace `
    -DbDeployment $DbDeployment
}

# Routine verification adds unique fixtures and never resets or injects failures.
if (-not $RunIsolatedExperiments) {
  Invoke-Step "Smoke test" {
    & "$PSScriptRoot/smoke_test.ps1" -BaseUrl $BaseUrl -SkipReset
  }
  Invoke-Step "API contract test" {
    & "$PSScriptRoot/test_api_contracts.ps1" -BaseUrl $BaseUrl -SkipReset
  }
  Write-Host "Data-preserving checks passed; fixtures retained."
  return
}
# Existing failure scripts use current-context internally. Verify it; never switch it.
if (-not $Context -or $Namespace -notmatch '^portfolio-lab-[a-z0-9-]+$') {
  throw "Isolated experiments require -Context and a dedicated portfolio-lab-* namespace."
}
$currentContext = & kubectl config current-context
if ($LASTEXITCODE -ne 0 -or $currentContext.Trim() -ne $Context) {
  throw "Current kubectl context does not match the explicit isolated context."
}
# Own the forwarding process so HTTP and kubectl target the same isolated namespace.
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$localPort = $listener.LocalEndpoint.Port
$listener.Stop()
$BaseUrl = "http://127.0.0.1:$localPort"
$forward = Start-Process -FilePath "kubectl" -ArgumentList @("--context", $Context,
  "-n", $Namespace, "port-forward", "service/api", "${localPort}:8000", "--address", "127.0.0.1") `
  -WindowStyle Hidden -PassThru
try {
  $connected = $false
  for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if ($forward.HasExited) { throw "Isolated API port-forward exited" }
    try {
      Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/health/live" -TimeoutSec 1 | Out-Null
      $connected = $true
      break
    } catch { Start-Sleep -Milliseconds 250 }
  }
  if (-not $connected) { throw "Isolated API port-forward did not become ready" }

$k6Failed = $false

Invoke-Step "Reset before correctness tests" {
  Reset-State
}

Invoke-Step "Smoke test" {
  & "$PSScriptRoot/smoke_test.ps1" `
    -BaseUrl $BaseUrl `
    -Namespace $Namespace `
    -DbDeployment $DbDeployment `
    -SkipReset
}

Invoke-Step "API contract test" {
  & "$PSScriptRoot/test_api_contracts.ps1" `
    -BaseUrl $BaseUrl `
    -Namespace $Namespace `
    -DbDeployment $DbDeployment `
    -SkipReset
}

Invoke-Step "DB outage and recovery test" {
  & "$PSScriptRoot/test_db_down.ps1" `
    -BaseUrl $BaseUrl `
    -Namespace $Namespace `
    -ApiDeployment "api" `
    -DbDeployment $DbDeployment `
    -SkipReset
}

Invoke-Step "DLQ flow test" {
  & "$PSScriptRoot/test_dlq_flow.ps1" `
    -BaseUrl $BaseUrl `
    -Namespace $Namespace `
    -DbDeployment $DbDeployment `
    -SkipReset
}

Invoke-Step "DLQ replay guard test" {
  & "$PSScriptRoot/test_dlq_replay_guard.ps1" `
    -BaseUrl $BaseUrl `
    -Namespace $Namespace `
    -DbDeployment $DbDeployment `
    -SkipReset
}

Invoke-Step "Reset before load test" {
  Reset-State
}

if (-not $SkipK6) {
  Invoke-Step "k6 load test (last)" {
    try {
      & "$PSScriptRoot/test_k6_load.ps1" `
        -BaseUrl $BaseUrl `
        -Namespace $Namespace `
        -DbDeployment $DbDeployment
    } catch {
      $script:k6Failed = $true
      Write-Warning $_.Exception.Message
      Write-Warning "k6 load execution completed but threshold may have failed. Reset will still run."
    }
  }
}

Invoke-Step "Final reset after load test" {
  Reset-State
}

if ($k6Failed) {
  Write-Host ""
  Write-Host "Recommended test flow finished, but k6 threshold failed. Treat this as a performance tuning signal, not a functional failure."
  exit 2
}

Write-Host ""
Write-Host "Recommended test flow completed successfully."

} finally {
  if (-not $forward.HasExited) { Stop-Process -Id $forward.Id -ErrorAction SilentlyContinue }
}
