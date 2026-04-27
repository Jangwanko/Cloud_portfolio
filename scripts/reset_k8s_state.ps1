param(
  [string]$BaseUrl = "http://localhost",
  [string]$Namespace = "messaging-app",
  [string]$ApiDeployment = "api",
  [string]$DbDeployment = "messaging-postgresql-ha-postgresql",
  [int]$TimeoutSec = 240
)

$ErrorActionPreference = "Stop"

function Get-WorkloadRef([string]$Name) {
  kubectl -n $Namespace get statefulset $Name --ignore-not-found | Out-Null
  if ($LASTEXITCODE -eq 0 -and (kubectl -n $Namespace get statefulset $Name --ignore-not-found -o name)) {
    return "statefulset/$Name"
  }
  kubectl -n $Namespace get deployment $Name --ignore-not-found | Out-Null
  if ($LASTEXITCODE -eq 0 -and (kubectl -n $Namespace get deployment $Name --ignore-not-found -o name)) {
    return "deployment/$Name"
  }
  throw "Workload not found: $Name"
}

function Scale-Workload([string]$Name, [int]$Replicas) {
  $ref = Get-WorkloadRef $Name
  kubectl -n $Namespace scale $ref --replicas=$Replicas | Out-Null
}

function Wait-Workload([string]$Name, [int]$TimeoutSec) {
  $ref = Get-WorkloadRef $Name
  kubectl -n $Namespace rollout status $ref --timeout="$($TimeoutSec)s" | Out-Null
}

function Get-BaseReplicas([string]$Name) {
  if ($Name -eq "messaging-postgresql-ha-postgresql") { return 3 }
  return 1
}

function Wait-Ready([int]$WaitSec = 240) {
  $deadline = (Get-Date).AddSeconds($WaitSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health/ready" -TimeoutSec 5
      if ($health.status -eq "ready") { return $true }
    } catch {}
    Start-Sleep -Seconds 2
  }
  throw "Timed out waiting for readiness at $BaseUrl"
}

function Wait-DbQueryReady([int]$WaitSec = 240, [int]$RequiredSuccesses = 3) {
  $deadline = (Get-Date).AddSeconds($WaitSec)
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

  throw "Timed out waiting for pgpool-backed DB query readiness from deploy/$ApiDeployment"
}

function Invoke-MigrationsWithRetry([int]$TimeoutSec = 240) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    Invoke-KubectlQuiet {
      kubectl -n $Namespace rollout status deploy/$ApiDeployment --timeout=30s 2>$null | Out-Null
    }
    if ($LASTEXITCODE -eq 0) {
      try {
        Wait-DbQueryReady -WaitSec 30
      } catch {}
      Invoke-KubectlQuiet {
        kubectl -n $Namespace exec deploy/$ApiDeployment -- python -c "from portfolio.db import run_alembic_migrations; run_alembic_migrations()" 2>$null | Out-Null
      }
      if ($LASTEXITCODE -eq 0) {
        return
      }
    }
    Start-Sleep -Seconds 3
  }

  throw "Failed to run schema migrations from deploy/$ApiDeployment"
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

Scale-Workload -Name $DbDeployment -Replicas (Get-BaseReplicas $DbDeployment)
Wait-Workload -Name $DbDeployment -TimeoutSec $TimeoutSec

# Ensure schema exists even when DB pods have been recreated.
Wait-DbQueryReady -WaitSec $TimeoutSec
Invoke-MigrationsWithRetry -TimeoutSec $TimeoutSec

Wait-Ready -WaitSec $TimeoutSec | Out-Null
Write-Host "reset_k8s_state completed"
