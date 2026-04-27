param(
  [string]$Namespace = "messaging-app",
  [string]$JobManifest = "k8s/app/k6-job.yaml",
  [string]$ScriptPath = "scripts/load_test_k6.js",
  [string]$BaseUrl = "http://localhost",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [string]$K6Profile = "single500",
  [int]$K6SingleVus = 100,
  [string]$StageDuration = "10s",
  [double]$ThinkTime = 0.05,
  [int]$TimeoutSec = 420,
  [switch]$AllowThresholdFailure,
  [switch]$SkipReset
)

$ErrorActionPreference = "Stop"

function Set-JobEnvValue([string]$Yaml, [string]$Name, [string]$Value) {
  $pattern = "(?m)(\s*-\s+name:\s+$([regex]::Escape($Name))\s*\r?\n\s*value:\s*)`"[^`"]*`""
  $replacement = "`${1}`"$Value`""
  return [regex]::Replace($Yaml, $pattern, $replacement)
}

if (-not $SkipReset) {
  & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment
}

$effectiveManifest = $null

try {
  kubectl -n $Namespace delete job k6-load-test --ignore-not-found | Out-Null
  kubectl -n $Namespace delete configmap k6-script --ignore-not-found | Out-Null
  kubectl -n $Namespace create configmap k6-script --from-file=load_test_k6.js=$ScriptPath | Out-Null

  $manifestText = Get-Content $JobManifest -Raw
  $manifestText = Set-JobEnvValue -Yaml $manifestText -Name "K6_PROFILE" -Value $K6Profile
  $manifestText = Set-JobEnvValue -Yaml $manifestText -Name "K6_SINGLE_VUS" -Value ([string]$K6SingleVus)
  $manifestText = Set-JobEnvValue -Yaml $manifestText -Name "STAGE_DURATION" -Value $StageDuration
  $manifestText = Set-JobEnvValue -Yaml $manifestText -Name "THINK_TIME" -Value ([string]$ThinkTime)

  $effectiveManifest = Join-Path ([System.IO.Path]::GetTempPath()) ("k6-load-test-{0}.yaml" -f ([guid]::NewGuid().ToString("N")))
  Set-Content -Path $effectiveManifest -Value $manifestText -Encoding UTF8

  Write-Host ("Running k6 load test: profile={0}, vus={1}, duration={2}, think_time={3}" -f $K6Profile, $K6SingleVus, $StageDuration, $ThinkTime)
  kubectl apply -f $effectiveManifest | Out-Null

  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $succeeded = kubectl -n $Namespace get job k6-load-test -o jsonpath="{.status.succeeded}" 2>$null
    $failed = kubectl -n $Namespace get job k6-load-test -o jsonpath="{.status.failed}" 2>$null
    $succeededCount = 0
    $failedCount = 0
    [void][int]::TryParse(($succeeded | Out-String).Trim(), [ref]$succeededCount)
    [void][int]::TryParse(($failed | Out-String).Trim(), [ref]$failedCount)

    if ($succeededCount -ge 1) {
      break
    }
    if ($failedCount -ge 1) {
      break
    }
    Start-Sleep -Seconds 2
  }

  $podDeadline = (Get-Date).AddSeconds(120)
  $podName = $null
  while ((Get-Date) -lt $podDeadline) {
    $podName = kubectl -n $Namespace get pods -l job-name=k6-load-test -o jsonpath="{.items[0].metadata.name}" 2>$null
    if ($podName) { break }
    Start-Sleep -Seconds 2
  }

  if (-not $podName) {
    throw "k6 pod was not created in time"
  }

  $containerReady = $false
  $readyDeadline = (Get-Date).AddSeconds(120)
  while ((Get-Date) -lt $readyDeadline) {
    $waitingReason = kubectl -n $Namespace get pod $podName -o jsonpath="{.status.containerStatuses[0].state.waiting.reason}" 2>$null
    $terminatedReason = kubectl -n $Namespace get pod $podName -o jsonpath="{.status.containerStatuses[0].state.terminated.reason}" 2>$null
    if ($terminatedReason) {
      $containerReady = $true
      break
    }
    if ($waitingReason -and $waitingReason -ne "ContainerCreating") {
      throw "k6 pod is waiting with reason: $waitingReason"
    }
    $started = kubectl -n $Namespace get pod $podName -o jsonpath="{.status.containerStatuses[0].started}" 2>$null
    if ($started -eq "true") {
      $containerReady = $true
      break
    }
    Start-Sleep -Seconds 2
  }

  if (-not $containerReady) {
    throw "k6 container did not start in time"
  }

  kubectl -n $Namespace logs job/k6-load-test

  $failedFinal = kubectl -n $Namespace get job k6-load-test -o jsonpath="{.status.failed}" 2>$null
  $failedFinalCount = 0
  [void][int]::TryParse(($failedFinal | Out-String).Trim(), [ref]$failedFinalCount)
  if ($failedFinalCount -ge 1 -and -not $AllowThresholdFailure) {
    throw "k6 job finished as Failed (likely threshold exceeded)"
  }
  if ($failedFinalCount -ge 1) {
    Write-Warning "k6 job finished as Failed (likely threshold exceeded); continuing because AllowThresholdFailure is set."
  }
}
finally {
  kubectl -n $Namespace delete job k6-load-test --ignore-not-found | Out-Null
  kubectl -n $Namespace delete configmap k6-script --ignore-not-found | Out-Null
  if ($effectiveManifest -and (Test-Path $effectiveManifest)) {
    Remove-Item -LiteralPath $effectiveManifest -Force
  }
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment
  }
}
