param(
  [Parameter(Mandatory=$true)][string]$SshKey,
  [string]$Terraform = "terraform"
)
$ErrorActionPreference = "Stop"
$ssh = Join-Path $env:WINDIR "System32/OpenSSH/ssh.exe"
Set-Location $PSScriptRoot
function Invoke-Terraform {
  & $Terraform @args
  if ($LASTEXITCODE -ne 0) { throw "Terraform failed: $args" }
}
Invoke-Terraform init -input=false
Invoke-Terraform fmt -check
Invoke-Terraform validate
Invoke-Terraform plan '-out=deployment.tfplan' '-input=false'
Invoke-Terraform apply -input=false deployment.tfplan
$address = & $Terraform output -raw floating_ip
if ($LASTEXITCODE -ne 0) { throw "Cannot read floating IP" }
Write-Host "Waiting for cloud-init on $address (up to 30 minutes)"
$deadline = (Get-Date).AddMinutes(30)
$passed = $false
while ((Get-Date) -lt $deadline) {
  $ErrorActionPreference = "Continue"
  $status = & $ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -i $SshKey "ubuntu@$address" 'sudo cat /opt/demo/status' 2>$null
  $sshExit = $LASTEXITCODE
  $ErrorActionPreference = "Stop"
  if ($sshExit -eq 0 -and "$status".Trim() -eq "PASS") { $passed = $true; break }
  if ("$status".Trim() -eq "FAILED") { throw "Bootstrap failed. Inspect sudo tail -n 100 /var/log/demo-bootstrap.log on $address" }
  Start-Sleep -Seconds 10
}
if (-not $passed) { throw "Bootstrap/SSH timeout; inspect cloud-init and /var/log/demo-bootstrap.log. Infrastructure remains for diagnosis." }
$response = Invoke-RestMethod -Uri "http://${address}:30080/health/ready" -TimeoutSec 15
if ($response.status -ne "ready") { throw "External API is not ready" }
& $Terraform plan -detailed-exitcode -input=false
if ($LASTEXITCODE -ne 0) { throw "Post-deployment plan is not clean (0 required)." }
Write-Host "PASS: bootstrap, event persistence, external readiness, clean Terraform plan"
Write-Host "http://${address}:30080/demo/order-dashboard.html"
