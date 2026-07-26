param(
  [string]$KindVersion = "v0.27.0",
  [string]$HelmVersion = "v3.21.3",
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
  Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $OutFile
}

function Assert-Sha256([string]$File, [string]$ChecksumFile) {
  $checksumText = (Get-Content -Raw -Encoding ascii $ChecksumFile).Trim()
  $match = [regex]::Match($checksumText, '(?i)\b[0-9a-f]{64}\b')
  if (-not $match.Success) {
    throw "Official checksum file does not contain a SHA256 digest: $ChecksumFile"
  }

  $expected = $match.Value.ToLowerInvariant()
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $File).Hash.ToLowerInvariant()
  if ($actual -ne $expected) {
    throw "SHA256 verification failed for $File. Expected $expected but found $actual."
  }

  Write-ToolOk "SHA256 verified: $File"
}

function Assert-ReleaseVersion([string]$Name, [string]$Version) {
  if ($Version -notmatch '^v\d+\.\d+\.\d+$') {
    throw "$Name version must be pinned as vMAJOR.MINOR.PATCH. Received: $Version"
  }
}

function Remove-ToolDirectory([string]$Path) {
  $root = [IO.Path]::GetFullPath($downloadDir).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
  $target = [IO.Path]::GetFullPath($Path)
  if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to remove a directory outside the managed download directory: $target"
  }
  if (Test-Path -LiteralPath $target) {
    Remove-Item -LiteralPath $target -Recurse -Force
  }
}

function Ensure-Kind() {
  Ensure-Directory -Path $downloadDir
  $url = "https://github.com/kubernetes-sigs/kind/releases/download/$KindVersion/kind-windows-amd64"
  $downloadPath = Join-Path $downloadDir "kind-$KindVersion-windows-amd64.exe"
  $checksumPath = "$downloadPath.sha256"

  if ((Test-Path $kindPath) -and -not $Force) {
    Download-File -Url "$url.sha256" -OutFile $checksumPath
    Assert-Sha256 -File $kindPath -ChecksumFile $checksumPath
    Write-ToolOk "kind already exists and is verified: $kindPath"
    return
  }

  Download-File -Url $url -OutFile $downloadPath
  Download-File -Url "$url.sha256" -OutFile $checksumPath
  Assert-Sha256 -File $downloadPath -ChecksumFile $checksumPath
  Move-Item -LiteralPath $downloadPath -Destination $kindPath -Force
  Write-ToolOk "kind installed: $kindPath"
}

function Ensure-Kubectl() {
  Ensure-Directory -Path $downloadDir
  $url = "https://dl.k8s.io/release/$KubectlVersion/bin/windows/amd64/kubectl.exe"
  $downloadPath = Join-Path $downloadDir "kubectl-$KubectlVersion-windows-amd64.exe"
  $checksumPath = "$downloadPath.sha256"

  if ((Test-Path $kubectlPath) -and -not $Force) {
    Download-File -Url "$url.sha256" -OutFile $checksumPath
    Assert-Sha256 -File $kubectlPath -ChecksumFile $checksumPath
    Write-ToolOk "kubectl already exists and is verified: $kubectlPath"
    return
  }

  Download-File -Url $url -OutFile $downloadPath
  Download-File -Url "$url.sha256" -OutFile $checksumPath
  Assert-Sha256 -File $downloadPath -ChecksumFile $checksumPath
  Move-Item -LiteralPath $downloadPath -Destination $kubectlPath -Force
  Write-ToolOk "kubectl installed: $kubectlPath"
}

function Ensure-Helm() {
  Ensure-Directory -Path $downloadDir
  $zipPath = Join-Path $downloadDir "helm-$HelmVersion-windows-amd64.zip"
  $checksumPath = "$zipPath.sha256sum"
  $extractPath = Join-Path $downloadDir "helm-$HelmVersion"
  $url = "https://get.helm.sh/helm-$HelmVersion-windows-amd64.zip"

  Download-File -Url $url -OutFile $zipPath
  Download-File -Url "$url.sha256sum" -OutFile $checksumPath
  Assert-Sha256 -File $zipPath -ChecksumFile $checksumPath
  Remove-ToolDirectory -Path $extractPath
  Expand-Archive -LiteralPath $zipPath -DestinationPath $extractPath -Force

  $archiveHelmPath = Join-Path $extractPath "windows-amd64\helm.exe"
  if ((Test-Path $helmPath) -and -not $Force) {
    $expected = (Get-FileHash -Algorithm SHA256 -LiteralPath $archiveHelmPath).Hash
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $helmPath).Hash
    if ($actual -ne $expected) {
      throw "Installed Helm binary does not match the verified $HelmVersion archive. Rerun with -Force to replace it."
    }
    Write-ToolOk "helm already exists and is verified: $helmPath"
    return
  }

  Ensure-Directory -Path (Split-Path $helmPath -Parent)
  Copy-Item -LiteralPath $archiveHelmPath -Destination $helmPath -Force
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

Assert-ReleaseVersion -Name "kind" -Version $KindVersion
Assert-ReleaseVersion -Name "kubectl" -Version $KubectlVersion
Assert-ReleaseVersion -Name "Helm" -Version $HelmVersion
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
