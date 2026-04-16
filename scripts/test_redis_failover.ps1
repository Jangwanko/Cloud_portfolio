param(
  [string]$BaseUrl = "http://localhost",
  [string]$Namespace = "messaging-app",
  [string]$RedisPod = "messaging-redis-node-0",
  [string]$RedisStatefulSet = "messaging-redis-node",
  [switch]$SkipReset
)

$ErrorActionPreference = "Stop"

if (-not $SkipReset) {
  & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment "messaging-postgresql-ha-postgresql" -RedisDeployment $RedisStatefulSet
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

function Wait-PodReady([string]$Name, [int]$TimeoutSec = 180) {
  kubectl wait --for=condition=Ready "pod/$Name" -n $Namespace --timeout="${TimeoutSec}s" | Out-Null
}

function Wait-EventAccepted([string]$Uri, [hashtable]$Headers, [string]$Body, [int]$TimeoutSec = 90) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $response = Invoke-RestMethod -Method Post -Uri $Uri -Headers $Headers -ContentType "application/json" -Body $Body
      if ($response.status -eq "accepted") {
        return $response
      }
    } catch {}
    Start-Sleep -Seconds 2
  }

  throw "Timed out waiting for event intake to recover after Redis failover"
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
  $stream = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/streams" -Headers @{ Authorization = "Bearer $u1Token" } -ContentType "application/json" -Body (@{ name = "redisfailover-stream-$suffix"; member_ids = @($u1.id, $u2.id) } | ConvertTo-Json)
  $eventUri = "$BaseUrl/v1/streams/$($stream.id)/events"
  $eventHeaders = @{ Authorization = "Bearer $u1Token"; "X-Idempotency-Key"="redis-failover-$suffix" }
  $eventBody = @{ body = "event after redis failover" } | ConvertTo-Json

  kubectl delete pod $RedisPod -n $Namespace --wait=false | Out-Null
  Wait-PodReady -Name $RedisPod -TimeoutSec 240
  Wait-Ready | Out-Null

  $accept = Wait-EventAccepted -Uri $eventUri -Headers $eventHeaders -Body $eventBody -TimeoutSec 90

  Write-Host "Redis failover test passed (k8s): single Redis pod restart recovered and event intake stayed available"
}
finally {
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment "messaging-postgresql-ha-postgresql" -RedisDeployment $RedisStatefulSet
  }
}
