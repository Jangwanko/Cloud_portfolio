param(
  [string]$Namespace = "messaging-app",
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

function Decode-Base64([string]$value) {
  return [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($value))
}

function Grant-PostgresMonitorRole([string]$Namespace) {
  $encodedPassword = kubectl -n $Namespace get secret messaging-postgresql-ha-postgresql -o jsonpath='{.data.postgres-password}'
  if (-not $encodedPassword) {
    Write-Warning "Unable to read postgres-password. Skipping pg_monitor grant for portfolio."
    return
  }

  $postgresPassword = Decode-Base64 $encodedPassword
  $pods = kubectl -n $Namespace get pods -l app.kubernetes.io/component=postgresql -o jsonpath='{.items[*].metadata.name}'
  foreach ($pod in ($pods -split " ")) {
    if (-not $pod) { continue }
    $isPrimary = kubectl -n $Namespace exec $pod -- bash -lc "PGPASSWORD='$postgresPassword' /opt/bitnami/postgresql/bin/psql -U postgres -d postgres -At -c 'SELECT NOT pg_is_in_recovery();'" 2>$null
    if ($LASTEXITCODE -eq 0 -and ($isPrimary | Select-Object -First 1) -eq "t") {
      kubectl -n $Namespace exec $pod -- bash -lc "PGPASSWORD='$postgresPassword' /opt/bitnami/postgresql/bin/psql -U postgres -d postgres -c 'GRANT pg_monitor TO portfolio;'" | Out-Host
      Write-Host "Granted pg_monitor to portfolio on primary pod: $pod"
      return
    }
  }

  Write-Warning "Unable to find PostgreSQL primary pod. Skipping pg_monitor grant for portfolio."
}

$repoCache = Join-Path $PSScriptRoot "..\..\tools\helm-cache\repository"
$pgHaChart = Get-ChildItem -Path $repoCache -Filter "postgresql-ha-*.tgz" -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
$redisChart = Get-ChildItem -Path $repoCache -Filter "redis-*.tgz" -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1

if (-not $pgHaChart -or -not $redisChart) {
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
$redisSource = if ($redisChart) { $redisChart.FullName } else { "bitnami/redis" }

& $helm upgrade --install messaging-postgresql-ha $pgHaSource `
  -n $Namespace `
  -f k8s/values/postgresql-ha-values.yaml `
  --wait --timeout 15m

Grant-PostgresMonitorRole -Namespace $Namespace

& $helm upgrade --install messaging-redis $redisSource `
  -n $Namespace `
  -f k8s/values/redis-ha-values.yaml `
  --wait --timeout 15m

kubectl get pods -n $Namespace
