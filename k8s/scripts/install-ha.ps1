$ErrorActionPreference = "Stop"

helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

kubectl create namespace messaging --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install messaging-postgresql-ha bitnami/postgresql-ha `
  -n messaging `
  -f k8s/values/postgresql-ha-values.yaml

helm upgrade --install messaging-redis bitnami/redis `
  -n messaging `
  -f k8s/values/redis-ha-values.yaml

kubectl get pods -n messaging
