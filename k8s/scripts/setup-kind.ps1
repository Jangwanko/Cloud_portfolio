$ErrorActionPreference = "Stop"

function Resolve-KindPath {
  $cmd = Get-Command kind -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }

  $local = Join-Path $PSScriptRoot "..\..\tools\kind.exe"
  $localResolved = Resolve-Path $local -ErrorAction SilentlyContinue
  if ($localResolved) {
    return $localResolved.Path
  }

  throw "kind executable not found. Install kind or place tools/kind.exe"
}

$kind = Resolve-KindPath
$kindConfig = Join-Path $PSScriptRoot "..\kind-config.yaml"

if (Test-Path $kindConfig) {
  & $kind create cluster --name messaging-ha --config $kindConfig
} else {
  & $kind create cluster --name messaging-ha
}

kubectl cluster-info
kubectl create namespace messaging-app --dry-run=client -o yaml | kubectl apply -f -
& "$PSScriptRoot/install-metrics-server.ps1"
