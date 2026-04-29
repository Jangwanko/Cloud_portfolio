param(
  [string]$Namespace = "argocd",
  [string]$ManifestUrl = "https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml"
)

$ErrorActionPreference = "Stop"

function Clear-ProxyForKubectlDownload {
  $proxyVars = @("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "GIT_HTTP_PROXY", "GIT_HTTPS_PROXY")
  foreach ($name in $proxyVars) {
    Set-Item -Path "Env:$name" -Value "" -ErrorAction SilentlyContinue
  }
}

function Wait-Deployment([string]$Name, [string]$NamespaceToUse, [int]$TimeoutSec = 600) {
  kubectl rollout status "deployment/$Name" -n $NamespaceToUse --timeout="$($TimeoutSec)s"
}

function Wait-StatefulSet([string]$Name, [string]$NamespaceToUse, [int]$TimeoutSec = 600) {
  kubectl rollout status "statefulset/$Name" -n $NamespaceToUse --timeout="$($TimeoutSec)s"
}

function Install-PortfolioHealthCustomizations([string]$NamespaceToUse) {
  $healthLua = @'
hs = {}

if obj.metadata ~= nil and obj.metadata.namespace == "messaging-app" and obj.metadata.name == "postgres-backups" then
  if obj.status ~= nil and obj.status.phase == "Bound" then
    hs.status = "Healthy"
    hs.message = "Backup PVC is bound."
    return hs
  end

  if obj.status ~= nil and obj.status.phase == "Pending" then
    hs.status = "Healthy"
    hs.message = "Backup PVC is waiting for the first CronJob consumer. This is expected with local-path WaitForFirstConsumer."
    return hs
  end
end

if obj.status ~= nil and obj.status.phase == "Bound" then
  hs.status = "Healthy"
  hs.message = "PVC is bound."
else
  hs.status = "Progressing"
  if obj.status ~= nil and obj.status.phase ~= nil then
    hs.message = "PVC phase is " .. obj.status.phase
  else
    hs.message = "PVC is not bound."
  end
end

return hs
'@

  $patch = @{
    data = @{
      "resource.customizations.health.PersistentVolumeClaim" = $healthLua
    }
  } | ConvertTo-Json -Depth 5

  $patchFile = New-TemporaryFile
  try {
    Set-Content -Path $patchFile -Value $patch -Encoding utf8
    kubectl patch configmap argocd-cm -n $NamespaceToUse --type merge --patch-file $patchFile
  } finally {
    Remove-Item -Force $patchFile -ErrorAction SilentlyContinue
  }
}

kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f -
Clear-ProxyForKubectlDownload
kubectl apply --server-side --force-conflicts -n $Namespace -f $ManifestUrl
Install-PortfolioHealthCustomizations -NamespaceToUse $Namespace

Wait-Deployment -Name "argocd-server" -NamespaceToUse $Namespace
Wait-Deployment -Name "argocd-repo-server" -NamespaceToUse $Namespace
Wait-Deployment -Name "argocd-redis" -NamespaceToUse $Namespace
Wait-Deployment -Name "argocd-dex-server" -NamespaceToUse $Namespace
Wait-StatefulSet -Name "argocd-application-controller" -NamespaceToUse $Namespace

Write-Host "Argo CD installed in namespace $Namespace."
