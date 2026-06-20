param(
  [string]$ClusterName = "messaging-lite",
  [string]$Namespace = "messaging-app",
  [string]$BaseUrl = "http://localhost",
  [string]$TlsBaseUrl = "https://localhost"
)

$ErrorActionPreference = "Stop"

$kindConfig = Join-Path $PSScriptRoot "..\k8s\kind-config.yaml"
$kindPath = Join-Path $PSScriptRoot "..\tools\kind.exe"
$litePgValues = "k8s/values/postgresql-lite-values.yaml"
$liteOverlay = "k8s/gitops/overlays/demo-lite"

function Fail-Friendly([string]$Message) {
  throw $Message
}

function Invoke-Step([string]$Message, [scriptblock]$Action) {
  Write-Host ""
  Write-Host "==> $Message"
  & $Action
}

function Resolve-KindPath {
  $cmd = Get-Command kind -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }
  $resolved = Resolve-Path $kindPath -ErrorAction SilentlyContinue
  if ($resolved) {
    return $resolved.Path
  }
  return $null
}

function Test-DockerReady {
  try {
    docker version | Out-Null
  } catch {
    Fail-Friendly "Docker Desktop is not running or Docker CLI is unavailable."
  }
}

function Test-PortAvailable([int]$Port) {
  $listener = $null
  try {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    $listener.Start()
    return $true
  } catch {
    return $false
  } finally {
    if ($listener) {
      $listener.Stop()
    }
  }
}

function Assert-LocalPorts {
  foreach ($port in @(80, 443)) {
    if (-not (Test-PortAvailable -Port $port)) {
      Fail-Friendly "Local port $port is already in use. Free the port and rerun this script."
    }
  }
}

function Remove-ClusterIfExists([string]$Name) {
  $kind = Resolve-KindPath
  $nodeName = "$Name-control-plane"
  $containers = docker ps -a --format "{{.Names}}"
  if ($containers -contains $nodeName) {
    & $kind delete cluster --name $Name | Out-Host
  } else {
    Write-Host "[skip] No existing kind cluster named $Name"
  }
}

function Wait-Deployment([string]$Name, [int]$TimeoutSec = 600) {
  kubectl rollout status "deployment/$Name" -n $Namespace --timeout="$($TimeoutSec)s" | Out-Host
}

function Wait-NamespacedDeployment([string]$Name, [string]$NamespaceToUse, [int]$TimeoutSec = 600) {
  kubectl rollout status "deployment/$Name" -n $NamespaceToUse --timeout="$($TimeoutSec)s" | Out-Host
}

function Wait-UrlReady([string]$Url, [int]$TimeoutSec = 180) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $res = Invoke-RestMethod -Method Get -Uri $Url -TimeoutSec 5
      if ($res.status -in @("ready", "degraded")) {
        return
      }
    } catch {}
    Start-Sleep -Seconds 2
  }

  throw "Timed out waiting for ready/degraded response from $Url"
}

function Wait-HttpsReady([string]$Url, [int]$TimeoutSec = 180) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $raw = & curl.exe -k --silent --show-error $Url
      $res = $raw | ConvertFrom-Json
      if ($res.status -in @("ready", "degraded")) {
        return
      }
    } catch {}
    Start-Sleep -Seconds 2
  }

  throw "Timed out waiting for ready/degraded response from $Url"
}

try {
  Invoke-Step "Checking local tools" {
    & "$PSScriptRoot/bootstrap_tools.ps1"
    Test-DockerReady
    if (-not (Resolve-KindPath)) {
      Fail-Friendly "kind is not available."
    }
    if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
      Fail-Friendly "kubectl is not available."
    }
  }

  Invoke-Step "Removing previous lite kind cluster if it exists" {
    Remove-ClusterIfExists -Name $ClusterName
  }

  Invoke-Step "Validating local ports" {
    Assert-LocalPorts
  }

  Invoke-Step "Building application image" {
    docker build -t messaging-portfolio:local . | Out-Host
  }

  Invoke-Step "Creating lite kind cluster" {
    $kind = Resolve-KindPath
    if (Test-Path $kindConfig) {
      & $kind create cluster --name $ClusterName --config $kindConfig | Out-Host
    } else {
      & $kind create cluster --name $ClusterName | Out-Host
    }
  }

  Invoke-Step "Preparing namespace and runtime dependencies" {
    kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f - | Out-Host
    & "$PSScriptRoot/../k8s/scripts/install-runtime-secrets.ps1" -Namespace $Namespace
    & "$PSScriptRoot/../k8s/scripts/install-metrics-server.ps1"
    & "$PSScriptRoot/../k8s/scripts/install-ingress-nginx.ps1"
    & "$PSScriptRoot/../k8s/scripts/install-local-tls.ps1" -Namespace $Namespace
  }

  Invoke-Step "Loading local image into kind" {
    $kind = Resolve-KindPath
    & $kind load docker-image messaging-portfolio:local --name $ClusterName | Out-Host
  }

  Invoke-Step "Installing lite PostgreSQL profile" {
    & "$PSScriptRoot/../k8s/scripts/install-ha.ps1" -Namespace $Namespace -ValuesFile $litePgValues
  }

  Invoke-Step "Installing observability and autoscaling dependencies" {
    & "$PSScriptRoot/../k8s/scripts/install-kube-state-metrics.ps1" -Namespace $Namespace
    & "$PSScriptRoot/../k8s/scripts/install-keda.ps1"
  }

  Invoke-Step "Applying demo-lite runtime" {
    kubectl apply -k $liteOverlay | Out-Host
    kubectl rollout status statefulset/kafka -n $Namespace --timeout=600s | Out-Host
    kubectl wait --for=condition=complete job/kafka-topic-bootstrap -n $Namespace --timeout=300s | Out-Host
  }

  Invoke-Step "Waiting for lite deployments" {
    Wait-NamespacedDeployment -Name "ingress-nginx-controller" -NamespaceToUse "ingress-nginx"
    Wait-Deployment -Name "kube-state-metrics"
    Wait-NamespacedDeployment -Name "keda-operator" -NamespaceToUse "keda"
    Wait-Deployment -Name "api"
    Wait-Deployment -Name "worker"
    Wait-Deployment -Name "prometheus"
    Wait-Deployment -Name "grafana"
  }

  Invoke-Step "Waiting for API readiness" {
    Wait-UrlReady -Url "$BaseUrl/health/ready" -TimeoutSec 180
    Wait-HttpsReady -Url "$TlsBaseUrl/health/ready" -TimeoutSec 180
  }

  Invoke-Step "Running smoke test" {
    & "$PSScriptRoot/smoke_test.ps1" `
      -BaseUrl $BaseUrl `
      -Namespace $Namespace `
      -DbDeployment "messaging-postgresql-ha-postgresql"
  }

  Write-Host ""
  Write-Host "Lite demo run completed."
  Write-Host "Demo UI: $BaseUrl/demo/order-dashboard.html"
  Write-Host "API docs: $BaseUrl/docs"
  Write-Host "Grafana: $BaseUrl/grafana/d/messaging-portfolio-overview/messaging-portfolio-operations-overview?orgId=1&refresh=5s"
  Write-Host "Profile: demo-lite keeps the Kafka -> Worker -> DB flow, but reduces HA and scale-out capacity for 2-core demo hosts."
}
catch {
  Write-Host ""
  Write-Host $_.Exception.Message
  exit 1
}
