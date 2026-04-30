param(
  [string]$BaseUrl = "http://localhost",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [int]$FreshTimeoutSec = 90,
  [switch]$SkipReset
)

$ErrorActionPreference = "Stop"

function Get-WorkloadRef([string]$Name) {
  $sts = kubectl -n $Namespace get statefulset $Name --ignore-not-found -o name
  if ($sts) { return $sts }
  $dep = kubectl -n $Namespace get deployment $Name --ignore-not-found -o name
  if ($dep) { return $dep }
  throw "Workload not found: $Name"
}

function Wait-Ready([int]$TimeoutSec = 180) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health/ready" -TimeoutSec 5
      if ($health.status -eq "ready") { return }
    } catch {}
    Start-Sleep -Seconds 2
  }
  throw "Timed out waiting for readiness"
}

function Wait-RequestPersisted([string]$RequestId, [string]$Token, [int]$TimeoutSec = 120) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $status = Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $Token" } -Uri "$BaseUrl/v1/event-requests/$RequestId"
      if ($status.status -eq "persisted" -and $status.event_id) {
        return $status
      }
    } catch {
      Start-Sleep -Milliseconds 500
      continue
    }
    Start-Sleep -Milliseconds 500
  }
  throw "Event request did not become persisted in time"
}

function Wait-FreshCacheRead([int]$StreamId, [string]$Token, [int]$TimeoutSec = 90) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $events = Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $Token" } -Uri "$BaseUrl/v1/streams/$StreamId/events"
    if ($events.source -eq "cache" -and $events.degraded -eq $false -and $null -ne $events.snapshot_age_seconds -and @($events.items).Count -gt 0) {
      return $events
    }
    Start-Sleep -Milliseconds 500
  }
  throw "Fresh cache read was not observed in time"
}

if (-not $SkipReset) {
  & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment
}

try {
  Wait-Ready

  $suffix = "{0}-{1}" -f [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(), (Get-Random -Maximum 999999)
  $u1Name = "cacheu" + ([guid]::NewGuid().ToString("N").Substring(0, 10))
  $u2Name = "cacheu" + ([guid]::NewGuid().ToString("N").Substring(0, 10))
  $password = "Password123!"

  $u1 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body (@{ username = $u1Name; password = $password } | ConvertTo-Json)
  $u2 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body (@{ username = $u2Name; password = $password } | ConvertTo-Json)
  $u1Token = (Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/auth/login" -ContentType "application/json" -Body (@{ username = $u1Name; password = $password } | ConvertTo-Json)).access_token

  $stream = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/streams" -Headers @{ Authorization = "Bearer $u1Token" } -ContentType "application/json" -Body (@{ name = "cache-fallback-$suffix"; member_ids = @($u1.id, $u2.id) } | ConvertTo-Json)
  $accepted = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/streams/$($stream.id)/events" -Headers @{ Authorization = "Bearer $u1Token"; "X-Idempotency-Key"="cache-fallback-$suffix" } -ContentType "application/json" -Body (@{ body = "cache fallback probe" } | ConvertTo-Json)

  Wait-RequestPersisted -RequestId $accepted.request_id -Token $u1Token | Out-Null
  $fresh = Wait-FreshCacheRead -StreamId $stream.id -Token $u1Token -TimeoutSec $FreshTimeoutSec

  $freshAge = [double]$fresh.snapshot_age_seconds
  $sleepSeconds = [Math]::Max(6, [Math]::Ceiling(6 - $freshAge))
  Start-Sleep -Seconds $sleepSeconds

  $dbRef = Get-WorkloadRef $DbDeployment
  $targetReplicas = if ($DbDeployment -eq "messaging-postgresql-ha-postgresql") { 3 } else { 1 }

  try {
    kubectl -n $Namespace scale $dbRef --replicas=0 | Out-Null
    Start-Sleep -Seconds 5

    $degraded = Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $u1Token" } -Uri "$BaseUrl/v1/streams/$($stream.id)/events"
    if ($degraded.source -ne "cache" -or $degraded.degraded -ne $true -or $null -eq $degraded.snapshot_age_seconds -or @($degraded.items).Count -lt 1) {
      throw "Expected degraded cache read while DB is down, got: $($degraded | ConvertTo-Json -Compress)"
    }

    Write-Host "Cache read fallback test passed: fresh source=$($fresh.source) degraded=$($fresh.degraded) age=$($fresh.snapshot_age_seconds); db_down source=$($degraded.source) degraded=$($degraded.degraded) age=$($degraded.snapshot_age_seconds)"
  }
  finally {
    kubectl -n $Namespace scale $dbRef --replicas=$targetReplicas | Out-Null
    kubectl -n $Namespace rollout status $dbRef --timeout=180s | Out-Host
    Wait-Ready
  }
}
finally {
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment
  }
}
