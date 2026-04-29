param(
  [string]$ClusterName = "messaging-ha",
  [string]$Namespace = "messaging-app",
  [string]$BaseUrl = "http://localhost",
  [string]$TlsBaseUrl = "https://localhost",
  [string]$PrometheusUrl = "http://localhost:9090",
  [switch]$KeepPortForwards
)

$ErrorActionPreference = "Stop"

$apiPortForwardProcess = $null
$prometheusPortForwardProcess = $null
$kindConfig = Join-Path $PSScriptRoot "..\k8s\kind-config.yaml"
$kindPath = Join-Path $PSScriptRoot "..\tools\kind.exe"
$helmPath = Join-Path $PSScriptRoot "..\tools\helm\windows-amd64\helm.exe"

function Fail-Friendly([string]$Message) {
  throw $Message
}

function Invoke-Step([string]$Message, [scriptblock]$Action) {
  Write-Host ""
  Write-Host "==> $Message"
  & $Action
}

function Write-CheckOk([string]$Message) {
  Write-Host "[ok] $Message"
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

function Resolve-HelmPath {
  $cmd = Get-Command helm -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }
  $resolved = Resolve-Path $helmPath -ErrorAction SilentlyContinue
  if ($resolved) {
    return $resolved.Path
  }
  return $null
}

function Require-Command([string]$Name, [string]$Hint) {
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if (-not $cmd) {
    Fail-Friendly $Hint
  }
  return $cmd.Source
}

function Test-DockerReady() {
  try {
    docker version | Out-Null
  } catch {
    Fail-Friendly "Preflight check failed: Docker Desktop is not running or Docker CLI is unavailable.`nStart Docker Desktop, wait for the engine to become ready, and rerun this script."
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

function Assert-Preflight {
  Write-Host "Preflight checks"
  Test-DockerReady
  Write-CheckOk "Docker Desktop is running"

  $resolvedKind = Resolve-KindPath
  if (-not $resolvedKind) {
    Fail-Friendly "Preflight check failed: kind is not available.`nInstall kind or place tools/kind.exe in this repository."
  }
  Write-CheckOk "Using kind: $resolvedKind"

  $resolvedHelm = Resolve-HelmPath
  if (-not $resolvedHelm) {
    Fail-Friendly "Preflight check failed: helm is not available.`nInstall helm or place tools/helm/windows-amd64/helm.exe in this repository."
  }
  Write-CheckOk "Using helm: $resolvedHelm"

  $resolvedKubectl = Require-Command "kubectl" "Preflight check failed: kubectl is not available.`nInstall kubectl or make sure Docker Desktop's kubectl is on PATH."
  Write-CheckOk "Using kubectl: $resolvedKubectl"
}

function Assert-LocalPorts {
  foreach ($port in @(80, 443)) {
    if (-not (Test-PortAvailable -Port $port)) {
      Fail-Friendly "Preflight check failed: local port $port is already in use.`nFree the port and rerun this script."
    }
    Write-CheckOk "Port $port is available"
  }
}

function Remove-ClusterIfExists([string]$Name) {
  $resolvedKind = Resolve-KindPath
  $nodeName = "$Name-control-plane"
  $containers = docker ps -a --format "{{.Names}}"
  if ($containers -contains $nodeName) {
    & $resolvedKind delete cluster --name $Name | Out-Host
  } else {
    Write-Host "[skip] No existing kind cluster named $Name"
  }
}

function Test-UrlReady([string]$Url) {
  try {
    $res = Invoke-RestMethod -Method Get -Uri $Url -TimeoutSec 5
    return ($res.status -eq "ready")
  } catch {
    return $false
  }
}

function Test-HttpOk([string]$Url) {
  try {
    Invoke-WebRequest -Method Get -Uri $Url -TimeoutSec 5 | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Test-HttpsReady([string]$Url) {
  try {
    $raw = & curl.exe -k --silent --show-error $Url
    $res = $raw | ConvertFrom-Json
    return ($res.status -eq "ready")
  } catch {
    return $false
  }
}

function Wait-HttpsReady([string]$Url, [int]$TimeoutSec = 180) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $raw = & curl.exe -k --silent --show-error $Url
      $res = $raw | ConvertFrom-Json
      if ($res.status -eq "ready") {
        return
      }
    } catch {}
    Start-Sleep -Seconds 2
  }

  throw "Timed out waiting for ready response from $Url"
}

function Wait-Deployment([string]$Name, [int]$TimeoutSec = 600) {
  Wait-NamespacedDeployment -Name $Name -Namespace $Namespace -TimeoutSec $TimeoutSec
}

function Wait-NamespacedDeployment([string]$Name, [string]$NamespaceToUse, [int]$TimeoutSec = 600) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $resource = kubectl get "deployment/$Name" -n $NamespaceToUse --ignore-not-found -o name 2>$null
    if ($resource) {
      kubectl rollout status "deployment/$Name" -n $NamespaceToUse --timeout="$($TimeoutSec)s" | Out-Host
      return
    }
    Start-Sleep -Seconds 2
  }

  Fail-Friendly "Timed out waiting for deployment/$Name to appear in namespace $NamespaceToUse."
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

