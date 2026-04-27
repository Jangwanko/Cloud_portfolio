param(
  [string]$BaseUrl = "http://localhost",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [int]$EventCount = 50,
  [string]$K6Profile = "single500",
  [int]$K6SingleVus = 100,
  [string]$StageDuration = "30s",
  [double]$ThinkTime = 0.05,
  [int]$TimeoutSec = 600,
  [switch]$SkipReset
)

$ErrorActionPreference = "Stop"

$resultDir = Join-Path $PSScriptRoot "..\results\kafka-performance"
$resultPath = Join-Path $resultDir "latest.txt"
$lines = [System.Collections.Generic.List[string]]::new()

function Add-Line([string]$Value = "") {
  [void]$lines.Add($Value)
  Write-Host $Value
}

function Invoke-SuiteStep([string]$Name, [scriptblock]$Action) {
  Add-Line ""
  Add-Line "==> $Name"
  $started = Get-Date
  & $Action
  $elapsed = (Get-Date) - $started
  Add-Line ("Elapsed: {0}s" -f ([math]::Round($elapsed.TotalSeconds, 2)))
}

function Assert-KubernetesReady() {
  kubectl get namespace $Namespace | Out-Null
  kubectl -n $Namespace get deployment api | Out-Null
  kubectl -n $Namespace get deployment worker | Out-Null
  kubectl -n $Namespace get statefulset kafka | Out-Null
}

New-Item -ItemType Directory -Force -Path $resultDir | Out-Null

Add-Line "# Kafka Performance Suite"
Add-Line ("timestamp: {0:o}" -f (Get-Date))
Add-Line ("namespace: {0}" -f $Namespace)
Add-Line ("base_url: {0}" -f $BaseUrl)
Add-Line ("k6_profile: {0}" -f $K6Profile)
Add-Line ("k6_single_vus: {0}" -f $K6SingleVus)
Add-Line ("stage_duration: {0}" -f $StageDuration)
Add-Line ("think_time: {0}" -f $ThinkTime)
Add-Line ("event_count: {0}" -f $EventCount)

try {
  Invoke-SuiteStep "Preflight Kubernetes state" {
    Assert-KubernetesReady
    kubectl -n $Namespace get pods | Out-String | ForEach-Object { Add-Line $_.TrimEnd() }
  }

  if (-not $SkipReset) {
    Invoke-SuiteStep "Reset before performance suite" {
      & "$PSScriptRoot/reset_k8s_state.ps1" `
        -BaseUrl $BaseUrl `
        -Namespace $Namespace `
        -DbDeployment $DbDeployment
    }
  }

  Invoke-SuiteStep "Kafka async persistence latency" {
    $latencyOutput = & "$PSScriptRoot/test_event_persist_latency.ps1" `
      -BaseUrl $BaseUrl `
      -Namespace $Namespace `
      -DbDeployment $DbDeployment `
      -EventCount $EventCount `
      -SkipReset
    $latencyOutput | Out-String | ForEach-Object { Add-Line $_.TrimEnd() }
  }

  Invoke-SuiteStep "k6 Kafka intake load" {
    $k6Output = & "$PSScriptRoot/test_k6_load.ps1" `
      -BaseUrl $BaseUrl `
      -Namespace $Namespace `
      -DbDeployment $DbDeployment `
      -K6Profile $K6Profile `
      -K6SingleVus $K6SingleVus `
      -StageDuration $StageDuration `
      -ThinkTime $ThinkTime `
      -TimeoutSec $TimeoutSec `
      -AllowThresholdFailure `
      -SkipReset
    $k6Output | Out-String | ForEach-Object { Add-Line $_.TrimEnd() }
  }

  Invoke-SuiteStep "HPA and metrics sanity" {
    $hpaOutput = & "$PSScriptRoot/test_hpa_scaling.ps1" `
      -Namespace $Namespace `
      -DeploymentName "api" `
      -HpaName "api-hpa" `
      -TimeoutSec 90
    $hpaOutput | Out-String | ForEach-Object { Add-Line $_.TrimEnd() }
  }

  Invoke-SuiteStep "Final runtime snapshot" {
    kubectl -n $Namespace get pods | Out-String | ForEach-Object { Add-Line $_.TrimEnd() }
    kubectl -n $Namespace get hpa | Out-String | ForEach-Object { Add-Line $_.TrimEnd() }
  }

  Add-Line ""
  Add-Line "Kafka performance suite completed successfully."
}
finally {
  if (-not $SkipReset) {
    Add-Line ""
    Add-Line "==> Final reset"
    & "$PSScriptRoot/reset_k8s_state.ps1" `
      -BaseUrl $BaseUrl `
      -Namespace $Namespace `
      -DbDeployment $DbDeployment
  }

  Set-Content -Path $resultPath -Value $lines -Encoding UTF8
  Write-Host ""
  Write-Host "Performance suite result written to $resultPath"
}
