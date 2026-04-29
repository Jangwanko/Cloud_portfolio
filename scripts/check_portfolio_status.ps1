param(
  [string]$BaseUrl = "http://localhost",
  [string]$PrometheusUrl = "http://localhost/prometheus",
  [string]$Namespace = "messaging-app",
  [string]$ArgoNamespace = "argocd",
  [string]$ArgoApplication = "messaging-portfolio-local-ha",
  [switch]$SkipArgoCd,
  [switch]$SkipPrometheus
)

$ErrorActionPreference = "Stop"

$script:Warnings = New-Object System.Collections.Generic.List[string]

function Write-Section([string]$Message) {
  Write-Host ""
  Write-Host "==> $Message"
}

function Add-Warning([string]$Message) {
  $script:Warnings.Add($Message)
  Write-Warning $Message
}

function Assert-Command([string]$Name) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Required command not found: $Name"
  }
}

function Get-KubectlJson([string[]]$ArgumentList) {
  $raw = & kubectl @ArgumentList -o json
  if ($LASTEXITCODE -ne 0) {
    throw "kubectl command failed: kubectl $($ArgumentList -join ' ') -o json"
  }
  return $raw | ConvertFrom-Json
}

function Assert-DeploymentReady([string]$Name) {
  $deployment = Get-KubectlJson @("get", "deployment", $Name, "-n", $Namespace)
  $desired = [int]$deployment.spec.replicas
  $available = [int]$deployment.status.availableReplicas
  if ($available -lt $desired) {
    throw "Deployment $Name is not ready: available=$available desired=$desired"
  }
  Write-Host ("deployment/{0} ready {1}/{2}" -f $Name, $available, $desired)
}

function Assert-StatefulSetReady([string]$Name) {
  $statefulSet = Get-KubectlJson @("get", "statefulset", $Name, "-n", $Namespace)
  $desired = [int]$statefulSet.spec.replicas
  $ready = [int]$statefulSet.status.readyReplicas
  if ($ready -lt $desired) {
    throw "StatefulSet $Name is not ready: ready=$ready desired=$desired"
  }
  Write-Host ("statefulset/{0} ready {1}/{2}" -f $Name, $ready, $desired)
}

function Invoke-PrometheusQuery([string]$Query) {
  $encoded = [uri]::EscapeDataString($Query)
  $response = Invoke-RestMethod -Method Get -Uri "$PrometheusUrl/api/v1/query?query=$encoded" -TimeoutSec 10
  if ($response.status -ne "success") {
    throw "Prometheus query failed: $Query"
  }
  return $response.data.result
}

function Get-ScalarPrometheusValue([string]$Query) {
  $result = Invoke-PrometheusQuery $Query
  if ($result.Count -eq 0) {
    throw "Prometheus query returned no data: $Query"
  }
  return [double]$result[0].value[1]
}

Assert-Command "kubectl"

Write-Section "Kubernetes context"
$context = kubectl config current-context
Write-Host "context=$context"
kubectl get namespace $Namespace | Out-Host

Write-Section "Application readiness"
$health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health/ready" -TimeoutSec 10
if ($health.status -ne "ready") {
  throw "API readiness is not ready: status=$($health.status)"
}
Write-Host "api readiness=ready"

if (-not $SkipArgoCd) {
  Write-Section "Argo CD GitOps"
  $app = Get-KubectlJson @("get", "application", $ArgoApplication, "-n", $ArgoNamespace)
  $sync = [string]$app.status.sync.status
  $healthStatus = [string]$app.status.health.status
  $revision = [string]$app.status.sync.revision
  Write-Host ("application/{0} sync={1} health={2} revision={3}" -f $ArgoApplication, $sync, $healthStatus, $revision)
  if ($sync -ne "Synced" -or $healthStatus -ne "Healthy") {
    throw "Argo CD application is not Synced / Healthy"
  }
}

Write-Section "Core workloads"
foreach ($name in @("api", "worker", "dlq-replayer", "kafka-exporter", "prometheus", "grafana", "kube-state-metrics", "messaging-postgresql-ha-pgpool")) {
  Assert-DeploymentReady $name
}
foreach ($name in @("kafka", "messaging-postgresql-ha-postgresql")) {
  Assert-StatefulSetReady $name
}

Write-Section "Autoscaling"
$apiHpa = Get-KubectlJson @("get", "hpa", "api-hpa", "-n", $Namespace)
$workerHpa = Get-KubectlJson @("get", "hpa", "worker-keda-hpa", "-n", $Namespace)
Write-Host ("api-hpa current={0} desired={1}" -f $apiHpa.status.currentReplicas, $apiHpa.status.desiredReplicas)
Write-Host ("worker-keda-hpa current={0} desired={1}" -f $workerHpa.status.currentReplicas, $workerHpa.status.desiredReplicas)

$scaledObject = Get-KubectlJson @("get", "scaledobject", "worker-keda", "-n", $Namespace)
$readyCondition = $scaledObject.status.conditions | Where-Object { $_.type -eq "Ready" } | Select-Object -First 1
if ($readyCondition.status -ne "True") {
  throw "KEDA ScaledObject worker-keda is not Ready"
}
Write-Host "scaledobject/worker-keda ready=True"

if (-not $SkipPrometheus) {
  Write-Section "Prometheus and Kafka exporter"
  foreach ($job in @("api", "worker", "dlq-replayer", "kafka-exporter", "kube-state-metrics")) {
    $value = Get-ScalarPrometheusValue "up{job=`"$job`"}"
    Write-Host ("up{{job=""{0}""}}={1}" -f $job, $value)
    if ($value -lt 1) {
      throw "Prometheus scrape is down for job=$job"
    }
  }

  $brokerCount = Get-ScalarPrometheusValue "kafka_brokers"
  $consumerLag = Get-ScalarPrometheusValue "sum(kafka_consumergroup_lag{consumergroup=`"message-worker`"})"
  Write-Host "kafka_brokers=$brokerCount"
  Write-Host "message-worker consumer_lag=$consumerLag"
  if ($brokerCount -lt 3) {
    throw "Kafka broker count is below local HA target: $brokerCount"
  }
  if ($consumerLag -gt 100) {
    Add-Warning "message-worker consumer lag is above warning threshold: $consumerLag"
  }
}

Write-Section "Backup PVC"
$backupPvc = Get-KubectlJson @("get", "pvc", "postgres-backups", "-n", $Namespace)
$backupPhase = [string]$backupPvc.status.phase
Write-Host "pvc/postgres-backups phase=$backupPhase"
if ($backupPhase -eq "Pending") {
  Add-Warning "postgres-backups PVC is Pending until the first backup CronJob consumer is scheduled. This is expected with local-path WaitForFirstConsumer."
} elseif ($backupPhase -ne "Bound") {
  throw "Unexpected backup PVC phase: $backupPhase"
}

Write-Host ""
if ($script:Warnings.Count -gt 0) {
  Write-Host "Portfolio status check passed with warnings:"
  foreach ($warning in $script:Warnings) {
    Write-Host "- $warning"
  }
} else {
  Write-Host "Portfolio status check passed."
}
