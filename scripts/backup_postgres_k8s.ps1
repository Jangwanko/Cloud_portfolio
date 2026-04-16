param(
  [string]$Namespace = "messaging-app",
  [string]$ServiceName = "messaging-postgresql-ha-pgpool",
  [string]$DbName = "portfolio",
  [string]$DbUser = "portfolio",
  [string]$OutputDir = "backups"
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
$outputFile = Join-Path $resolvedOutputDir "postgres-$timestamp.sql"

$encoded = kubectl -n $Namespace get secret messaging-postgresql-ha-postgresql -o jsonpath='{.data.password}'
if (-not $encoded) {
  throw "Unable to read PostgreSQL password from secret messaging-postgresql-ha-postgresql."
}

$password = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))

$dumpCommand = @(
  "run", "postgres-backup-$timestamp",
  "--rm",
  "-i",
  "--attach",
  "--restart=Never",
  "-n", $Namespace,
  "--image", "bitnamilegacy/postgresql-repmgr:17.6.0-debian-12-r2",
  "--env", "PGPASSWORD=$password",
  "--command", "--",
  "pg_dump",
  "-h", $ServiceName,
  "-p", "5432",
  "-U", $DbUser,
  "-d", $DbName
)

$dump = & kubectl @dumpCommand
if ($LASTEXITCODE -ne 0 -or -not $dump) {
  throw "pg_dump failed."
}

[System.IO.File]::WriteAllText($outputFile, ($dump -join [Environment]::NewLine) + [Environment]::NewLine)
Write-Host "PostgreSQL backup written to $outputFile"
