param(
  [string]$Namespace = "messaging-app",
  [string]$ServiceName = "messaging-postgresql-ha-pgpool",
  [string]$DbName = "portfolio",
  [string]$DbUser = "portfolio",
  [string]$OutputDir = "backups",
  [ValidateRange(1, 365)]
  [int]$RetentionDays = 7
)

$ErrorActionPreference = "Stop"

function Ensure-Dir([string]$Path) {
  if (-not (Test-Path $Path)) {
    New-Item -ItemType Directory -Path $Path | Out-Null
  }
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$resolvedOutputDir = Join-Path (Get-Location) $OutputDir
Ensure-Dir -Path $resolvedOutputDir
$outputName = "postgres-$timestamp.sql"
$outputFile = Join-Path $resolvedOutputDir $outputName
$backupPod = "postgres-backup-client-" + ([guid]::NewGuid().ToString("N").Substring(0, 8))

$podSpec = @{
  apiVersion = "v1"
  kind = "Pod"
  metadata = @{
    name = $backupPod
    namespace = $Namespace
    labels = @{ app = "postgres-backup-client" }
  }
  spec = @{
    automountServiceAccountToken = $false
    restartPolicy = "Never"
    securityContext = @{
      runAsNonRoot = $true
      runAsUser = 1001
      runAsGroup = 1001
      seccompProfile = @{ type = "RuntimeDefault" }
    }
    containers = @(
      @{
        name = "postgres-backup-client"
        image = "bitnamilegacy/postgresql-repmgr:17.6.0-debian-12-r2"
        imagePullPolicy = "IfNotPresent"
        command = @("sh", "-c", "sleep 600")
        env = @(
          @{
            name = "PGPASSWORD"
            valueFrom = @{
              secretKeyRef = @{
                name = "messaging-postgresql-ha-postgresql"
                key = "password"
              }
            }
          }
        )
        securityContext = @{
          allowPrivilegeEscalation = $false
          capabilities = @{ drop = @("ALL") }
        }
      }
    )
  }
}

try {
  $podSpec | ConvertTo-Json -Depth 12 -Compress | kubectl create -f - | Out-Host
  kubectl wait --for=condition=Ready "pod/$backupPod" -n $Namespace --timeout=180s | Out-Host

  kubectl exec -n $Namespace $backupPod -- `
    pg_dump -h $ServiceName -p 5432 -U $DbUser -d $DbName --file=/tmp/postgres-backup.sql | Out-Host
  if ($LASTEXITCODE -ne 0) {
    throw "pg_dump failed."
  }

  Push-Location $resolvedOutputDir
  try {
    kubectl cp "$Namespace/$backupPod`:/tmp/postgres-backup.sql" $outputName | Out-Host
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to copy PostgreSQL backup from the temporary pod."
    }
  }
  finally {
    Pop-Location
  }

  if (-not (Test-Path -LiteralPath $outputFile) -or (Get-Item -LiteralPath $outputFile).Length -le 0) {
    throw "PostgreSQL backup file is missing or empty: $outputFile"
  }

  $retentionCutoff = (Get-Date).AddDays(-$RetentionDays)
  $expiredBackups = @(
    Get-ChildItem -LiteralPath $resolvedOutputDir -File -Filter "postgres-*.sql" |
      Where-Object { $_.LastWriteTime -lt $retentionCutoff }
  )
  if ($expiredBackups.Count -gt 0) {
    $expiredBackups | Remove-Item -Force
    Write-Host "Removed $($expiredBackups.Count) PostgreSQL backup(s) older than $RetentionDays days."
  }
  Write-Host "PostgreSQL backup written to $outputFile"
}
finally {
  kubectl delete pod $backupPod -n $Namespace --ignore-not-found | Out-Host
}
