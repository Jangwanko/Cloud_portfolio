param(
  [Parameter(Mandatory = $true)]
  [string]$RepoUrl,
  [string]$Revision = "master",
  [string]$Namespace = "argocd",
  [string]$AppName = "messaging-portfolio-local-ha",
  [string]$ProjectName = "messaging-portfolio",
  [string]$ProjectFile = "k8s/argocd/project-messaging-portfolio.yaml",
  [string]$ManifestPath = "k8s/gitops/overlays/local-ha",
  [string]$ImageRepository = "",
  [string]$ImageTag = ""
)

$ErrorActionPreference = "Stop"

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
if ($useImageOverride) {
  if ($ImageRepository -notmatch '^ghcr\.io/[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._/-]*$') {
    throw "ImageRepository must be a lowercase GHCR repository path."
  }
  if ($ImageTag -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$') {
    throw "ImageTag is not a valid container image tag."
  }
}

$projectManifest = Get-Content -Raw -Encoding utf8 $ProjectFile
$projectManifest = $projectManifest -replace '(?m)^    - https://github\.com/[^\s]+$', "    - $RepoUrl"
if (-not $projectManifest.Contains("    - $RepoUrl")) {
  throw "Unable to bind the AppProject source repository to RepoUrl."
}
$projectManifest | kubectl apply -f -

$kustomizeOverride = ""
if ($useImageOverride) {
  $kustomizeOverride = @"
    kustomize:
      images:
        - messaging-portfolio=${ImageRepository}:${ImageTag}
"@
}

$applicationManifest = @"
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: $AppName
  namespace: $Namespace
spec:
  project: $ProjectName
  source:
    repoURL: $RepoUrl
    targetRevision: $Revision
    path: $ManifestPath
$kustomizeOverride
  destination:
    server: https://kubernetes.default.svc
    namespace: messaging-app
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - RespectIgnoreDifferences=true
  ignoreDifferences:
    - group: apps
      kind: Deployment
      name: api
      jsonPointers:
        - /spec/replicas
    - group: apps
      kind: Deployment
      name: worker
      jsonPointers:
        - /spec/replicas
"@

$applicationManifest | kubectl apply -f -

Write-Host "Argo CD application $AppName created."
