[CmdletBinding()]
param(
  [string]$Namespace = "messaging-app",
  [string]$StatefulSet = "messaging-postgresql-ha-postgresql",
  [ValidateRange(0, 100)]
  [int]$ExpectedReplicas = 0,
  [ValidateRange(1, 3600)]
  [int]$TimeoutSec = 240,
  [ValidateRange(1, 60)]
  [int]$PollIntervalSec = 3
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($Namespace -notmatch '^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$') {
  throw "Namespace must be a valid DNS label"
}
if ($StatefulSet -notmatch '^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$') {
  throw "StatefulSet must be a valid DNS label"
}

$script:PostgresShell = @'
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
$script:PostgresShell = $script:PostgresShell.Replace("`r`n", "`n")
$script:PostgresShellBase64 = [Convert]::ToBase64String(
  [Text.Encoding]::UTF8.GetBytes($script:PostgresShell)
)

function Invoke-KubectlCapture([string[]]$Arguments) {
  $output = & kubectl @Arguments 2>$null
  if ($LASTEXITCODE -ne 0) {
    return $null
  }
  return (($output | Out-String).Trim())
}

function Invoke-PostgresSql(
  [string]$Pod,
  [string]$Sql,
  [switch]$AllowFailure
) {
  $encodedSql = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Sql))
  $remoteCommand = "printf '%s' '$script:PostgresShellBase64' | base64 -d | bash"
  $output = & kubectl `
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

function Get-StatefulSetReplicaCount() {
  if ($ExpectedReplicas -gt 0) {
    return $ExpectedReplicas
  }

  $raw = Invoke-KubectlCapture -Arguments @(
    "-n", $Namespace,
    "get", "statefulset/$StatefulSet",
    "-o", "jsonpath={.spec.replicas}"
  )
  $count = 0
  if (-not $raw -or -not [int]::TryParse($raw, [ref]$count)) {
    throw "Unable to determine replica count for statefulset/$StatefulSet"
  }
  return $count
}

function Wait-StatefulSetReadyReplicas([int]$ReplicaCount, [DateTime]$Deadline) {
  $lastReady = 0
  while ((Get-Date) -lt $Deadline) {
    $raw = Invoke-KubectlCapture -Arguments @(
      "-n", $Namespace,
      "get", "statefulset/$StatefulSet",
      "-o", "jsonpath={.status.readyReplicas}"
    )
    $ready = 0
    if ($raw) {
      [void][int]::TryParse($raw, [ref]$ready)
    }
    $lastReady = $ready
    if ($ready -ge $ReplicaCount) {
      return
    }
    Start-Sleep -Seconds $PollIntervalSec
  }

  throw "Timed out waiting for statefulset/$StatefulSet ready replicas: $lastReady/$ReplicaCount"
}

function Find-PostgresPrimary([string[]]$Pods) {
  foreach ($pod in $Pods) {
    $role = Invoke-PostgresSql `
      -Pod $pod `
      -Sql "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby' ELSE 'primary' END;" `
      -AllowFailure
    if ($role -eq "primary") {
      return $pod
    }
  }
  return $null
}

$replicaCount = Get-StatefulSetReplicaCount
if ($replicaCount -lt 2) {
  throw "Synchronous replication requires at least two StatefulSet replicas"
}

$deadline = (Get-Date).AddSeconds($TimeoutSec)
Wait-StatefulSetReadyReplicas -ReplicaCount $replicaCount -Deadline $deadline

$expectedPods = @(
  for ($index = 0; $index -lt $replicaCount; $index++) {
    "$StatefulSet-$index"
  }
)
$synchronousStandbyNames = "ANY 1 (" + (($expectedPods | ForEach-Object { '"' + $_ + '"' }) -join ", ") + ")"
$escapedStandbyNames = $synchronousStandbyNames.Replace("'", "''")

foreach ($pod in $expectedPods) {
  Invoke-PostgresSql `
    -Pod $pod `
    -Sql "ALTER SYSTEM SET synchronous_commit = 'on';" | Out-Null
  Invoke-PostgresSql `
    -Pod $pod `
    -Sql "ALTER SYSTEM SET synchronous_standby_names = '$escapedStandbyNames';" | Out-Null
  $reloaded = Invoke-PostgresSql -Pod $pod -Sql "SELECT pg_reload_conf();"
  if ($reloaded -ne "t") {
    throw "PostgreSQL configuration reload failed on pod $pod"
  }
  $loadedCommit = Invoke-PostgresSql `
    -Pod $pod `
    -Sql "SELECT current_setting('synchronous_commit');"
  if ($loadedCommit -ne "on") {
    throw "PostgreSQL did not load synchronous_commit=on on pod $pod"
  }
  $loadedStandbyNames = Invoke-PostgresSql `
    -Pod $pod `
    -Sql "SELECT current_setting('synchronous_standby_names');"
  if ($loadedStandbyNames -ne $synchronousStandbyNames) {
    throw "PostgreSQL did not load synchronous_standby_names on pod $pod"
  }
}

$expectedPodSql = ($expectedPods | ForEach-Object { "'$_'" }) -join ", "
$syncCountSql = @"
SELECT count(*)
FROM pg_stat_replication
WHERE state = 'streaming'
  AND sync_state IN ('sync', 'quorum')
  AND application_name IN ($expectedPodSql);
"@
$lastPrimary = $null
$lastSyncCount = 0

while ((Get-Date) -lt $deadline) {
  $lastPrimary = Find-PostgresPrimary -Pods $expectedPods
  if ($lastPrimary) {
    $rawCount = Invoke-PostgresSql `
      -Pod $lastPrimary `
      -Sql $syncCountSql `
      -AllowFailure
    $syncCount = 0
    if ($rawCount) {
      [void][int]::TryParse($rawCount, [ref]$syncCount)
    }
    $lastSyncCount = $syncCount
    if ($syncCount -ge 1) {
      Write-Host "PostgreSQL synchronous replication ready: primary=$lastPrimary sync_or_quorum_standbys=$syncCount"
      return
    }
  }
  Start-Sleep -Seconds $PollIntervalSec
}

throw "Timed out waiting for a sync/quorum standby: primary=$lastPrimary sync_or_quorum_standbys=$lastSyncCount"
