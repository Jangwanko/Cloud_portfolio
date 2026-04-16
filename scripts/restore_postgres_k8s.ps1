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

$encoded = kubectl -n $Namespace get secret messaging-postgresql-ha-postgresql -o jsonpath='{.data.password}'
if (-not $encoded) {
  throw "Unable to read PostgreSQL password from secret messaging-postgresql-ha-postgresql."
}

$password = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))
$resolvedBackupFile = (Resolve-Path $BackupFile).Path
$backupContent = Get-Content -LiteralPath $resolvedBackupFile -Raw

if (-not $backupContent) {
  throw "Backup file is empty: $resolvedBackupFile"
}

$restorePod = "postgres-restore-" + (Get-Date -Format "yyyyMMddHHmmss")

try {
  & kubectl run $restorePod `
    -n $Namespace `
    --image "bitnamilegacy/postgresql-repmgr:17.6.0-debian-12-r2" `
    --restart=Never `
    --env "PGPASSWORD=$password" `
    --command -- sleep 600 | Out-Host

  kubectl wait --for=condition=Ready "pod/$restorePod" -n $Namespace --timeout=180s | Out-Host

  if ($ResetSchema) {
    $resetSql = "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"
    & kubectl exec -i -n $Namespace $restorePod -- `
      psql -h $ServiceName -p 5432 -U $DbUser -d $DbName -v ON_ERROR_STOP=1 -c $resetSql | Out-Host
  }

  $backupContent | & kubectl exec -i -n $Namespace $restorePod -- `
    psql -h $ServiceName -p 5432 -U $DbUser -d $DbName -v ON_ERROR_STOP=1 | Out-Host

  Write-Host "PostgreSQL restore completed from $resolvedBackupFile"
}
finally {
  kubectl delete pod $restorePod -n $Namespace --ignore-not-found | Out-Host
}
