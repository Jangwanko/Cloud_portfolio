param(
  [string]$BaseUrl = "http://localhost:30080",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [string]$RedisDeployment = "messaging-redis-node",
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

function Get-HealthState() {
  $request = [System.Net.WebRequest]::Create("$BaseUrl/health/ready")
  $request.Method = "GET"
  $request.Timeout = 5000

  try {
    $response = $request.GetResponse()
  } catch [System.Net.WebException] {
    $response = $_.Exception.Response
  }

  if (-not $response) {
    throw "Failed to read health response from $BaseUrl/health/ready"
  }

  $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
  $body = $reader.ReadToEnd()
  if (-not $body) {
    throw "Health endpoint returned an empty response"
  }

  return $body | ConvertFrom-Json
}

if (-not $SkipReset) {
  & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment -RedisDeployment $RedisDeployment
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

try {
Wait-Ready | Out-Null

$suffix = "{0}-{1}" -f [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(), (Get-Random -Maximum 999999)
$u1Name = "u" + ([guid]::NewGuid().ToString("N").Substring(0, 12))
$u2Name = "u" + ([guid]::NewGuid().ToString("N").Substring(0, 12))
$password = "Password123!"
$u1 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body (@{ username = $u1Name; password = $password } | ConvertTo-Json)
$u2 = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/users" -ContentType "application/json" -Body (@{ username = $u2Name; password = $password } | ConvertTo-Json)
$u1Token = (Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/auth/login" -ContentType "application/json" -Body (@{ username = $u1Name; password = $password } | ConvertTo-Json)).access_token
$room = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/rooms" -Headers @{ Authorization = "Bearer $u1Token" } -ContentType "application/json" -Body (@{ name = "redistest-room-$suffix"; member_ids = @($u1.id, $u2.id) } | ConvertTo-Json)

try {
  $redisRef = Get-WorkloadRef $RedisDeployment
  kubectl -n $Namespace scale $redisRef --replicas=0 | Out-Null
  Start-Sleep -Seconds 3

  $healthDown = $null
  $deadline = (Get-Date).AddSeconds(60)
  while ((Get-Date) -lt $deadline) {
    try {
      $candidate = Get-HealthState
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
    Invoke-RestMethod -Method Post -Uri ("$BaseUrl/v1/rooms/{0}/messages" -f $room.id) -Headers @{ Authorization = "Bearer $u1Token"; "X-Idempotency-Key"="redis-down-$suffix"} -ContentType "application/json" -Body (@{ body = "message while redis down" } | ConvertTo-Json) | Out-Null
  } catch {
    $failedAsExpected = $true
  }

  if (-not $failedAsExpected) {
    throw "Expected message API to fail while redis is down"
  }

  $targetReplicas = if ($RedisDeployment -eq "messaging-redis-node") { 3 } else { 1 }
  kubectl -n $Namespace scale $redisRef --replicas=$targetReplicas | Out-Null
  kubectl -n $Namespace rollout status $redisRef --timeout=120s | Out-Null
  Wait-Ready | Out-Null

  $accept = Invoke-RestMethod -Method Post -Uri ("$BaseUrl/v1/rooms/{0}/messages" -f $room.id) -Headers @{ Authorization = "Bearer $u1Token"; "X-Idempotency-Key"="redis-recover-$suffix"} -ContentType "application/json" -Body (@{ body = "message after redis recovery" } | ConvertTo-Json)
  if ($accept.status -ne "accepted") {
    throw "Expected accepted after redis recovery, got: $($accept | ConvertTo-Json -Compress)"
  }

  Write-Host "Redis outage test passed (k8s): API fails during redis down, recovers after redis start"
}
finally {
  $redisRef = Get-WorkloadRef $RedisDeployment
  $targetReplicas = if ($RedisDeployment -eq "messaging-redis-node") { 3 } else { 1 }
  kubectl -n $Namespace scale $redisRef --replicas=$targetReplicas | Out-Null
}
}
finally {
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment -RedisDeployment $RedisDeployment
  }
}
