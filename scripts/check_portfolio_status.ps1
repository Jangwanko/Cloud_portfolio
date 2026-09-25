param(
  [string]$Context = "",
  [ValidateSet("local-ha", "demo-lite")][string]$Profile = "local-ha",
  [string]$BaseUrl = "http://localhost",
  [string]$PrometheusUrl = "http://localhost/prometheus",
  [string]$Namespace = "messaging-app",
  [string]$ArgoNamespace = "argocd",
  [string]$ArgoApplication = "",
  [switch]$PublicOnly,
  [switch]$SkipArgoCd,
  [switch]$SkipPrometheus
)
$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot "../.venv/Scripts/python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$arguments = @("$PSScriptRoot/check_portfolio_status.py", "--profile", $Profile,
  "--base-url", $BaseUrl, "--prometheus-url", $PrometheusUrl,
  "--namespace", $Namespace, "--argo-namespace", $ArgoNamespace)
if ($Context) { $arguments += @("--context", $Context) }
if ($ArgoApplication) { $arguments += @("--argo-application", $ArgoApplication) }
if ($PublicOnly) { $arguments += "--public-only" }
if ($SkipArgoCd) { $arguments += "--skip-argocd" }
if ($SkipPrometheus) { $arguments += "--skip-prometheus" }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw "Status check failed/incomplete; inspect the saved JSON report (exit=$LASTEXITCODE)" }
