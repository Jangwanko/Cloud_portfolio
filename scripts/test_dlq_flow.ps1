param(
  [string]$BaseUrl = "http://localhost",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [switch]$SkipReset
)

$ErrorActionPreference = "Stop"

if (-not $SkipReset) {
  & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment
}

function Wait-Ready([int]$TimeoutSec = 180) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health/ready" -TimeoutSec 5
      if ($health.status -eq "ready") { return $true }
    } catch {}
    Start-Sleep -Seconds 2
  }
  throw "Timed out waiting for readiness"
}

function Publish-PoisonEvent([string]$RequestId, [int]$StreamId, [int]$UserId) {
  $code = @'
import os
from portfolio.config import settings
from portfolio.kafka_client import publish_ingress_job

request_id = os.environ["REQUEST_ID"]
stream_id = int(os.environ["STREAM_ID"])
user_id = int(os.environ["USER_ID"])
payload = {
    "request_id": request_id,
    "route": f"POST:/v1/streams/{stream_id}/events",
    "room_id": stream_id,
    "user_id": user_id,
    "body": "poison event for dlq verification",
    "room_seq": 999,
    "x_idempotency_key": None,
    "queued_at": "1970-01-01T00:00:00+00:00",
    "retry_count": settings.ingress_max_retries,
    "next_retry_at": None,
}
publish_ingress_job(payload["room_id"], payload)
'@
  $encodedCode = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($code))
  kubectl -n $Namespace exec deploy/api -- env "REQUEST_ID=$RequestId" "STREAM_ID=$StreamId" "USER_ID=$UserId" python -c "import base64; exec(base64.b64decode('$encodedCode'))" | Out-Null
}

try {
Wait-Ready | Out-Null
kubectl -n $Namespace scale deployment/dlq-replayer --replicas=0 | Out-Null
kubectl -n $Namespace rollout status deployment/dlq-replayer --timeout=120s | Out-Null

$suffix = "{0}-{1}" -f [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(), (Get-Random -Maximum 999999)
$u1Name = "u" + ([guid]::NewGuid().ToString("N").Substring(0, 12))
$password = "Password123!"
$u1 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body (@{ username = $u1Name; password = $password } | ConvertTo-Json)
$u1Token = (Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/auth/login" -ContentType "application/json" -Body (@{ username = $u1Name; password = $password } | ConvertTo-Json)).access_token
$stream = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/streams" -Headers @{ Authorization = "Bearer $u1Token" } -ContentType "application/json" -Body (@{ name = "dlq-stream-$suffix"; member_ids = @($u1.id) } | ConvertTo-Json)
$requestId = "dlq-$suffix"
Publish-PoisonEvent -RequestId $requestId -StreamId $stream.id -UserId $u1.id

  $deadline = (Get-Date).AddSeconds(90)
  $found = $false
  while ((Get-Date) -lt $deadline) {
    $dlq = Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $u1Token" } -Uri "$BaseUrl/v1/dlq/ingress?limit=200"
    foreach ($item in $dlq.items) {
      if ($item.value.request_id -eq $requestId) {
        $found = $true
        break
      }
    }
    if ($found) { break }
    Start-Sleep -Seconds 2
  }

  if (-not $found) {
    throw "Poison event did not reach Kafka DLQ in time"
  }

  $dlq = Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $u1Token" } -Uri "$BaseUrl/v1/dlq/ingress?limit=200"
  Write-Host "DLQ flow test passed (k8s): request_id=$requestId dlq_count=$($dlq.count)"
}
finally {
  kubectl -n $Namespace scale deployment/dlq-replayer --replicas=1 | Out-Null
  kubectl -n $Namespace rollout status deployment/dlq-replayer --timeout=120s | Out-Null
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment
  }
}
