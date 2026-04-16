$ErrorActionPreference = "Stop"

$manifestUrl = "https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/kind/deploy.yaml"

function Wait-JobIfPresent([string]$Namespace, [string]$JobName, [int]$TimeoutSec = 300) {
  $job = kubectl get job $JobName -n $Namespace --ignore-not-found -o name 2>$null
  if ($job) {
    kubectl wait --namespace $Namespace --for=condition=complete $job --timeout="$($TimeoutSec)s" | Out-Host
  }
}

kubectl apply -f $manifestUrl | Out-Host
Wait-JobIfPresent -Namespace "ingress-nginx" -JobName "ingress-nginx-admission-create"
Wait-JobIfPresent -Namespace "ingress-nginx" -JobName "ingress-nginx-admission-patch"
kubectl rollout status deployment/ingress-nginx-controller -n ingress-nginx --timeout=300s | Out-Host
