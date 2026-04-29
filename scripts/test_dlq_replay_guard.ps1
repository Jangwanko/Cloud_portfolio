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

function Publish-MaxReplayPoisonEvent([string]$RequestId, [int]$StreamId, [int]$UserId) {
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
    "body": "poison event for replay guard verification",
    "room_seq": 999,
    "x_idempotency_key": None,
    "queued_at": "1970-01-01T00:00:00+00:00",
    "retry_count": settings.ingress_max_retries,
    "replay_count": settings.dlq_replay_max_count,
    "next_retry_at": None,
}
publish_ingress_job(payload["room_id"], payload)
'@
  $encodedCode = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($code))
  kubectl -n $Namespace exec deploy/api -- env "REQUEST_ID=$RequestId" "STREAM_ID=$StreamId" "USER_ID=$UserId" python -c "import base64; exec(base64.b64decode('$encodedCode'))" | Out-Null
}

try {
  Wait-Ready | Out-Null

  $suffix = "{0}-{1}" -f [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(), (Get-Random -Maximum 999999)
  $u1Name = "guard_" + ([guid]::NewGuid().ToString("N").Substring(0, 10))
  $password = "Password123!"
  $u1 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body (@{ username = $u1Name; password = $password } | ConvertTo-Json)
  $u1Token = (Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/auth/login" -ContentType "application/json" -Body (@{ username = $u1Name; password = $password } | ConvertTo-Json)).access_token
  $headers = @{ Authorization = "Bearer $u1Token" }
  $stream = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/streams" -Headers $headers -ContentType "application/json" -Body (@{ name = "dlq-guard-stream-$suffix"; member_ids = @($u1.id) } | ConvertTo-Json)
  $requestId = "dlq-guard-$suffix"

  Publish-MaxReplayPoisonEvent -RequestId $requestId -StreamId $stream.id -UserId $u1.id

  $deadline = (Get-Date).AddSeconds(90)
  $found = $null
  while ((Get-Date) -lt $deadline) {
    $dlq = Invoke-RestMethod -Method Get -Headers $headers -Uri "$BaseUrl/v1/dlq/ingress?limit=200"
    $found = @($dlq.items | Where-Object { $_.request_id -eq $requestId } | Select-Object -First 1)
    if ($found) { break }
    Start-Sleep -Seconds 2
  }

  if (-not $found) {
    throw "Max replay poison event did not reach Kafka DLQ in time"
  }

  if ($found.replayable -ne $false) {
    throw "Expected replayable=false for request_id=$requestId"
  }
  if ([int]$found.replay_count -lt [int]$found.max_replay_count) {
    throw "Expected replay_count >= max_replay_count for request_id=$requestId"
  }

  Start-Sleep -Seconds 5
  $eventsResponse = Invoke-RestMethod -Method Get -Headers $headers -Uri "$BaseUrl/v1/streams/$($stream.id)/events?limit=20"
  $events = @($eventsResponse.items)
  $persistedPoison = @($events | Where-Object { $_.body -eq "poison event for replay guard verification" }).Count
  if ($persistedPoison -ne 0) {
    throw "Replay guard failed: max replay poison event was persisted"
  }

  Write-Host "DLQ replay guard test passed: request_id=$requestId replay_count=$($found.replay_count) max_replay_count=$($found.max_replay_count)"
}
finally {
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment
  }
}
