param(
  [Parameter(Mandatory = $true)]
  [string]$RepoUrl,
  [string]$Revision = "master",
  [string]$ClusterName = "messaging-ha",
  [string]$Namespace = "messaging-app",
  [string]$BaseUrl = "http://localhost"
)

$ErrorActionPreference = "Stop"

function Resolve-KindPath {
  $cmd = Get-Command kind -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }

  $local = Join-Path $PSScriptRoot "..\tools\kind.exe"
  $resolved = Resolve-Path $local -ErrorAction SilentlyContinue
  if ($resolved) {
    return $resolved.Path
  }

  throw "kind executable not found. Install kind or place tools/kind.exe in this repository."
}

function Remove-ClusterIfExists([string]$Name) {
  $kind = Resolve-KindPath
  $clusters = & $kind get clusters 2>$null
  if ($clusters -and ($clusters -contains $Name)) {
    & $kind delete cluster --name $Name
  }
}

function Load-ImageIntoKind([string]$Cluster, [string]$Image) {
  $kind = Resolve-KindPath
  & $kind load docker-image $Image --name $Cluster
}

function Wait-UrlReady([string]$Url, [int]$TimeoutSec = 180) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $res = Invoke-RestMethod -Method Get -Uri $Url -TimeoutSec 5
      if ($res.status -eq "ready") {
        return
      }
    } catch {}
    Start-Sleep -Seconds 2
  }

  throw "Timed out waiting for ready response from $Url"
}

Write-Host ""
Write-Host "==> Removing previous cluster if it exists"
Remove-ClusterIfExists -Name $ClusterName

Write-Host ""
Write-Host "==> Building application image"
docker build -t messaging-portfolio:local . | Out-Host

Write-Host ""
Write-Host "==> Bootstrapping local cluster and shared runtime components"
& "$PSScriptRoot/../k8s/scripts/setup-kind.ps1" -ClusterName $ClusterName
& "$PSScriptRoot/../k8s/scripts/install-ha.ps1" -Namespace $Namespace
& "$PSScriptRoot/../k8s/scripts/install-kube-state-metrics.ps1" -Namespace $Namespace
& "$PSScriptRoot/../k8s/scripts/install-keda.ps1"

Write-Host ""
Write-Host "==> Loading application image into kind"
Load-ImageIntoKind -Cluster $ClusterName -Image "messaging-portfolio:local"

Write-Host ""
Write-Host "==> Installing Argo CD"
& "$PSScriptRoot/../k8s/scripts/install-argocd.ps1"

Write-Host ""
Write-Host "==> Registering Argo CD application"
& "$PSScriptRoot/../k8s/scripts/bootstrap-argocd-app.ps1" `
  -RepoUrl $RepoUrl `
  -Revision $Revision

Write-Host ""
Write-Host "==> Waiting for API readiness"
Wait-UrlReady -Url "$BaseUrl/health/ready" -TimeoutSec 240

Write-Host ""
Write-Host "==> Running smoke test"
& "$PSScriptRoot/smoke_test.ps1" `
  -BaseUrl $BaseUrl `
  -Namespace $Namespace `
  -DbDeployment "messaging-postgresql-ha-postgresql" `
  -RedisDeployment "messaging-redis-node"

Write-Host ""
Write-Host "GitOps quick start completed successfully."
Write-Host "Argo CD namespace: argocd"
Write-Host "Application name: messaging-portfolio-local-ha"
Write-Host "API URL: $BaseUrl"
