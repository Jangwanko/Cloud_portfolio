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
$failedResultPath = Join-Path $resultDir ("failed-{0}.txt" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$lines = [System.Collections.Generic.List[string]]::new()
$suiteSucceeded = $false

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

function Get-KubernetesJson([string[]]$Arguments) {
  $raw = & kubectl @Arguments -o json
  if ($LASTEXITCODE -ne 0 -or -not $raw) {
    throw "kubectl failed: $($Arguments -join ' ')"
  }
  return ($raw | ConvertFrom-Json)
}

function Assert-DeploymentReady([string]$Name) {
  $deployment = Get-KubernetesJson @("-n", $Namespace, "get", "deployment", $Name)
  $desired = [int]$deployment.spec.replicas
  $ready = [int]$deployment.status.readyReplicas
  $updated = [int]$deployment.status.updatedReplicas
  if ($ready -ne $desired -or $updated -ne $desired) {
    throw "deployment/$Name is not fully rolled out (desired=$desired ready=$ready updated=$updated)"
  }
  return $deployment
}

function Assert-KubernetesReady() {
  $null = Get-KubernetesJson @("get", "namespace", $Namespace)
  $api = Assert-DeploymentReady -Name "api"
  $worker = Assert-DeploymentReady -Name "worker"
  foreach ($name in @("notification-worker", "dlq-replayer", "prometheus", "kafka-exporter", "kube-state-metrics")) {
    $null = Assert-DeploymentReady -Name $name
  }

  $kafka = Get-KubernetesJson @("-n", $Namespace, "get", "statefulset", "kafka")
  if ([int]$kafka.status.readyReplicas -ne [int]$kafka.spec.replicas) {
    throw "statefulset/kafka is not fully ready"
  }

  $apiImage = [string]$api.spec.template.spec.containers[0].image
  $workerImage = [string]$worker.spec.template.spec.containers[0].image
  if ($apiImage -ne $workerImage) {
    throw "API and Worker images differ (api=$apiImage worker=$workerImage)"
  }
  $gate = $api.spec.template.spec.containers[0].env | Where-Object { $_.name -eq "GENERIC_EVENTS_V2_ENABLED" } | Select-Object -Last 1
  if ($null -eq $gate -or [string]$gate.value -ne "true") {
    throw "API generic v2 gate is not enabled"
  }

  $health = Invoke-RestMethod -Method Get -Uri "$($BaseUrl.TrimEnd('/'))/health/ready" -TimeoutSec 10
  if ([string]$health.status -ne "ready") {
    throw "API readiness is not ready: $($health.status)"
  }
  $openapi = Invoke-RestMethod -Method Get -Uri "$($BaseUrl.TrimEnd('/'))/openapi.json" -TimeoutSec 10
  if ([string]$openapi.info.version -ne "2.0.0" -or $null -eq $openapi.paths.'/v2/streams/{stream_id}/events') {
    throw "Deployed OpenAPI is not the generic v2 contract"
  }

  $workerLag = Get-ConsumerLag -ConsumerGroup "message-worker"
  $notificationLag = Get-ConsumerLag -ConsumerGroup "notification-worker"
  if ($workerLag -ne 0 -or $notificationLag -ne 0) {
    throw "Consumer lag must start at zero (message-worker=$workerLag notification-worker=$notificationLag)"
  }

  $script:ApiImage = $apiImage
  $script:WorkerImage = $workerImage
  $script:InitialWorkerLag = $workerLag
  $script:InitialNotificationLag = $notificationLag
}

function Get-PrometheusScalar([string]$PromQl, [string]$MetricLabel) {
  $query = [uri]::EscapeDataString($PromQl)
  $response = Invoke-RestMethod -Method Get -Uri "$($PrometheusUrl.TrimEnd('/'))/api/v1/query?query=$query" -TimeoutSec 10
  $result = @($response.data.result)
  if ($response.status -ne "success" -or $result.Count -eq 0) {
    throw "Prometheus metric is absent: $MetricLabel"
  }
  $value = [double]$result[0].value[1]
  if ([double]::IsNaN($value) -or [double]::IsInfinity($value)) {
    throw "Prometheus metric is not finite: $MetricLabel"
  }
  return $value
}

function Get-ConsumerLag([string]$ConsumerGroup) {
  return Get-PrometheusScalar `
    -PromQl "sum(clamp_min(kafka_consumergroup_lag{consumergroup=`"$ConsumerGroup`"}, 0))" `
    -MetricLabel "Kafka lag for consumer group $ConsumerGroup"
}

