param(
  [string]$Namespace = "messaging-app",
  [string]$AuthSecretKey = "",
  [string]$GrafanaAdminUser = "admin",
  [string]$GrafanaAdminPassword = "1q2w3e4r",
  [string]$SecretName = "messaging-runtime-secrets",
  [switch]$ShowCredentials
)

$ErrorActionPreference = "Stop"

function New-RandomSecret([int]$Bytes = 32) {
  $buffer = New-Object byte[] $Bytes
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
  return [Convert]::ToBase64String($buffer).TrimEnd("=")
}

if (-not $AuthSecretKey) {
  $AuthSecretKey = New-RandomSecret -Bytes 48
}

if (-not $GrafanaAdminPassword) {
  $GrafanaAdminPassword = New-RandomSecret -Bytes 18
}

kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f - | Out-Host

kubectl create secret generic $SecretName `
  -n $Namespace `
  --from-literal=AUTH_SECRET_KEY=$AuthSecretKey `
  --from-literal=ACCESS_TOKEN_TTL_SECONDS=3600 `
  --from-literal=GRAFANA_ADMIN_USER=$GrafanaAdminUser `
  --from-literal=GRAFANA_ADMIN_PASSWORD=$GrafanaAdminPassword `
  --dry-run=client `
  -o yaml | kubectl apply -f - | Out-Host

Write-Host "Runtime secret installed: $SecretName"
Write-Host "Grafana admin user: $GrafanaAdminUser"
if ($ShowCredentials) {
  Write-Host "Grafana admin password: $GrafanaAdminPassword"
}
