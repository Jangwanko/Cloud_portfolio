$ErrorActionPreference = "Stop"

kind create cluster --name messaging-ha
kubectl cluster-info
kubectl create namespace messaging --dry-run=client -o yaml | kubectl apply -f -
