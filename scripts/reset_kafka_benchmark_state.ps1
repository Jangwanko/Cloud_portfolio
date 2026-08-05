param(
  [string]$Namespace = "messaging-app",
  [int]$TimeoutSec = 300,
  [ValidateRange(60, 300)]
  [int]$KafkaCleanupQuietSec = 75,
  [switch]$ConfirmDataLoss
)

$ErrorActionPreference = "Stop"

if (-not $ConfirmDataLoss) {
  throw "This reset deletes local benchmark events and Kafka topic data. Pass -ConfirmDataLoss to continue."
}

function Wait-DeploymentReplicas([string]$Name, [int]$Expected) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $raw = kubectl -n $Namespace get deployment $Name -o jsonpath="{.status.readyReplicas}" 2>$null
    $ready = 0
    [void][int]::TryParse(($raw | Out-String).Trim(), [ref]$ready)
    if ($ready -eq $Expected) {
      return
    }
    Start-Sleep -Seconds 2
  }
  throw "Timed out waiting for deployment/$Name ready replicas=$Expected"
}

function Assert-KubectlSuccess([string]$Operation) {
  if ($LASTEXITCODE -ne 0) {
    throw "kubectl failed while $Operation (exit=$LASTEXITCODE)"
  }
}

$cleanupErrors = [System.Collections.Generic.List[string]]::new()
function Invoke-CleanupKubectl([string]$Operation, [string[]]$Arguments) {
  kubectl @Arguments | Out-Null
  if ($LASTEXITCODE -ne 0) {
    [void]$script:cleanupErrors.Add("$Operation (exit=$LASTEXITCODE)")
  }
}

$resetSucceeded = $false
$resetError = $null
try {
  kubectl -n $Namespace annotate scaledobject worker-keda `
    autoscaling.keda.sh/paused-replicas="0" `
    --overwrite | Out-Null
  Assert-KubectlSuccess -Operation "pausing worker KEDA"
  kubectl -n $Namespace scale deployment/notification-worker deployment/dlq-replayer `
    --replicas=0 | Out-Null
  Assert-KubectlSuccess -Operation "scaling auxiliary consumers to zero"

  Wait-DeploymentReplicas -Name "worker" -Expected 0
  Wait-DeploymentReplicas -Name "notification-worker" -Expected 0
  Wait-DeploymentReplicas -Name "dlq-replayer" -Expected 0

  $apiPod = (
    kubectl -n $Namespace get pods -l app=api `
      -o jsonpath="{.items[0].metadata.name}"
  ).Trim()
  Assert-KubectlSuccess -Operation "selecting an API pod"
  if (-not $apiPod) {
    throw "No API pod is available for the local benchmark reset."
  }

  $resetCode = @"
import json
from portfolio.api import _reset_demo_event_data
from portfolio.config import settings
from portfolio.db import get_conn, get_cursor
from portfolio.kafka_client import reset_topic
conn_context = get_conn()
conn = conn_context.__enter__()
cursor_context = get_cursor(conn)
cursor = cursor_context.__enter__()
result = _reset_demo_event_data(cursor)
conn.commit()
cursor_context.__exit__(None, None, None)
conn_context.__exit__(None, None, None)
base = {"min.insync.replicas": str(settings.kafka_min_insync_replicas)}
topics = [
    (settings.kafka_ingress_topic, base),
    (settings.kafka_dlq_topic, base),
    (settings.kafka_notification_topic, base),
]
for topic, configs in topics:
    reset_topic(
        topic,
        partitions=settings.kafka_topic_partitions,
        replication_factor=settings.kafka_topic_replication_factor,
        configs=configs,
    )
print(json.dumps({
    "deleted_events": result["deleted_messages"],
    "deleted_streams": result["reset_streams"],
    "deleted_request_statuses": result["reset_request_statuses"],
    "reset_topics": [topic for topic, _ in topics],
}))
"@
  $resetCodeBase64 = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes($resetCode)
  )
  $resetLauncher = "import base64;exec(base64.b64decode('$resetCodeBase64'))"
  kubectl -n $Namespace exec $apiPod -- python -c $resetLauncher
  if ($LASTEXITCODE -ne 0) {
    throw "Local benchmark database/topic reset failed."
  }
  Write-Host "Waiting $KafkaCleanupQuietSec seconds for delayed Kafka log deletion to finish."
  Start-Sleep -Seconds $KafkaCleanupQuietSec
  $resetSucceeded = $true
}
catch {
  $resetError = $_
}
finally {
  Invoke-CleanupKubectl "unpausing worker KEDA" @(
    "-n", $Namespace, "annotate", "scaledobject", "worker-keda",
    "autoscaling.keda.sh/paused-replicas-"
  )
  Invoke-CleanupKubectl "restoring worker replicas" @(
    "-n", $Namespace, "scale", "deployment/worker", "--replicas=2"
  )
  Invoke-CleanupKubectl "restoring auxiliary consumers" @(
    "-n", $Namespace, "scale", "deployment/notification-worker",
    "deployment/dlq-replayer", "--replicas=1"
  )
  foreach ($deployment in @("worker", "notification-worker", "dlq-replayer")) {
    Invoke-CleanupKubectl "waiting for deployment/$deployment rollout" @(
      "-n", $Namespace, "rollout", "status", "deployment/$deployment",
      "--timeout=$($TimeoutSec)s"
    )
  }
}

if ($null -ne $resetError) {
  $cleanupSuffix = if ($cleanupErrors.Count -gt 0) {
    " Cleanup failures: $($cleanupErrors -join '; ')"
  } else {
    ""
  }
  throw "$($resetError.Exception.Message)$cleanupSuffix"
}
if ($cleanupErrors.Count -gt 0) {
  throw "Local benchmark reset cleanup failed: $($cleanupErrors -join '; ')"
}
if (-not $resetSucceeded) {
  throw "Local benchmark reset did not complete."
}

Write-Host "Local benchmark data and all three active Kafka topics reset."
