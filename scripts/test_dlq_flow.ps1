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

function Get-BaseReplicas([string]$Name) {
  if ($Name -eq "messaging-postgresql-ha-postgresql") { return 3 }
  if ($Name -eq "messaging-redis-node") { return 3 }
  return 1
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
$room = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/rooms" -Headers @{ Authorization = "Bearer $u1Token" } -ContentType "application/json" -Body (@{ name = "dlq-room-$suffix"; member_ids = @($u1.id, $u2.id) } | ConvertTo-Json)

try {
  $dbRef = Get-WorkloadRef $DbDeployment
  kubectl -n $Namespace scale $dbRef --replicas=0 | Out-Null
  Start-Sleep -Seconds 3

  $accept = Invoke-RestMethod -Method Post -Uri ("$BaseUrl/v1/rooms/{0}/messages" -f $room.id) -Headers @{ Authorization = "Bearer $u1Token"; "X-Idempotency-Key"="dlq-$suffix"} -ContentType "application/json" -Body (@{ body = "force dlq while db down" } | ConvertTo-Json)
  if ($accept.status -ne "accepted") { throw "Expected accepted" }

  $requestId = $accept.request_id
  $deadline = (Get-Date).AddSeconds(120)
  $finalStatus = $null
  while ((Get-Date) -lt $deadline) {
    $status = Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $u1Token" } -Uri ("$BaseUrl/v1/message-requests/{0}" -f $requestId)
    if ($status.status -in @("failed_dlq", "failed")) {
      $finalStatus = $status.status
      break
    }
    Start-Sleep -Seconds 2
  }

  if ($null -eq $finalStatus) {
    throw "Request did not reach failed_dlq/failed in time"
  }

  $dlq = Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $u1Token" } -Uri "$BaseUrl/v1/dlq/ingress?limit=200"
  Write-Host "DLQ flow test passed (k8s): request_status=$finalStatus dlq_count=$($dlq.count)"
}
finally {
  $dbRef = Get-WorkloadRef $DbDeployment
  $targetReplicas = Get-BaseReplicas $DbDeployment
  kubectl -n $Namespace scale $dbRef --replicas=$targetReplicas | Out-Null
}
}
finally {
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment -RedisDeployment $RedisDeployment
  }
}