function Wait-TcpPort([string]$Host, [int]$Port, [int]$TimeoutSec = 30) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $client = New-Object System.Net.Sockets.TcpClient
      $async = $client.BeginConnect($Host, $Port, $null, $null)
      if ($async.AsyncWaitHandle.WaitOne(1000, $false) -and $client.Connected) {
        $client.EndConnect($async)
        $client.Close()
        return
      }
      $client.Close()
    } catch {}
    Start-Sleep -Milliseconds 500
  }

  throw "Timed out waiting for TCP port $Host`:$Port"
}

function Start-PortForward([string]$ServiceName, [int]$LocalPort, [int]$RemotePort) {
  $process = Start-Process powershell `
    -ArgumentList @(
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
      "kubectl port-forward -n $Namespace svc/$ServiceName $LocalPort`:$RemotePort"
    ) `
    -PassThru

  Wait-TcpPort -Host "127.0.0.1" -Port $LocalPort -TimeoutSec 30
  return $process
}

try {
  Invoke-Step "Checking Docker availability" {
    Assert-Preflight
  }

  Invoke-Step "Removing previous local Docker Compose resources" {
    $composeFiles = @("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
    $hasComposeFile = $false
    foreach ($composeFile in $composeFiles) {
      if (Test-Path (Join-Path (Get-Location) $composeFile)) {
        $hasComposeFile = $true
        break
      }
    }
    if ($hasComposeFile) {
      docker compose down -v | Out-Host
    } else {
      Write-Host "[skip] No Docker Compose project found"
    }
  }

  Invoke-Step "Removing previous kind cluster if it exists" {
    Remove-ClusterIfExists -Name $ClusterName
  }

  Invoke-Step "Validating local ports" {
    Assert-LocalPorts
  }

  Invoke-Step "Building application image" {
    docker build -t messaging-portfolio:local . | Out-Host
  }

  Invoke-Step "Creating kind cluster" {
    $resolvedKind = Resolve-KindPath
    if (Test-Path $kindConfig) {
      & $resolvedKind create cluster --name $ClusterName --config $kindConfig | Out-Host
    } else {
      & $resolvedKind create cluster --name $ClusterName | Out-Host
    }
  }

  Invoke-Step "Creating application namespace" {
    kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f - | Out-Host
  }

  Invoke-Step "Installing runtime secrets" {
    & "$PSScriptRoot/../k8s/scripts/install-runtime-secrets.ps1" -Namespace $Namespace
  }

  Invoke-Step "Installing metrics-server" {
    & "$PSScriptRoot/../k8s/scripts/install-metrics-server.ps1"
  }

  Invoke-Step "Installing ingress-nginx" {
    & "$PSScriptRoot/../k8s/scripts/install-ingress-nginx.ps1"
  }

  Invoke-Step "Generating local TLS certificate" {
    & "$PSScriptRoot/../k8s/scripts/install-local-tls.ps1" -Namespace $Namespace
  }

  Invoke-Step "Loading local image into kind" {
    $resolvedKind = Resolve-KindPath
    & $resolvedKind load docker-image messaging-portfolio:local --name $ClusterName | Out-Host
  }

  Invoke-Step "Installing HA PostgreSQL" {
    & "$PSScriptRoot/../k8s/scripts/install-ha.ps1" -Namespace $Namespace
  }

  Invoke-Step "Installing kube-state-metrics" {
    & "$PSScriptRoot/../k8s/scripts/install-kube-state-metrics.ps1" -Namespace $Namespace
  }

  Invoke-Step "Installing KEDA" {
    & "$PSScriptRoot/../k8s/scripts/install-keda.ps1"
  }

  Invoke-Step "Applying Kafka runtime" {
    kubectl apply -f k8s/gitops/base/kafka-ha.yaml | Out-Host
    kubectl rollout status statefulset/kafka -n $Namespace --timeout=600s | Out-Host
    kubectl wait --for=condition=complete job/kafka-topic-bootstrap -n $Namespace --timeout=300s | Out-Host
  }

  Invoke-Step "Applying application manifests" {
    kubectl apply -f k8s/app/manifests-ha.yaml | Out-Host
  }

  Invoke-Step "Waiting for deployments" {
    Wait-NamespacedDeployment -Name "ingress-nginx-controller" -NamespaceToUse "ingress-nginx"
    Wait-Deployment -Name "kube-state-metrics"
    Wait-NamespacedDeployment -Name "keda-operator" -NamespaceToUse "keda"
    Wait-Deployment -Name "api"
    Wait-Deployment -Name "worker"
    Wait-Deployment -Name "dlq-replayer"
    Wait-Deployment -Name "prometheus"
    Wait-Deployment -Name "grafana"
  }

  Invoke-Step "Waiting for API readiness" {
    Wait-UrlReady -Url "$BaseUrl/health/ready" -TimeoutSec 180
  }

  Invoke-Step "Verifying HTTPS ingress readiness" {
    Wait-HttpsReady -Url "$TlsBaseUrl/health/ready" -TimeoutSec 180
  }

  Invoke-Step "Running smoke test" {
    & "$PSScriptRoot/smoke_test.ps1" `
      -BaseUrl $BaseUrl `
      -Namespace $Namespace `
      -DbDeployment "messaging-postgresql-ha-postgresql"
  }

  Invoke-Step "Running DB recovery test" {
    & "$PSScriptRoot/test_db_down.ps1" `
      -BaseUrl $BaseUrl `
      -Namespace $Namespace `
      -ApiDeployment "api" `
      -DbDeployment "messaging-postgresql-ha-postgresql"
  }

  Invoke-Step "Running HPA scaling test" {
    & "$PSScriptRoot/test_hpa_scaling.ps1" `
      -Namespace $Namespace `
      -DeploymentName "api" `
      -HpaName "api-hpa"
  }

  Write-Host ""
  Write-Host "Quick Start all-in-one run completed successfully."
  Write-Host "API URL: $BaseUrl"
  Write-Host "Grafana URL: http://localhost/grafana"
  Write-Host "Grafana login: admin / 1q2w3e4r"
  Write-Host "Prometheus URL: http://localhost/prometheus"
  Write-Host "TLS ingress is also available from $TlsBaseUrl for the same paths."
  Write-Host "k6 load tests remain separate: powershell -ExecutionPolicy Bypass -File scripts/test_k6_load.ps1"
}
catch {
  Write-Host ""
  Write-Host $_.Exception.Message
  exit 1
}
finally {
  if (-not $KeepPortForwards) {
    if ($apiPortForwardProcess -and -not $apiPortForwardProcess.HasExited) {
      Stop-Process -Id $apiPortForwardProcess.Id -Force
    }
    if ($prometheusPortForwardProcess -and -not $prometheusPortForwardProcess.HasExited) {
      Stop-Process -Id $prometheusPortForwardProcess.Id -Force
    }
  }
}
