param(
  [string]$BaseUrl = "http://localhost",
  [string]$PrometheusUrl = "http://localhost/prometheus",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [switch]$SkipDbOutage,
  [switch]$SkipDlqAlerts,
  [switch]$SkipWorkerRollout
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

function Get-PrometheusJson([string]$Path) {
  $response = Invoke-RestMethod -Method Get -Uri "$PrometheusUrl$Path" -TimeoutSec 10
  if ($response.status -ne "success") {
    throw "Prometheus API call failed: $PrometheusUrl$Path"
  }
  return $response.data
}

function Get-AlertStates([string]$Name) {
  $data = Get-PrometheusJson "/api/v1/rules"
  $states = New-Object System.Collections.Generic.List[string]
  foreach ($group in $data.groups) {
    foreach ($rule in $group.rules) {
      if ($rule.type -eq "alerting" -and $rule.name -eq $Name) {
        foreach ($alert in $rule.alerts) {
          $states.Add([string]$alert.state)
        }
      }
    }
  }
  return $states
}

function Wait-AlertState([string]$Name, [string[]]$ExpectedStates = @("pending", "firing"), [int]$TimeoutSec = 90) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    foreach ($state in (Get-AlertStates $Name)) {
      if ($ExpectedStates -contains $state) {
        Write-Host ("Alert observed: {0} state={1}" -f $Name, $state)
        return
      }
    }
    Start-Sleep -Seconds 5
  }
  throw "Timed out waiting for alert $Name to enter one of: $($ExpectedStates -join ', ')"
}

try {
  if (-not $SkipDbOutage) {
    Invoke-Step "PostgreSQL outage and recovery signal" {
      & "$PSScriptRoot/test_db_down.ps1" `
        -BaseUrl $BaseUrl `
        -Namespace $Namespace `
        -DbDeployment $DbDeployment `
        -SkipReset
    }
  }

  if (-not $SkipDlqAlerts) {
    Invoke-Step "DLQ alert signal probe" {
      & "$PSScriptRoot/test_operational_alerts.ps1" `
        -BaseUrl $BaseUrl `
        -PrometheusUrl $PrometheusUrl `
        -Namespace $Namespace `
        -DbDeployment $DbDeployment `
        -SkipReset `
        -SkipUnavailableReplicaScenario
    }
  }

  if (-not $SkipWorkerRollout) {
    Invoke-Step "Worker bad rollout unavailable replica signal" {
      kubectl -n $Namespace set image deployment/worker worker=messaging-portfolio:incident-probe-missing | Out-Null
      try {
        Wait-AlertState "MessagingDeploymentUnavailableReplicas" -ExpectedStates @("pending", "firing") -TimeoutSec 90
      } finally {
        kubectl -n $Namespace set image deployment/worker worker=messaging-portfolio:local | Out-Null
        kubectl -n $Namespace rollout status deployment/worker --timeout=180s | Out-Null
      }
    }
  }

  Write-Host ""
  Write-Host "Incident signal test passed."
}
finally {
  kubectl -n $Namespace set image deployment/worker worker=messaging-portfolio:local | Out-Null
  kubectl -n $Namespace set image deployment/dlq-replayer dlq-replayer=messaging-portfolio:local | Out-Null
  kubectl -n $Namespace rollout status deployment/worker --timeout=180s | Out-Null
  kubectl -n $Namespace rollout status deployment/dlq-replayer --timeout=180s | Out-Null
}
