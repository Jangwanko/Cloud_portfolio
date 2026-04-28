param(
  [string]$BaseUrl = "http://localhost",
  [string]$PrometheusUrl = "http://localhost/prometheus",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [switch]$SkipReset,
  [switch]$SkipUnavailableReplicaScenario
)

$ErrorActionPreference = "Stop"

function Get-PrometheusJson([string]$Path) {
  $uri = "$PrometheusUrl$Path"
  $response = Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec 10
  if ($response.status -ne "success") {
    throw "Prometheus API call failed: $uri"
  }
  return $response.data
}

function Get-LoadedAlertNames() {
  $data = Get-PrometheusJson "/api/v1/rules"
  $names = New-Object System.Collections.Generic.List[string]
  foreach ($group in $data.groups) {
    foreach ($rule in $group.rules) {
      if ($rule.type -eq "alerting") {
        $names.Add([string]$rule.name)
      }
    }
  }
  return $names
}

function Assert-AlertRuleLoaded([string]$Name) {
  $names = Get-LoadedAlertNames
  if ($names -notcontains $Name) {
    throw "Prometheus alert rule is not loaded: $Name"
  }
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
    $states = Get-AlertStates $Name
    foreach ($state in $states) {
      if ($ExpectedStates -contains $state) {
        Write-Host ("Alert observed: {0} state={1}" -f $Name, $state)
        return
      }
    }
    Start-Sleep -Seconds 5
  }
  throw "Timed out waiting for alert $Name to enter one of: $($ExpectedStates -join ', ')"
}

function Wait-Ready([int]$TimeoutSec = 180) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health/ready" -TimeoutSec 5
      if ($health.status -eq "ready") { return }
    } catch {}
    Start-Sleep -Seconds 2
  }
  throw "Timed out waiting for readiness"
}

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

if (-not $SkipReset) {
  Invoke-Step "Reset state before operational alert test" {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment
  }
}

try {
  Invoke-Step "Check readiness and loaded alert rules" {
    Wait-Ready
    foreach ($name in @(
      "MessagingDlqEventsIncreasing",
      "MessagingDlqReplayBlocked",
      "MessagingDeploymentUnavailableReplicas"
    )) {
      Assert-AlertRuleLoaded $name
    }
  }

  Invoke-Step "Trigger DLQ event alert" {
    & "$PSScriptRoot/test_dlq_flow.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment -SkipReset
    Wait-AlertState "MessagingDlqEventsIncreasing" -ExpectedStates @("firing") -TimeoutSec 90
  }

  Invoke-Step "Trigger DLQ replay blocked alert" {
    & "$PSScriptRoot/test_dlq_replay_guard.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment -SkipReset
    Wait-AlertState "MessagingDlqReplayBlocked" -ExpectedStates @("firing") -TimeoutSec 120
  }

  if (-not $SkipUnavailableReplicaScenario) {
    Invoke-Step "Trigger unavailable replica alert with bad dlq-replayer rollout" {
      kubectl -n $Namespace set image deployment/dlq-replayer dlq-replayer=messaging-portfolio:alert-probe-missing | Out-Null
      try {
        Wait-AlertState "MessagingDeploymentUnavailableReplicas" -ExpectedStates @("pending", "firing") -TimeoutSec 90
      } finally {
        kubectl -n $Namespace set image deployment/dlq-replayer dlq-replayer=messaging-portfolio:local | Out-Null
        kubectl -n $Namespace rollout status deployment/dlq-replayer --timeout=180s | Out-Null
      }
    }
  }

  Write-Host ""
  Write-Host "Operational alert test passed."
}
finally {
  kubectl -n $Namespace set image deployment/dlq-replayer dlq-replayer=messaging-portfolio:local | Out-Null
  kubectl -n $Namespace rollout status deployment/dlq-replayer --timeout=120s | Out-Null
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment
  }
}
