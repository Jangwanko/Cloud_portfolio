param(
  [string]$KindVersion = "v0.27.0",
  [string]$HelmVersion = "v3.17.3",
  [string]$KubectlVersion = "v1.32.2",
  [switch]$Force
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$toolsDir = Join-Path $repoRoot "tools"
$kindPath = Join-Path $toolsDir "kind.exe"
$kubectlPath = Join-Path $toolsDir "kubectl.exe"
$helmDir = Join-Path $toolsDir "helm"
$helmPath = Join-Path $helmDir "windows-amd64\helm.exe"
$downloadDir = Join-Path $toolsDir "downloads"

function Write-ToolOk([string]$Message) {
  Write-Host "[ok] $Message"
}

function Ensure-Directory([string]$Path) {
  if (-not (Test-Path $Path)) {
    New-Item -ItemType Directory -Path $Path | Out-Null
  }
}

function Test-DockerReady() {
  try {
    docker version | Out-Null
  } catch {
    throw "Docker Desktop is required. Install and start Docker Desktop, then rerun this script."
  }
}

function Download-File([string]$Url, [string]$OutFile) {
  Write-Host "Downloading $Url"
  Ensure-Directory -Path (Split-Path $OutFile -Parent)
  Invoke-WebRequest -Uri $Url -OutFile $OutFile
}

function Ensure-Kind() {
  if ((Test-Path $kindPath) -and -not $Force) {
    Write-ToolOk "kind already exists: $kindPath"
    return
  }

  $url = "https://github.com/kubernetes-sigs/kind/releases/download/$KindVersion/kind-windows-amd64"
  Download-File -Url $url -OutFile $kindPath
  Write-ToolOk "kind installed: $kindPath"
}

function Ensure-Kubectl() {
  if ((Test-Path $kubectlPath) -and -not $Force) {
    Write-ToolOk "kubectl already exists: $kubectlPath"
    return
  }

  $url = "https://dl.k8s.io/release/$KubectlVersion/bin/windows/amd64/kubectl.exe"
  Download-File -Url $url -OutFile $kubectlPath
  Write-ToolOk "kubectl installed: $kubectlPath"
}

function Ensure-Helm() {
  if ((Test-Path $helmPath) -and -not $Force) {
    Write-ToolOk "helm already exists: $helmPath"
    return
  }

  Ensure-Directory -Path $downloadDir
  $zipPath = Join-Path $downloadDir "helm-$HelmVersion-windows-amd64.zip"
  $extractPath = Join-Path $downloadDir "helm-$HelmVersion"
  $url = "https://get.helm.sh/helm-$HelmVersion-windows-amd64.zip"

  if (Test-Path $extractPath) {
    Remove-Item -LiteralPath $extractPath -Recurse -Force
  }
  Download-File -Url $url -OutFile $zipPath
  Expand-Archive -LiteralPath $zipPath -DestinationPath $extractPath -Force

  Ensure-Directory -Path (Split-Path $helmPath -Parent)
  Copy-Item -LiteralPath (Join-Path $extractPath "windows-amd64\helm.exe") -Destination $helmPath -Force
  Copy-Item -LiteralPath (Join-Path $extractPath "windows-amd64\LICENSE") -Destination (Join-Path (Split-Path $helmPath -Parent) "LICENSE") -Force
  Copy-Item -LiteralPath (Join-Path $extractPath "windows-amd64\README.md") -Destination (Join-Path (Split-Path $helmPath -Parent) "README.md") -Force
  Write-ToolOk "helm installed: $helmPath"
}

function Add-ToolsToPath() {
  $paths = @(
    $toolsDir,
    (Split-Path $helmPath -Parent)
  )
  foreach ($path in $paths) {
    if ($env:PATH -notlike "*$path*") {
      $env:PATH = "$path;$env:PATH"
    }
  }
}

Test-DockerReady
Ensure-Directory -Path $toolsDir
Ensure-Kind
Ensure-Kubectl
Ensure-Helm
Add-ToolsToPath

Write-Host ""
& $kindPath version
& $kubectlPath version --client=true
& $helmPath version --short
Write-ToolOk "local Kubernetes tools are ready under tools/"
