param(
  [Parameter(Mandatory = $true)]
  [string]$BackupFile,
  [string]$Namespace = "messaging-app",
  [string]$ServiceName = "messaging-postgresql-ha-pgpool",
  [string]$DbName = "portfolio",
  [string]$DbUser = "portfolio",
  [switch]$ResetSchema,
  [switch]$Force
)

$ErrorActionPreference = "Stop"

if (-not $Force) {
  throw "Restore is disabled by default. Re-run with -Force after confirming the target cluster and backup file."
}

if (-not (Test-Path $BackupFile)) {
  throw "Backup file not found: $BackupFile"
}

$resolvedBackupFile = (Resolve-Path $BackupFile).Path
$backupInfo = Get-Item -LiteralPath $resolvedBackupFile
if ($backupInfo.Length -le 0) {
  throw "Backup file is empty: $resolvedBackupFile"
}
$backupDirectory = $backupInfo.DirectoryName
$backupName = $backupInfo.Name

$restorePod = "postgres-restore-client-" + ([guid]::NewGuid().ToString("N").Substring(0, 8))
$podSpec = @{
  apiVersion = "v1"
  kind = "Pod"
  metadata = @{
    name = $restorePod
    namespace = $Namespace
    labels = @{ app = "postgres-restore-client" }
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
        name = "postgres-restore-client"
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

  kubectl wait --for=condition=Ready "pod/$restorePod" -n $Namespace --timeout=180s | Out-Host

  Push-Location $backupDirectory
  try {
    kubectl cp $backupName "$Namespace/$restorePod`:/tmp/postgres-restore.sql" | Out-Host
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to copy backup into the temporary restore pod."
    }
  }
  finally {
    Pop-Location
  }

  if ($ResetSchema) {
    $resetSql = "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"
    & kubectl exec -i -n $Namespace $restorePod -- `
      psql -h $ServiceName -p 5432 -U $DbUser -d $DbName -v ON_ERROR_STOP=1 -c $resetSql | Out-Host
  }

  & kubectl exec -n $Namespace $restorePod -- `
    psql -q -h $ServiceName -p 5432 -U $DbUser -d $DbName -v ON_ERROR_STOP=1 --file=/tmp/postgres-restore.sql | Out-Host
  if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL restore failed."
  }

  Write-Host "PostgreSQL restore completed from $resolvedBackupFile"
}
finally {
  kubectl delete pod $restorePod -n $Namespace --ignore-not-found | Out-Host
}
