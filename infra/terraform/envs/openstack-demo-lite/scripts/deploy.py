"""First-boot installation of the pinned, manually verified core demo profile."""
import json
from pathlib import Path
import secrets
import subprocess
import yaml

ROOT = Path("/opt/demo")
SOURCE = ROOT / "source"
K = ["k3s", "kubectl"]

def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)

def kubectl(*args, **kwargs):
    return run(K + list(args), **kwargs)

def apply(docs):
    kubectl("apply", "-f", "-", input=yaml.safe_dump_all(docs, sort_keys=False))

def wait(kind, name):
    if kind == "job":
        kubectl("wait", "-n", "messaging-app", "--for=condition=complete",
                "job/" + name, "--timeout=600s")
    else:
        kubectl("rollout", "status", "-n", "messaging-app",
                kind + "/" + name, "--timeout=600s")

values = (SOURCE / "k8s/values/postgresql-lite-values.yaml").read_text()
apply([{
    "apiVersion": "helm.cattle.io/v1", "kind": "HelmChart",
    "metadata": {"name": "messaging-postgresql-ha", "namespace": "kube-system"},
    "spec": {
        "repo": "https://charts.bitnami.com/bitnami", "chart": "postgresql-ha",
        "version": "16.3.2", "targetNamespace": "messaging-app",
        "createNamespace": True, "timeout": "15m", "failurePolicy": "abort",
        "valuesContent": values,
    },
}])
# The Helm controller creates its Job asynchronously.
import time
for attempt in range(120):
    result = subprocess.run(
        K + ["get", "job", "helm-install-messaging-postgresql-ha", "-n", "kube-system"],
        capture_output=True,
    )
    if result.returncode == 0:
        break
    time.sleep(5)
else:
    raise RuntimeError("Helm install job was not created")
kubectl("wait", "-n", "kube-system", "--for=condition=complete",
        "job/helm-install-messaging-postgresql-ha", "--timeout=900s")
wait("statefulset", "messaging-postgresql-ha-postgresql")
wait("deployment", "messaging-postgresql-ha-pgpool")

rendered = kubectl("kustomize", str(SOURCE / "k8s/gitops/overlays/demo-lite-k3s"),
                   capture_output=True).stdout
docs = [d for d in yaml.safe_load_all(rendered) if d]
def select(wanted):
    selected = [d for d in docs if (d["kind"], d["metadata"]["name"]) in wanted]
    if len(selected) != len(wanted):
        raise RuntimeError("Missing or duplicate resources: " + str(wanted))
    return selected

apply(select({("Service", "kafka"), ("Service", "kafka-headless"),
              ("StatefulSet", "kafka"), ("Job", "kafka-topic-bootstrap")}))
wait("statefulset", "kafka")
wait("job", "kafka-topic-bootstrap")
apply(select({("Secret", "messaging-env"), ("Job", "messaging-schema-migration")}))
wait("job", "messaging-schema-migration")

apply([{
    "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
    "metadata": {"name": "messaging-runtime-secrets", "namespace": "messaging-app"},
    "stringData": {
        "AUTH_SECRET_KEY": secrets.token_urlsafe(48),
        "ACCESS_TOKEN_TTL_SECONDS": "3600",
        "GRAFANA_ADMIN_USER": "admin",
        "GRAFANA_ADMIN_PASSWORD": secrets.token_urlsafe(24),
    },
}])
admin = """
set -eu
if [ -n "${POSTGRES_POSTGRES_PASSWORD_FILE:-}" ] && [ -r "$POSTGRES_POSTGRES_PASSWORD_FILE" ]; then
  PGPASSWORD="$(cat "$POSTGRES_POSTGRES_PASSWORD_FILE")"
elif [ -r /opt/bitnami/postgresql/secrets/postgres-password ]; then
  PGPASSWORD="$(cat /opt/bitnami/postgresql/secrets/postgres-password)"
elif [ -n "${POSTGRES_POSTGRES_PASSWORD:-}" ]; then
  PGPASSWORD="$POSTGRES_POSTGRES_PASSWORD"
else
  exit 41
fi
export PGPASSWORD
exec /opt/bitnami/postgresql/bin/psql -X -v ON_ERROR_STOP=1 -U postgres -d postgres -c 'GRANT pg_monitor TO portfolio;'
"""
kubectl("exec", "-i", "-n", "messaging-app",
        "messaging-postgresql-ha-postgresql-0", "--", "sh", "-s", input=admin)

workers = {"worker", "notification-worker", "dlq-replayer"}
apply(select({(kind, name) for kind in ("Deployment", "Service") for name in workers}))
for name in sorted(workers):
    wait("deployment", name)
apply(select({("Deployment", "api"), ("Service", "api"), ("Service", "api-metrics")}))
wait("deployment", "api")
apply([{
    "apiVersion": "v1", "kind": "Service",
    "metadata": {"name": "demo-http", "namespace": "messaging-app"},
    "spec": {"type": "NodePort", "selector": {"app": "api"},
             "ports": [{"name": "http", "port": 8000, "targetPort": 8000,
                        "nodePort": 30080}]},
}])
