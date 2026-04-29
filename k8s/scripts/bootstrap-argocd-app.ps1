param(
  [Parameter(Mandatory = $true)]
  [string]$RepoUrl,
  [string]$Revision = "dev-kafka",
  [string]$Namespace = "argocd",
  [string]$AppName = "messaging-portfolio-local-ha",
  [string]$ProjectName = "messaging-portfolio",
  [string]$ProjectFile = "k8s/argocd/project-messaging-portfolio.yaml",
  [string]$ManifestPath = "k8s/gitops/overlays/local-ha"
)

$ErrorActionPreference = "Stop"

kubectl apply -f $ProjectFile

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
      jsonPointers:
        - /spec/replicas
"@

$applicationManifest | kubectl apply -f -

Write-Host "Argo CD application $AppName created."
