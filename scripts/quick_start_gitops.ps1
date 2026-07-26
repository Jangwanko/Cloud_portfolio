param(
  [Parameter(Mandatory = $true)]
  [string]$RepoUrl,
  [string]$Revision = "master",
  [string]$ClusterName = "messaging-ha",
  [string]$Namespace = "messaging-app",
  [string]$BaseUrl = "http://localhost",
  [string]$ImageRepository = "",
  [string]$ImageTag = ""
)

$ErrorActionPreference = "Stop"

if ($Namespace -ne "messaging-app") {
  throw "GitOps manifests currently target the fixed namespace 'messaging-app'."
}
if ($RepoUrl -notmatch '^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$') {
  throw "RepoUrl must be an HTTPS GitHub repository URL."
}
if ($Revision -notmatch '^[A-Za-z0-9._/-]+$') {
  throw "Revision contains unsupported characters."
}
if ([string]::IsNullOrWhiteSpace($ImageRepository) -xor [string]::IsNullOrWhiteSpace($ImageTag)) {
  throw "ImageRepository and ImageTag must be supplied together."
}

$useImageOverride = -not [string]::IsNullOrWhiteSpace($ImageTag)
$effectiveImageRepository = $ImageRepository
$effectiveImageTag = $ImageTag

if (-not $useImageOverride) {
  $repoPath = ($RepoUrl -replace '^https://github\.com/', '') -replace '\.git$', ''
  $overlayUrl = "https://raw.githubusercontent.com/$repoPath/$Revision/k8s/gitops/overlays/local-ha/kustomization.yaml"
  try {
    $overlay = (Invoke-WebRequest -UseBasicParsing -Uri $overlayUrl -TimeoutSec 20).Content
  } catch {
    throw "Unable to read the committed local-ha overlay from $overlayUrl`nFor a private repository, pass both -ImageRepository and -ImageTag explicitly."
  }
  $repositoryMatch = [regex]::Match($overlay, '(?m)^\s*newName:\s*(\S+)\s*$')
  $tagMatch = [regex]::Match($overlay, '(?m)^\s*newTag:\s*(\S+)\s*$')
  if (-not $repositoryMatch.Success -or -not $tagMatch.Success) {
    throw "Unable to resolve the registry image from $overlayUrl."
  }
  $effectiveImageRepository = $repositoryMatch.Groups[1].Value
  $effectiveImageTag = $tagMatch.Groups[1].Value
}

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

function Assert-RegistryImageAvailable([string]$Repository, [string]$Tag) {
  $docker = Get-Command docker -ErrorAction SilentlyContinue
  if (-not $docker) {
    throw "Docker CLI is required to verify the GitOps registry image."
  }

  $image = "${Repository}:${Tag}"
  & $docker.Source manifest inspect $image *> $null
  if ($LASTEXITCODE -ne 0) {
    throw "Registry image is not accessible: $image`nPublish the initial master image first and make the GHCR package public, or authenticate with 'docker login ghcr.io'."
  }

  Write-Host "[ok] Registry image is accessible: $image"
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
Write-Host "==> Verifying registry-backed GitOps image"
Assert-RegistryImageAvailable -Repository $effectiveImageRepository -Tag $effectiveImageTag

Write-Host ""
Write-Host "==> Removing previous cluster if it exists"
Remove-ClusterIfExists -Name $ClusterName

Write-Host ""
Write-Host "==> Bootstrapping local cluster and shared runtime components"
& "$PSScriptRoot/../k8s/scripts/setup-kind.ps1" -ClusterName $ClusterName
& "$PSScriptRoot/../k8s/scripts/install-ha.ps1" -Namespace $Namespace
& "$PSScriptRoot/../k8s/scripts/install-kube-state-metrics.ps1" -Namespace $Namespace
& "$PSScriptRoot/../k8s/scripts/install-keda.ps1"

Write-Host ""
Write-Host "==> Installing Argo CD"
& "$PSScriptRoot/../k8s/scripts/install-argocd.ps1"

Write-Host ""
Write-Host "==> Registering Argo CD application"
$bootstrapArgs = @{
  RepoUrl  = $RepoUrl
  Revision = $Revision
}
if ($useImageOverride) {
  $bootstrapArgs.ImageRepository = $effectiveImageRepository
  $bootstrapArgs.ImageTag = $effectiveImageTag
}
& "$PSScriptRoot/../k8s/scripts/bootstrap-argocd-app.ps1" @bootstrapArgs

Write-Host ""
Write-Host "==> Waiting for API readiness"
Wait-UrlReady -Url "$BaseUrl/health/ready" -TimeoutSec 240

Write-Host ""
Write-Host "==> Running smoke test"
& "$PSScriptRoot/smoke_test.ps1" `
  -BaseUrl $BaseUrl `
  -Namespace $Namespace `
  -DbDeployment "messaging-postgresql-ha-postgresql"

Write-Host ""
Write-Host "GitOps quick start completed successfully."
Write-Host "Argo CD namespace: argocd"
Write-Host "Application name: messaging-portfolio-local-ha"
Write-Host "Application image: ${effectiveImageRepository}:${effectiveImageTag}"
Write-Host "Image source: $(if ($useImageOverride) { 'explicit Application override' } else { 'committed remote local-ha overlay' })"
Write-Host "API URL: $BaseUrl"
