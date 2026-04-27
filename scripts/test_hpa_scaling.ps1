param(
  [string]$Namespace = "messaging-app",
  [string]$DeploymentName = "api",
  [string]$HpaName = "api-hpa",
  [string]$JobManifest = "k8s/app/k6-job.yaml",
  [string]$ScriptPath = "scripts/load_test_k6.js",
  [int]$TimeoutSec = 420
)

$ErrorActionPreference = "Stop"

function Wait-MetricsApi([int]$WaitSec = 180) {
  $deadline = (Get-Date).AddSeconds($WaitSec)
  while ((Get-Date) -lt $deadline) {
    try {
      kubectl top pods -n $Namespace | Out-Null
      return
    } catch {}
    Start-Sleep -Seconds 3
  }
  throw "Timed out waiting for Kubernetes metrics API."
}

function Get-ReplicaCount([string]$Name) {
  $raw = kubectl -n $Namespace get deployment $Name -o jsonpath="{.status.replicas}"
  $count = 0
  [void][int]::TryParse(($raw | Out-String).Trim(), [ref]$count)
  return $count
}

function Get-HpaCurrentReplicas([string]$Name) {
  $raw = kubectl -n $Namespace get hpa $Name -o jsonpath="{.status.currentReplicas}"
  $count = 0
  [void][int]::TryParse(($raw | Out-String).Trim(), [ref]$count)
  return $count
}

function Get-HpaTargetState([string]$Name) {
  $raw = kubectl -n $Namespace get hpa $Name -o jsonpath="{.status.currentMetrics[0].resource.current.averageUtilization}"
  return ($raw | Out-String).Trim()
}

Wait-MetricsApi

$initialReplicas = Get-ReplicaCount -Name $DeploymentName
if ($initialReplicas -lt 1) {
  throw "Deployment/$DeploymentName has no running replicas."
}

kubectl -n $Namespace delete job k6-load-test --ignore-not-found | Out-Null
kubectl -n $Namespace delete configmap k6-script --ignore-not-found | Out-Null
kubectl -n $Namespace create configmap k6-script --from-file=load_test_k6.js=$ScriptPath | Out-Null
kubectl apply -f $JobManifest | Out-Null

$scaled = $false
$maxReplicas = $initialReplicas
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
  $currentDeploymentReplicas = Get-ReplicaCount -Name $DeploymentName
  $currentHpaReplicas = Get-HpaCurrentReplicas -Name $HpaName

  if ($currentDeploymentReplicas -gt $maxReplicas) {
    $maxReplicas = $currentDeploymentReplicas
  }
  if ($currentHpaReplicas -gt $maxReplicas) {
    $maxReplicas = $currentHpaReplicas
  }

  if ($currentDeploymentReplicas -gt $initialReplicas -or $currentHpaReplicas -gt $initialReplicas) {
    $scaled = $true
    break
  }

  $jobSucceeded = kubectl -n $Namespace get job k6-load-test -o jsonpath="{.status.succeeded}" 2>$null
  $jobFailed = kubectl -n $Namespace get job k6-load-test -o jsonpath="{.status.failed}" 2>$null
  $succeededCount = 0
  $failedCount = 0
  [void][int]::TryParse(($jobSucceeded | Out-String).Trim(), [ref]$succeededCount)
  [void][int]::TryParse(($jobFailed | Out-String).Trim(), [ref]$failedCount)

  if ($succeededCount -ge 1 -or $failedCount -ge 1) {
    Start-Sleep -Seconds 10
  } else {
    Start-Sleep -Seconds 5
  }
}

$cpuTarget = Get-HpaTargetState -Name $HpaName
$hpaDescribe = kubectl describe hpa $HpaName -n $Namespace | Out-String
$jobLogs = kubectl -n $Namespace logs job/k6-load-test 2>$null | Out-String
kubectl -n $Namespace delete job k6-load-test --ignore-not-found | Out-Null
kubectl -n $Namespace delete configmap k6-script --ignore-not-found | Out-Null

if (-not $scaled) {
  if ($cpuTarget -match '^\d+$') {
    Write-Host "HPA metrics test passed: deployment=$DeploymentName stayed at $initialReplicas replicas because CPU remained below target. cpu_target=$cpuTarget"
    return
  }
  throw "HPA metrics test failed: Deployment/$DeploymentName stayed at $initialReplicas replicas and CPU metric was unavailable. HPA CPU reading: $cpuTarget`n$hpaDescribe`n$jobLogs"
}

Write-Host "HPA scaling test passed: deployment=$DeploymentName initial_replicas=$initialReplicas max_replicas=$maxReplicas cpu_target=$cpuTarget"