function Wait-ConsumerLagDrain(
  [Parameter(Mandatory = $true)]
  [string]$Phase,
  [Parameter(Mandatory = $true)]
  [datetime]$StartedAt,
  [object[]]$InitialWorkerLagSamples = @(),
  [int]$RequiredConsecutiveZeroSamples = 1
) {
  if ($RequiredConsecutiveZeroSamples -lt 1) {
    throw "RequiredConsecutiveZeroSamples must be at least 1"
  }
  $drainDeadline = (Get-Date).AddSeconds($LagDrainTimeoutSec)
  $drainedAt = $null
  $consecutiveZeroSamples = 0
  $workerLagSamples = [System.Collections.Generic.List[double]]::new()
  $notificationLagSamples = [System.Collections.Generic.List[double]]::new()
  foreach ($sample in @($InitialWorkerLagSamples)) {
    if ($null -ne $sample -and $null -ne $sample.lag) {
      [void]$workerLagSamples.Add([double]$sample.lag)
    }
  }

  $lastWorkerLag = $null
  $lastNotificationLag = $null
  while ((Get-Date) -lt $drainDeadline) {
    $lastWorkerLag = Get-ConsumerLag -ConsumerGroup "message-worker"
    $lastNotificationLag = Get-ConsumerLag -ConsumerGroup "notification-worker"
    [void]$workerLagSamples.Add($lastWorkerLag)
    [void]$notificationLagSamples.Add($lastNotificationLag)
    Add-Line ("{0}: message-worker lag={1}; notification-worker lag={2}" -f $Phase, $lastWorkerLag, $lastNotificationLag)
    if ($lastWorkerLag -eq 0 -and $lastNotificationLag -eq 0) {
      $consecutiveZeroSamples += 1
      if ($consecutiveZeroSamples -ge $RequiredConsecutiveZeroSamples) {
        $drainedAt = Get-Date
        break
      }
    } else {
      $consecutiveZeroSamples = 0
    }
    Start-Sleep -Seconds $LagSampleIntervalSec
  }

  if ($null -eq $drainedAt) {
    throw ("{0} consumer lag did not drain to zero within {1} seconds (message-worker={2}, notification-worker={3})" -f $Phase, $LagDrainTimeoutSec, $lastWorkerLag, $lastNotificationLag)
  }

  $workerPeakLag = ($workerLagSamples | Measure-Object -Maximum).Maximum
  $notificationPeakLag = ($notificationLagSamples | Measure-Object -Maximum).Maximum
  $drainSeconds = [math]::Round(($drainedAt - $StartedAt).TotalSeconds, 2)
  Add-Line ("{0}_message_worker_peak_consumer_lag: {1}" -f $Phase, $workerPeakLag)
  Add-Line ("{0}_notification_worker_peak_consumer_lag: {1}" -f $Phase, $notificationPeakLag)
  Add-Line ("{0}_all_consumer_backlog_drain_seconds: {1}" -f $Phase, $drainSeconds)
  Add-Line ("{0}_message_worker_final_consumer_lag: {1}" -f $Phase, $lastWorkerLag)
  Add-Line ("{0}_notification_worker_final_consumer_lag: {1}" -f $Phase, $lastNotificationLag)

  return [pscustomobject]@{
    Phase = $Phase
    MessageWorkerPeakLag = $workerPeakLag
    NotificationWorkerPeakLag = $notificationPeakLag
    DrainSeconds = $drainSeconds
    MessageWorkerFinalLag = $lastWorkerLag
    NotificationWorkerFinalLag = $lastNotificationLag
  }
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
Add-Line ("source_branch: {0}" -f ((& git branch --show-current | Out-String).Trim()))
Add-Line ("source_commit: {0}" -f ((& git rev-parse HEAD | Out-String).Trim()))

try {
  Invoke-SuiteStep "Preflight Kubernetes state" {
    Assert-KubernetesReady
    Add-Line ("api_image: {0}" -f $script:ApiImage)
    Add-Line ("worker_image: {0}" -f $script:WorkerImage)
    Add-Line ("initial_message_worker_lag: {0}" -f $script:InitialWorkerLag)
    Add-Line ("initial_notification_worker_lag: {0}" -f $script:InitialNotificationLag)
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
          $query = [uri]::EscapeDataString('sum(clamp_min(kafka_consumergroup_lag{consumergroup="message-worker"}, 0))')
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
        -SkipReset
      $k6Output | Out-String | ForEach-Object { Add-Line $_.TrimEnd() }
    } finally {
      Stop-Job -Job $lagJob -ErrorAction SilentlyContinue
      $script:LagSamples = @(Receive-Job -Job $lagJob -ErrorAction SilentlyContinue)
      Remove-Job -Job $lagJob -Force -ErrorAction SilentlyContinue
      $script:LoadCompletedAt = Get-Date
    }
  }

  Invoke-SuiteStep "Main load Kafka consumer lag drain" {
    $script:MainLoadDrain = Wait-ConsumerLagDrain `
      -Phase "main_load" `
      -StartedAt $script:LoadCompletedAt `
      -InitialWorkerLagSamples @($script:LagSamples)
    $persistLagP95 = Get-PrometheusScalar `
      -PromQl 'histogram_quantile(0.95, sum(rate(messaging_event_persist_lag_seconds_bucket{job="worker"}[5m])) by (le))' `
      -MetricLabel "Worker accepted-to-commit lag p95"
    Add-Line ("worker accepted_to_commit_lag_p95_5m_seconds: {0}" -f $persistLagP95)
  }

  Invoke-SuiteStep "HPA and metrics sanity" {
    $hpaOutput = & "$PSScriptRoot/test_hpa_scaling.ps1" `
      -Namespace $Namespace `
      -DeploymentName "api" `
      -HpaName "api-hpa" `
      -TimeoutSec 90
    $hpaOutput | Out-String | ForEach-Object { Add-Line $_.TrimEnd() }
    $script:HpaLoadCompletedAt = Get-Date
  }

  Invoke-SuiteStep "Post-HPA Kafka consumer lag drain" {
    $script:PostHpaDrain = Wait-ConsumerLagDrain `
      -Phase "post_hpa" `
      -StartedAt $script:HpaLoadCompletedAt `
      -RequiredConsecutiveZeroSamples 2
  }

  Invoke-SuiteStep "Final runtime snapshot" {
    kubectl -n $Namespace get pods | Out-String | ForEach-Object { Add-Line $_.TrimEnd() }
    kubectl -n $Namespace get hpa | Out-String | ForEach-Object { Add-Line $_.TrimEnd() }
  }

  Add-Line ""
  Add-Line "Kafka performance suite completed successfully."
  $suiteSucceeded = $true
}
catch {
  Add-Line ""
  Add-Line ("Kafka performance suite failed: {0}" -f $_.Exception.Message)
  throw
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

  $outputPath = if ($suiteSucceeded) { $resultPath } else { $failedResultPath }
  $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
  [System.IO.File]::WriteAllLines($outputPath, [string[]]$lines, $utf8NoBom)
  Write-Host ""
  Write-Host "Performance suite result written to $outputPath"
}
