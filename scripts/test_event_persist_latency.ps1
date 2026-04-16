param(
  [string]$BaseUrl = "http://localhost",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [string]$RedisDeployment = "messaging-redis-node",
  [int]$EventCount = 20,
  [int]$PersistTimeoutSec = 90,
  [switch]$SkipReset
)

$ErrorActionPreference = "Stop"

function Get-MetricLines([string]$MetricName) {
  try {
    $raw = Invoke-WebRequest -Method Get -Uri "$BaseUrl/metrics" -TimeoutSec 10
    return ($raw.Content -split "`n" | Where-Object { $_ -match "^$MetricName" })
  } catch {
    return @()
  }
}

function Get-QueueDepthSnapshot() {
  $lines = Get-MetricLines -MetricName "messaging_queue_depth"
  $result = @{}
  foreach ($line in $lines) {
    if ($line -match 'queue="([^"]+)".*}\s+([0-9\.]+)$') {
      $result[$matches[1]] = [double]$matches[2]
    }
  }
  return $result
}

function Add-StatSample([System.Collections.Generic.List[double]]$List, [double]$Value) {
  [void]$List.Add([math]::Round($Value, 2))
}

function Get-Stats([System.Collections.Generic.List[double]]$Values) {
  if ($Values.Count -eq 0) {
    return [ordered]@{
      count = 0
      avg_ms = 0
      p95_ms = 0
      max_ms = 0
    }
  }

  $sorted = $Values | Sort-Object
  $avg = ($Values | Measure-Object -Average).Average
  $index = [math]::Ceiling($sorted.Count * 0.95) - 1
  if ($index -lt 0) { $index = 0 }
  if ($index -ge $sorted.Count) { $index = $sorted.Count - 1 }

  return [ordered]@{
    count = $sorted.Count
    avg_ms = [math]::Round($avg, 2)
    p95_ms = [math]::Round([double]$sorted[$index], 2)
    max_ms = [math]::Round([double]$sorted[-1], 2)
  }
}

if (-not $SkipReset) {
  & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment -RedisDeployment $RedisDeployment
}

try {
  $suffix = "{0}-{1}" -f [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(), (Get-Random -Maximum 999999)
  $u1Name = "u" + ([guid]::NewGuid().ToString("N").Substring(0, 12))
  $u2Name = "u" + ([guid]::NewGuid().ToString("N").Substring(0, 12))
  $password = "Password123!"

  $u1 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body (@{ username = $u1Name; password = $password } | ConvertTo-Json)
  $u2 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body (@{ username = $u2Name; password = $password } | ConvertTo-Json)
  $u1Token = (Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/auth/login" -ContentType "application/json" -Body (@{ username = $u1Name; password = $password } | ConvertTo-Json)).access_token

  $stream = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/streams" -Headers @{ Authorization = "Bearer $u1Token" } -ContentType "application/json" -Body (@{ name = "latency-stream-$suffix"; member_ids = @($u1.id, $u2.id) } | ConvertTo-Json)

  $acceptLatencies = [System.Collections.Generic.List[double]]::new()
  $persistLatencies = [System.Collections.Generic.List[double]]::new()
  $pollCounts = [System.Collections.Generic.List[double]]::new()
  $queueBefore = Get-QueueDepthSnapshot

  for ($i = 1; $i -le $EventCount; $i++) {
    $body = @{ body = "latency event $i" } | ConvertTo-Json
    $idempotencyKey = "latency-$suffix-$i"

    $acceptWatch = [System.Diagnostics.Stopwatch]::StartNew()
    $accepted = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/streams/$($stream.id)/events" -Headers @{ Authorization = "Bearer $u1Token"; "X-Idempotency-Key" = $idempotencyKey } -ContentType "application/json" -Body $body
    $acceptWatch.Stop()
    Add-StatSample -List $acceptLatencies -Value $acceptWatch.Elapsed.TotalMilliseconds

    $queuedAt = [DateTimeOffset]::Parse($accepted.queued_at)
    $deadline = (Get-Date).AddSeconds($PersistTimeoutSec)
    $pollCount = 0
    $persisted = $null
    while ((Get-Date) -lt $deadline) {
      $pollCount += 1
      $status = Invoke-RestMethod -Method Get -Uri "$BaseUrl/v1/event-requests/$($accepted.request_id)" -Headers @{ Authorization = "Bearer $u1Token" }
      if ($status.status -eq "persisted" -and $status.created_at) {
        $persisted = $status
        break
      }
      Start-Sleep -Milliseconds 200
    }

    if ($null -eq $persisted) {
      throw "Event request did not become persisted in time for request_id=$($accepted.request_id)"
    }

    $persistedAt = [DateTimeOffset]::Parse($persisted.created_at)
    Add-StatSample -List $persistLatencies -Value ($persistedAt - $queuedAt).TotalMilliseconds
    Add-StatSample -List $pollCounts -Value $pollCount
  }

  $queueAfter = Get-QueueDepthSnapshot

  $acceptStats = Get-Stats -Values $acceptLatencies
  $persistStats = Get-Stats -Values $persistLatencies
  $pollStats = Get-Stats -Values $pollCounts

  $result = [ordered]@{
    event_count = $EventCount
    stream_id = $stream.id
    accept_latency = $acceptStats
    persist_latency = $persistStats
    status_poll_count = $pollStats
    queue_depth_before = $queueBefore
    queue_depth_after = $queueAfter
  }

  $result | ConvertTo-Json -Depth 5
}
finally {
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment -RedisDeployment $RedisDeployment
  }
}
