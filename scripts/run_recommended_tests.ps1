param(
  [string]$BaseUrl = "http://localhost",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [string]$RedisDeployment = "messaging-redis-node",
  [switch]$SkipK6
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
    -DbDeployment $DbDeployment `
    -RedisDeployment $RedisDeployment
}

$k6Failed = $false

Invoke-Step "Reset before correctness tests" {
  Reset-State
}

Invoke-Step "Smoke test" {
  & "$PSScriptRoot/smoke_test.ps1" `
    -BaseUrl $BaseUrl `
    -Namespace $Namespace `
    -DbDeployment $DbDeployment `
    -RedisDeployment $RedisDeployment `
    -SkipReset
}

Invoke-Step "DB outage and recovery test" {
  & "$PSScriptRoot/test_db_down.ps1" `
    -BaseUrl $BaseUrl `
    -Namespace $Namespace `
    -ApiDeployment "api" `
    -DbDeployment $DbDeployment `
    -RedisDeployment $RedisDeployment `
    -SkipReset
}

Invoke-Step "Redis total outage and recovery test" {
  & "$PSScriptRoot/test_redis_down.ps1" `
    -BaseUrl $BaseUrl `
    -Namespace $Namespace `
    -DbDeployment $DbDeployment `
    -RedisDeployment $RedisDeployment `
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
        -DbDeployment $DbDeployment `
        -RedisDeployment $RedisDeployment
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
