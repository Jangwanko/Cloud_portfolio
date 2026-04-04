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
$u1 = Invoke-RestMethod -Method Post -Uri http://localhost/api/v1/users -ContentType "application/json" -Body (@{ username = "redistest_alice_$suffix" } | ConvertTo-Json)
$u2 = Invoke-RestMethod -Method Post -Uri http://localhost/api/v1/users -ContentType "application/json" -Body (@{ username = "redistest_bob_$suffix" } | ConvertTo-Json)
$room = Invoke-RestMethod -Method Post -Uri http://localhost/api/v1/rooms -ContentType "application/json" -Body (@{ name = "redistest-room-$suffix"; member_ids = @($u1.id, $u2.id) } | ConvertTo-Json)

try {
  docker compose stop redis | Out-Null
  Start-Sleep -Seconds 2

  $healthDown = $null
  $deadline = (Get-Date).AddSeconds(60)
  while ((Get-Date) -lt $deadline) {
    try {
      $candidate = Invoke-RestMethod -Method Get -Uri http://localhost/api/health/ready
      if ($candidate.status -eq "not_ready" -and $candidate.db -eq "up" -and $candidate.redis -eq "down") {
        $healthDown = $candidate
        break
      }
    } catch {}
    Start-Sleep -Seconds 2
  }

  if ($null -eq $healthDown) {
    throw "Expected readiness state db=up, redis=down within timeout"
  }

  $failedAsExpected = $false
  try {
    Invoke-RestMethod -Method Post -Uri ("http://localhost/api/v1/rooms/{0}/messages" -f $room.id) -Headers @{"X-Idempotency-Key"="redis-down-$suffix"} -ContentType "application/json" -Body (@{ user_id = $u1.id; body = "message while redis down" } | ConvertTo-Json) | Out-Null
  } catch {
    $failedAsExpected = $true
  }

  if (-not $failedAsExpected) {
    throw "Expected message API to fail while redis is down"
  }

  docker compose start redis | Out-Null
  Wait-Ready

  $accept = Invoke-RestMethod -Method Post -Uri ("http://localhost/api/v1/rooms/{0}/messages" -f $room.id) -Headers @{"X-Idempotency-Key"="redis-recover-$suffix"} -ContentType "application/json" -Body (@{ user_id = $u1.id; body = "message after redis recovery" } | ConvertTo-Json)
  if ($accept.status -ne "accepted") {
    throw "Expected accepted after redis recovery, got: $($accept | ConvertTo-Json -Compress)"
  }

  Write-Host "Redis outage test passed: API fails during redis down, recovers after redis start"
}
finally {
  docker compose start redis | Out-Null
}
