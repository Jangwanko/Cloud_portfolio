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
$u1 = Invoke-RestMethod -Method Post -Uri http://localhost/api/v1/users -ContentType "application/json" -Body (@{ username = "dbtest_alice_$suffix" } | ConvertTo-Json)
$u2 = Invoke-RestMethod -Method Post -Uri http://localhost/api/v1/users -ContentType "application/json" -Body (@{ username = "dbtest_bob_$suffix" } | ConvertTo-Json)
$room = Invoke-RestMethod -Method Post -Uri http://localhost/api/v1/rooms -ContentType "application/json" -Body (@{ name = "dbtest-room-$suffix"; member_ids = @($u1.id, $u2.id) } | ConvertTo-Json)

try {
  docker compose stop db | Out-Null
  Start-Sleep -Seconds 3

  $healthDown = Invoke-RestMethod -Method Get -Uri http://localhost/api/health/ready
  if ($healthDown.status -ne "not_ready" -or $healthDown.db -ne "down") {
    throw "Expected db down readiness state, got: $($healthDown | ConvertTo-Json -Compress)"
  }

  $accept = Invoke-RestMethod -Method Post -Uri ("http://localhost/api/v1/rooms/{0}/messages" -f $room.id) -Headers @{"X-Idempotency-Key"="db-down-$suffix"} -ContentType "application/json" -Body (@{ user_id = $u1.id; body = "message while db down" } | ConvertTo-Json)
  if ($accept.status -ne "accepted") {
    throw "Expected accepted while db down, got: $($accept | ConvertTo-Json -Compress)"
  }

  $requestId = $accept.request_id
  if (-not $requestId) { throw "request_id missing" }

  docker compose start db | Out-Null
  Wait-Ready

  $persisted = $false
  $deadline = (Get-Date).AddSeconds(120)
  while ((Get-Date) -lt $deadline) {
    $status = Invoke-RestMethod -Method Get -Uri ("http://localhost/api/v1/message-requests/{0}" -f $requestId)
    if ($status.status -eq "persisted") {
      $persisted = $true
      break
    }
    Start-Sleep -Seconds 2
  }

  if (-not $persisted) {
    throw "Message request did not become persisted in time"
  }

  Write-Host "DB outage test passed: accepted during DB down and persisted after recovery"
}
finally {
  docker compose start db | Out-Null
}
