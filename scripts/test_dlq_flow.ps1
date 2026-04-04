$ErrorActionPreference = "Stop"

function Wait-Ready([int]$TimeoutSec = 180) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $health = Invoke-RestMethod -Method Get -Uri http://localhost/api/health/ready -TimeoutSec 5
      if ($health.status -eq "ready") { return $true }
    } catch {}
    Start-Sleep -Seconds 2
  }
  throw "Timed out waiting for readiness"
}

Wait-Ready

$suffix = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$u1 = Invoke-RestMethod -Method Post -Uri http://localhost/api/v1/users -ContentType "application/json" -Body (@{ username = "dlq_alice_$suffix" } | ConvertTo-Json)
$u2 = Invoke-RestMethod -Method Post -Uri http://localhost/api/v1/users -ContentType "application/json" -Body (@{ username = "dlq_bob_$suffix" } | ConvertTo-Json)
$room = Invoke-RestMethod -Method Post -Uri http://localhost/api/v1/rooms -ContentType "application/json" -Body (@{ name = "dlq-room-$suffix"; member_ids = @($u1.id, $u2.id) } | ConvertTo-Json)

try {
  docker compose stop db | Out-Null
  Start-Sleep -Seconds 2

  $accept = Invoke-RestMethod -Method Post -Uri ("http://localhost/api/v1/rooms/{0}/messages" -f $room.id) -Headers @{"X-Idempotency-Key"="dlq-$suffix"} -ContentType "application/json" -Body (@{ user_id = $u1.id; body = "force dlq while db down" } | ConvertTo-Json)
  if ($accept.status -ne "accepted") {
    throw "Expected accepted status"
  }

  $requestId = $accept.request_id
  if (-not $requestId) { throw "request_id missing" }

  $failedDlq = $false
  $deadline = (Get-Date).AddSeconds(180)
  while ((Get-Date) -lt $deadline) {
    try {
      $status = Invoke-RestMethod -Method Get -Uri ("http://localhost/api/v1/message-requests/{0}" -f $requestId)
      if ($status.status -eq "failed_dlq") {
        $failedDlq = $true
        break
      }
    } catch {}
    Start-Sleep -Seconds 2
  }

  if (-not $failedDlq) {
    throw "Request did not move to failed_dlq in time"
  }

  $dlq = Invoke-RestMethod -Method Get -Uri "http://localhost/api/v1/dlq/ingress?limit=200"
  $found = $false
  foreach ($item in $dlq.items) {
    if ($item.request_id -eq $requestId) {
      $found = $true
      break
    }
  }

  if (-not $found) {
    throw "DLQ does not contain expected request_id=$requestId"
  }

  Write-Host "DLQ test passed: request moved to failed_dlq and exists in ingress DLQ"
}
finally {
  docker compose start db | Out-Null
}
