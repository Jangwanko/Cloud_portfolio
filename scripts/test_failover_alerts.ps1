param(
  [string]$BaseUrl = "http://localhost",
  [string]$PrometheusUrl = "http://localhost:9090",
  [string]$Namespace = "messaging-app",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [string]$RedisDeployment = "messaging-redis-node",
  [int]$ReadyTimeoutSec = 240,
  [int]$AlertTimeoutSec = 240,
  [switch]$SkipReset,
  [switch]$KeepPortForward
)

$ErrorActionPreference = "Stop"
$prometheusPortForwardProcess = $null

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

function Get-BaseReplicas([string]$Name) {
  if ($Name -eq "messaging-postgresql-ha-postgresql") { return 3 }
  if ($Name -eq "messaging-redis-node") { return 3 }
  return 1
}

function Test-HttpOk([string]$Url) {
  try {
    Invoke-WebRequest -Method Get -Uri $Url -TimeoutSec 5 | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Wait-TcpPort([string]$TargetHost, [int]$Port, [int]$TimeoutSec = 30) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $client = New-Object System.Net.Sockets.TcpClient
      $async = $client.BeginConnect($TargetHost, $Port, $null, $null)
      if ($async.AsyncWaitHandle.WaitOne(1000, $false) -and $client.Connected) {
        $client.EndConnect($async)
        $client.Close()
        return
      }
      $client.Close()
    } catch {}
    Start-Sleep -Milliseconds 500
  }

  throw "Timed out waiting for TCP port $TargetHost`:$Port"
}

function Start-PortForward([string]$ServiceName, [int]$LocalPort, [int]$RemotePort) {
  $process = Start-Process powershell `
    -ArgumentList @(
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
      "kubectl port-forward -n $Namespace svc/$ServiceName $LocalPort`:$RemotePort"
    ) `
    -PassThru

  Wait-TcpPort -TargetHost "127.0.0.1" -Port $LocalPort -TimeoutSec 30
  return $process
}

if (-not $SkipReset) {
  & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment -RedisDeployment $RedisDeployment -TimeoutSec $ReadyTimeoutSec
}

function Wait-Ready([int]$TimeoutSec = 240) {
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

function Wait-NotReadyDb([int]$TimeoutSec = 120) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $health = Get-HealthState
      if ($health.db -eq "down") { return $true }
    } catch {}
    Start-Sleep -Seconds 2
  }
  throw "Timed out waiting for db=down health state"
}

function Wait-NotReadyRedis([int]$TimeoutSec = 120) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $health = Get-HealthState
      if ($health.status -eq "not_ready" -and $health.redis -eq "down") { return $true }
    } catch {}
    Start-Sleep -Seconds 2
  }
  throw "Timed out waiting for redis=down readiness"
}

function Test-AlertFiring([string]$AlertName) {
  try {
    $res = Invoke-RestMethod -Method Get -Uri "$PrometheusUrl/api/v1/alerts" -TimeoutSec 5
    $alerts = @($res.data.alerts)
    foreach ($a in $alerts) {
      if ($a.labels.alertname -eq $AlertName -and $a.state -eq "firing") { return $true }
    }
  } catch {}
  return $false
}

function Wait-AlertFiring([string]$AlertName, [int]$TimeoutSec = 240) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    if (Test-AlertFiring -AlertName $AlertName) { return $true }
    Start-Sleep -Seconds 5
  }
  throw "Timed out waiting for alert firing: $AlertName"
}

function Wait-AlertResolved([string]$AlertName, [int]$TimeoutSec = 240) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    if (-not (Test-AlertFiring -AlertName $AlertName)) { return $true }
    Start-Sleep -Seconds 5
  }
  throw "Timed out waiting for alert resolved: $AlertName"
}

try {
if (-not (Test-HttpOk -Url "$PrometheusUrl/-/ready")) {
  $prometheusPortForwardProcess = Start-PortForward -ServiceName "prometheus" -LocalPort 9090 -RemotePort 9090
}

Wait-Ready -TimeoutSec $ReadyTimeoutSec | Out-Null

$dbOutageStart = Get-Date
try {
  $dbRef = Get-WorkloadRef $DbDeployment
  kubectl -n $Namespace scale $dbRef --replicas=0 | Out-Null
  Wait-NotReadyDb | Out-Null
  Wait-AlertFiring -AlertName "MessagingDbDown" -TimeoutSec $AlertTimeoutSec | Out-Null
} finally {
  $dbRef = Get-WorkloadRef $DbDeployment
  $targetReplicas = Get-BaseReplicas $DbDeployment
  kubectl -n $Namespace scale $dbRef --replicas=$targetReplicas | Out-Null
}
Wait-Ready -TimeoutSec $ReadyTimeoutSec | Out-Null
Wait-AlertResolved -AlertName "MessagingDbDown" -TimeoutSec $AlertTimeoutSec | Out-Null
$dbRecoverEnd = Get-Date

$redisOutageStart = Get-Date
try {
  $redisRef = Get-WorkloadRef $RedisDeployment
  kubectl -n $Namespace scale $redisRef --replicas=0 | Out-Null
  Wait-NotReadyRedis | Out-Null
  Wait-AlertFiring -AlertName "MessagingRedisDown" -TimeoutSec $AlertTimeoutSec | Out-Null
} finally {
  $redisRef = Get-WorkloadRef $RedisDeployment
  $targetReplicas = Get-BaseReplicas $RedisDeployment
  kubectl -n $Namespace scale $redisRef --replicas=$targetReplicas | Out-Null
}
Wait-Ready -TimeoutSec $ReadyTimeoutSec | Out-Null
Wait-AlertResolved -AlertName "MessagingRedisDown" -TimeoutSec $AlertTimeoutSec | Out-Null
$redisRecoverEnd = Get-Date

$dbRecoverySec = [int](($dbRecoverEnd - $dbOutageStart).TotalSeconds)
$redisRecoverySec = [int](($redisRecoverEnd - $redisOutageStart).TotalSeconds)

Write-Host "Failover alert scenario passed (k8s)"
Write-Host ("DB outage+recovery time: {0}s" -f $dbRecoverySec)
Write-Host ("Redis outage+recovery time: {0}s" -f $redisRecoverySec)
}
finally {
  if (-not $KeepPortForward) {
    if ($prometheusPortForwardProcess -and -not $prometheusPortForwardProcess.HasExited) {
      Stop-Process -Id $prometheusPortForwardProcess.Id -Force
    }
  }
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment -RedisDeployment $RedisDeployment -TimeoutSec $ReadyTimeoutSec
  }
}
