#!/bin/bash
set -euo pipefail
umask 077
exec > >(tee -a /var/log/demo-bootstrap.log) 2>&1
trap 'echo FAILED > /opt/demo/status' ERR
echo RUNNING > /opt/demo/status
git clone --no-checkout https://github.com/Jangwanko/Cloud_portfolio.git /opt/demo/source
git -C /opt/demo/source checkout --detach "$1"
test "$(git -C /opt/demo/source rev-parse HEAD)" = "$1"
curl --fail --location --retry 3 https://get.k3s.io -o /opt/demo/install-k3s.sh
INSTALL_K3S_VERSION="$2" sh /opt/demo/install-k3s.sh server --disable traefik --disable servicelb --write-kubeconfig-mode 600
for attempt in $(seq 1 60); do
  if k3s kubectl get nodes >/dev/null 2>&1; then break; fi
  sleep 5
done
k3s kubectl wait --for=condition=Ready nodes --all --timeout=300s
python3 /opt/demo/deploy.py
k3s kubectl exec -i -n messaging-app deployment/api -- python - < /opt/demo/smoke.py
echo PASS > /opt/demo/status
