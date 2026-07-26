param(
  [string]$Namespace = "messaging-app",
  [string]$ChartVersion = "16.3.2",
  [switch]$PrepareImages
)

$ErrorActionPreference = "Stop"

function Resolve-HelmPath {
  $cmd = Get-Command helm -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }
  $local = Join-Path $PSScriptRoot "..\..\tools\helm\windows-amd64\helm.exe"
  $localResolved = (Resolve-Path $local -ErrorAction SilentlyContinue)
  if ($localResolved) {
    return $localResolved.Path
  }
  throw "helm executable not found. Install helm or place tools/helm/windows-amd64/helm.exe"
}

$helm = Resolve-HelmPath

function Resolve-KindPath {
  $cmd = Get-Command kind -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }
  $local = Join-Path $PSScriptRoot "..\..\tools\kind.exe"
  $localResolved = (Resolve-Path $local -ErrorAction SilentlyContinue)
  if ($localResolved) {
    return $localResolved.Path
  }
  return $null
}

function Get-FirstKindCluster([string]$kindPath) {
  if (-not $kindPath) { return $null }
  $clusters = & $kindPath get clusters 2>$null
  if ($LASTEXITCODE -ne 0 -or -not $clusters) { return $null }
  return ($clusters | Select-Object -First 1)
}

function Ensure-ImagePresent([string]$image) {
  $exists = $false
  try {
    docker image inspect $image *> $null
    if ($LASTEXITCODE -eq 0) { $exists = $true }
  } catch {
    $exists = $false
  }
  if (-not $exists) {
    Write-Host "Pull image: $image"
    docker pull $image | Out-Host
  }
}

$script:PostgresAdminShell = @'
set -eu
set +x

if [ -n "${POSTGRES_POSTGRES_PASSWORD_FILE:-}" ] && [ -r "$POSTGRES_POSTGRES_PASSWORD_FILE" ]; then
  PGPASSWORD="$(cat "$POSTGRES_POSTGRES_PASSWORD_FILE")"
elif [ -r /opt/bitnami/postgresql/secrets/postgres-password ]; then
  PGPASSWORD="$(cat /opt/bitnami/postgresql/secrets/postgres-password)"
elif [ -n "${POSTGRES_POSTGRES_PASSWORD:-}" ]; then
  PGPASSWORD="$POSTGRES_POSTGRES_PASSWORD"
else
  exit 41
fi

export PGPASSWORD
SQL="$(printf '%s' "$SQL_BASE64" | base64 -d)"
unset SQL_BASE64
exec /opt/bitnami/postgresql/bin/psql \
  -X \
  --no-psqlrc \
  -v ON_ERROR_STOP=1 \
  -U postgres \
  -d postgres \
  -qAt \
  -c "$SQL"
'@
$script:PostgresAdminShell = $script:PostgresAdminShell.Replace("`r`n", "`n")
$script:PostgresAdminShellBase64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes($script:PostgresAdminShell)
)

function Invoke-PostgresAdminSql(
  [string]$Namespace,
  [string]$Pod,
  [string]$Sql,
  [switch]$AllowFailure
) {
  $encodedSql = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Sql))
  $remoteCommand = "printf '%s' '$script:PostgresAdminShellBase64' | base64 -d | bash"
  $output = kubectl `
    -n $Namespace `
    exec $Pod `
    -- `
    env "SQL_BASE64=$encodedSql" `
    bash -lc $remoteCommand 2>$null
  if ($LASTEXITCODE -ne 0) {
    if ($AllowFailure) {
      return $null
    }
    throw "PostgreSQL command failed on pod $Pod"
  }
  return (($output | Out-String).Trim())
}

function Grant-PostgresMonitorRole([string]$Namespace) {
  $pods = kubectl -n $Namespace get pods -l app.kubernetes.io/component=postgresql -o jsonpath='{.items[*].metadata.name}'
  foreach ($pod in ($pods -split " ")) {
    if (-not $pod) { continue }
    $isPrimary = Invoke-PostgresAdminSql `
      -Namespace $Namespace `
      -Pod $pod `
      -Sql "SELECT NOT pg_is_in_recovery();" `
      -AllowFailure
    if ($isPrimary -eq "t") {
      Invoke-PostgresAdminSql `
        -Namespace $Namespace `
        -Pod $pod `
        -Sql "GRANT pg_monitor TO portfolio;" | Out-Null
      Write-Host "Granted pg_monitor to portfolio on primary pod: $pod"
      return
    }
  }

  Write-Warning "Unable to find PostgreSQL primary pod. Skipping pg_monitor grant for portfolio."
}

$repoCache = Join-Path $PSScriptRoot "..\..\tools\helm-cache\repository"
$pgHaChart = Get-ChildItem -Path $repoCache -Filter "postgresql-ha-*.tgz" -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1

if (-not $pgHaChart) {
  & $helm repo add bitnami https://charts.bitnami.com/bitnami
  & $helm repo update
}

kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f -

$images = @(
  "bitnamilegacy/postgresql-repmgr:17.6.0-debian-12-r2",
  "bitnamilegacy/pgpool:4.6.3-debian-12-r0",
  "bitnamilegacy/postgres-exporter:0.17.1-debian-12-r16"
)

if ($PrepareImages) {
  $kind = Resolve-KindPath
  $clusterName = Get-FirstKindCluster $kind

  foreach ($img in $images) {
    Ensure-ImagePresent $img
    if ($clusterName -and $kind) {
      Write-Host "Load image into kind($clusterName): $img"
      try {
        & $kind load docker-image $img --name $clusterName | Out-Host
      } catch {
        Write-Warning "kind image load failed for $img. Cluster will pull directly from registry."
      }
    }
  }
}

$pgHaSource = if ($pgHaChart) { $pgHaChart.FullName } else { "bitnami/postgresql-ha" }
$pgHaVersionArgs = if ($pgHaChart) { @() } else { @("--version", $ChartVersion) }

& $helm upgrade --install messaging-postgresql-ha $pgHaSource `
  -n $Namespace `
  @pgHaVersionArgs `
  -f k8s/values/postgresql-ha-values.yaml `
  --wait --timeout 15m

& "$PSScriptRoot\..\..\scripts\configure_postgres_sync.ps1" `
  -Namespace $Namespace `
  -StatefulSet "messaging-postgresql-ha-postgresql" `
  -ExpectedReplicas 3 `
  -TimeoutSec 300

Grant-PostgresMonitorRole -Namespace $Namespace

kubectl get pods -n $Namespace
