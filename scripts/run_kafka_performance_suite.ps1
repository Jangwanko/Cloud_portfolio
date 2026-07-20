param(
  [string]$BaseUrl = "http://localhost",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [int]$OrderingEventCount = 100,
  [int]$EventCount = 50,
  [string]$K6Profile = "single500",
  [int]$K6SingleVus = 100,
  [string]$StageDuration = "30s",
  [double]$ThinkTime = 0.05,
  [int]$TimeoutSec = 600,
  [string]$PrometheusUrl = "http://localhost/prometheus",
  [int]$LagSampleIntervalSec = 5,
  [int]$LagDrainTimeoutSec = 1200,
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

function Get-ConsumerLag([string]$ConsumerGroup) {
  $query = [uri]::EscapeDataString("sum(kafka_consumergroup_lag{consumergroup=`"$ConsumerGroup`"})")
  $response = Invoke-RestMethod -Method Get -Uri "$($PrometheusUrl.TrimEnd('/'))/api/v1/query?query=$query" -TimeoutSec 10
  $result = @($response.data.result)
  if ($response.status -ne "success" -or $result.Count -eq 0) {
    throw "Kafka lag metric is absent for consumer group $ConsumerGroup"
  }
  return [double]$result[0].value[1]
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
Add-Line ("ordering_event_count: {0}" -f $OrderingEventCount)
Add-Line ("event_count: {0}" -f $EventCount)
Add-Line ("lag_sample_interval_seconds: {0}" -f $LagSampleIntervalSec)
Add-Line ("lag_drain_timeout_seconds: {0}" -f $LagDrainTimeoutSec)

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

  Invoke-SuiteStep "Same-stream ordering guarantee" {
    $orderingOutput = & "$PSScriptRoot/test_stream_ordering.ps1" `
      -BaseUrl $BaseUrl `
      -Namespace $Namespace `
      -DbDeployment $DbDeployment `
      -EventCount $OrderingEventCount `
      -SkipReset
    $orderingOutput | Out-String | ForEach-Object { Add-Line $_.TrimEnd() }
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
    $lagJob = Start-Job -ScriptBlock {
      param($PrometheusBaseUrl, $SampleIntervalSec)
      while ($true) {
        try {
          $query = [uri]::EscapeDataString('sum(kafka_consumergroup_lag{consumergroup="message-worker"})')
          $response = Invoke-RestMethod -Method Get -Uri "$($PrometheusBaseUrl.TrimEnd('/'))/api/v1/query?query=$query" -TimeoutSec 10
          $result = @($response.data.result)
          if ($response.status -eq "success" -and $result.Count -gt 0) {
            [pscustomobject]@{ timestamp = [DateTimeOffset]::UtcNow; lag = [double]$result[0].value[1] }
          }
        } catch {
          # The foreground drain check treats a missing metric as a failure.
        }
        Start-Sleep -Seconds $SampleIntervalSec
      }
    } -ArgumentList $PrometheusUrl, $LagSampleIntervalSec
    try {
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
    } finally {
      Stop-Job -Job $lagJob -ErrorAction SilentlyContinue
      $script:LagSamples = @(Receive-Job -Job $lagJob -ErrorAction SilentlyContinue)
      Remove-Job -Job $lagJob -Force -ErrorAction SilentlyContinue
      $script:LoadCompletedAt = Get-Date
    }
  }

  Invoke-SuiteStep "Kafka consumer lag drain" {
    $drainDeadline = (Get-Date).AddSeconds($LagDrainTimeoutSec)
    $drainedAt = $null
    $notificationLagSamples = [System.Collections.Generic.List[double]]::new()
    $lastWorkerLag = $null
    $lastNotificationLag = $null
    while ((Get-Date) -lt $drainDeadline) {
      $lastWorkerLag = Get-ConsumerLag -ConsumerGroup "message-worker"
      $lastNotificationLag = Get-ConsumerLag -ConsumerGroup "notification-worker"
      $script:LagSamples += [pscustomobject]@{ timestamp = [DateTimeOffset]::UtcNow; lag = $lastWorkerLag }
      [void]$notificationLagSamples.Add($lastNotificationLag)
      Add-Line ("message-worker lag={0}; notification-worker lag={1}" -f $lastWorkerLag, $lastNotificationLag)
      if ($lastWorkerLag -eq 0 -and $lastNotificationLag -eq 0) {
        $drainedAt = Get-Date
        break
      }
      Start-Sleep -Seconds $LagSampleIntervalSec
    }
    if ($null -eq $drainedAt) {
      throw ("consumer lag did not drain to zero within {0} seconds (message-worker={1}, notification-worker={2})" -f $LagDrainTimeoutSec, $lastWorkerLag, $lastNotificationLag)
    }
    $peakLag = ($script:LagSamples | Measure-Object -Property lag -Maximum).Maximum
    $notificationPeakLag = ($notificationLagSamples | Measure-Object -Maximum).Maximum
    $drainSeconds = [math]::Round(($drainedAt - $script:LoadCompletedAt).TotalSeconds, 2)
    Add-Line ("message-worker peak_consumer_lag: {0}" -f $peakLag)
    Add-Line ("notification-worker peak_consumer_lag_during_drain: {0}" -f $notificationPeakLag)
    Add-Line ("all-consumer backlog_drain_seconds_after_load: {0}" -f $drainSeconds)
    Add-Line ("message-worker final_consumer_lag: 0")
    Add-Line ("notification-worker final_consumer_lag: 0")
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
