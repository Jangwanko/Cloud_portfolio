param(
  [string]$BaseUrl = "http://localhost",
  [string]$Namespace = "messaging-app",
  [string]$ApiDeployment = "api",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
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

function Invoke-KubectlQuiet([scriptblock]$Action) {
  $oldPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    & $Action
  } finally {
    $ErrorActionPreference = $oldPreference
  }
}

function Wait-DbQueryReady([int]$TimeoutSec = 180, [int]$RequiredSuccesses = 3) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  $successCount = 0
  while ((Get-Date) -lt $deadline) {
    Invoke-KubectlQuiet {
      kubectl -n $Namespace exec deploy/$ApiDeployment -- python -c "from portfolio.db import get_conn; conn = get_conn().__enter__(); cur = conn.cursor(); cur.execute('SELECT 1'); cur.fetchone(); conn.close()" 2>$null | Out-Null
    }
    if ($LASTEXITCODE -eq 0) {
      $successCount += 1
      if ($successCount -ge $RequiredSuccesses) {
        return
      }
      Start-Sleep -Seconds 2
      continue
    }
    $successCount = 0
    Start-Sleep -Seconds 3
  }
  throw "Timed out waiting for pgpool-backed DB query readiness"
}

function Warm-ApiMembershipCache([int]$StreamId, [string]$Token, [string]$Suffix) {
  $code = @'
import json
import os
import urllib.request

stream_id = os.environ["STREAM_ID"]
token = os.environ["TOKEN"]
suffix = os.environ["SUFFIX"]
payload = json.dumps({"body": f"cache warmup {suffix}"}).encode("utf-8")
request = urllib.request.Request(
    f"http://127.0.0.1:8000/v1/streams/{stream_id}/events",
    data=payload,
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    },
    method="POST",
)
with urllib.request.urlopen(request, timeout=10) as response:
    if response.status >= 300:
        raise SystemExit(response.status)
'@
  $encodedCode = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($code))
  $podsRaw = kubectl -n $Namespace get pods -l app=$ApiDeployment -o jsonpath='{.items[*].metadata.name}'
  $pods = @($podsRaw -split '\s+' | Where-Object { $_ })
  foreach ($pod in $pods) {
    kubectl -n $Namespace exec $pod -- env "TOKEN=$Token" "STREAM_ID=$StreamId" "SUFFIX=$Suffix" python -c "import base64; exec(base64.b64decode('$encodedCode'))" | Out-Null
  }
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
$stream = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/streams" -Headers @{ Authorization = "Bearer $u1Token" } -ContentType "application/json" -Body (@{ name = "dbtest-stream-$suffix"; member_ids = @($u1.id, $u2.id) } | ConvertTo-Json)
Warm-ApiMembershipCache -StreamId $stream.id -Token $u1Token -Suffix $suffix

try {
  $dbRef = Get-WorkloadRef $DbDeployment
  kubectl -n $Namespace scale $dbRef --replicas=0 | Out-Null
  Start-Sleep -Seconds 4

  $healthDown = Get-HealthState
  $reasons = @($healthDown.reason)
  if (
    $healthDown.status -ne "degraded" `
      -or $reasons -notcontains "postgres_primary_unreachable" `
      -or $healthDown.postgres.primary_reachable -ne $false
  ) {
    throw "Expected db down readiness state, got: $($healthDown | ConvertTo-Json -Compress)"
  }

  $accept = Invoke-RestMethod -Method Post -Uri ("$BaseUrl/v1/streams/{0}/events" -f $stream.id) -Headers @{ Authorization = "Bearer $u1Token" } -ContentType "application/json" -Body (@{ body = "event while db down" } | ConvertTo-Json)
  if ($accept.status -ne "accepted") {
    throw "Expected accepted while db down, got: $($accept | ConvertTo-Json -Compress)"
  }

  $requestId = $accept.request_id
  if (-not $requestId) { throw "request_id missing" }

  $targetReplicas = if ($DbDeployment -eq "messaging-postgresql-ha-postgresql") { 3 } else { 1 }
  kubectl -n $Namespace scale $dbRef --replicas=$targetReplicas | Out-Null
  kubectl -n $Namespace rollout status $dbRef --timeout=120s | Out-Null
  Wait-DbQueryReady -TimeoutSec 180
  Invoke-KubectlQuiet {
    kubectl -n $Namespace exec deploy/$ApiDeployment -- python -c "from portfolio.db import run_alembic_migrations; run_alembic_migrations()" 2>$null | Out-Null
  }
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to run schema migrations after DB recovery"
  }
  Wait-Ready | Out-Null

  $persisted = $false
  $deadline = (Get-Date).AddSeconds(300)
  while ((Get-Date) -lt $deadline) {
    $status = Invoke-RestMethod -Method Get -Headers @{ Authorization = "Bearer $u1Token" } -Uri ("$BaseUrl/v1/event-requests/{0}" -f $requestId)
    if ($status.status -eq "persisted") {
      $persisted = $true
      break
    }
    Start-Sleep -Seconds 2
  }

  if (-not $persisted) {
    throw "Event request did not become persisted in time"
  }

  Write-Host "DB outage test passed (k8s): accepted during DB down and persisted after recovery"
}
finally {
  $dbRef = Get-WorkloadRef $DbDeployment
  $targetReplicas = if ($DbDeployment -eq "messaging-postgresql-ha-postgresql") { 3 } else { 1 }
  kubectl -n $Namespace scale $dbRef --replicas=$targetReplicas | Out-Null
}
}
finally {
  if (-not $SkipReset) {
    & "$PSScriptRoot/reset_k8s_state.ps1" -BaseUrl $BaseUrl -Namespace $Namespace -DbDeployment $DbDeployment
  }
}
