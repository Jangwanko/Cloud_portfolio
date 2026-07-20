from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8-sig")


def _literal_block(document: str, key: str) -> str:
    marker = f"  {key}: |\n"
    try:
        remainder = document.split(marker, 1)[1]
    except IndexError as exc:
        raise AssertionError(f"missing YAML literal block: {key}") from exc

    lines: list[str] = []
    for line in remainder.splitlines():
        if line and not line.startswith("    "):
            break
        lines.append(line[4:] if line.startswith("    ") else "")
    return "\n".join(lines).rstrip() + "\n"


def test_generated_manifest_copy_stays_identical() -> None:
    assert _read("k8s/app/manifests-ha.yaml") == _read(
        "k8s/gitops/base/manifests-ha.yaml"
    )


def test_monitoring_sources_match_embedded_configmaps() -> None:
    manifest = _read("k8s/gitops/base/manifests-ha.yaml")
    assert _literal_block(manifest, "prometheus.yml") == _read(
        "monitoring/prometheus/prometheus.yml"
    )
    assert _literal_block(manifest, "alerts.yml") == _read(
        "monitoring/prometheus/alerts.yml"
    )
    assert _literal_block(manifest, "messaging-overview.json") == _read(
        "monitoring/grafana/dashboards/messaging-overview.json"
    )


def test_local_ha_uses_immutable_registry_tag_workflow() -> None:
    overlay = _read("k8s/gitops/overlays/local-ha/kustomization.yaml")
    workflow = _read(".github/workflows/ci.yml")
    assert "newName: ghcr.io/jangwanko/cloud_portfolio" in overlay
    assert "newTag:" in overlay
    assert "needs: validate" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "[skip image publish]" in workflow
    assert "docker/build-push-action@" in workflow
    assert '"${REPOSITORY}:master-bootstrap"' in workflow
    assert "Build and push candidate image" in workflow
    assert "Verify published candidate digest" in workflow
    assert "docker buildx imagetools create" in workflow
    assert '${REPOSITORY}@${DIGEST}' in workflow
    assert "- demo-lite" in workflow
    assert "github.ref == 'refs/heads/master'" in workflow


def test_gitops_quick_start_uses_the_registry_image() -> None:
    quick_start = _read("scripts/quick_start_gitops.ps1")
    bootstrap = _read("k8s/scripts/bootstrap-argocd-app.ps1")
    assert "Assert-RegistryImageAvailable" in quick_start
    assert "docker build" not in quick_start
    assert "load docker-image" not in quick_start
    assert "ImageRepository" in quick_start
    assert "ImageTag" in quick_start
    assert "committed remote local-ha overlay" in quick_start
    assert "messaging-portfolio=${ImageRepository}:${ImageTag}" in bootstrap


def test_local_quick_starts_do_not_publish_fixed_admin_passwords() -> None:
    windows = _read("scripts/quick_start_all.ps1")
    linux = _read("scripts/quick_start_all.sh")
    runtime_secret = _read("k8s/scripts/install-runtime-secrets.ps1")
    for source in (windows, linux, runtime_secret):
        assert "1q2w3e4r" not in source
    assert "secrets.token_urlsafe" in linux
    assert 'GrafanaAdminPassword = ""' in runtime_secret


def test_windows_quick_start_uses_the_declared_namespace_parameter() -> None:
    script = _read("scripts/quick_start_all.ps1")

    assert "Wait-NamespacedDeployment -Name $Name -NamespaceToUse $Namespace" in script
    assert "Wait-NamespacedDeployment -Name $Name -Namespace $Namespace" not in script


def test_tool_bootstraps_verify_official_sha256_sidecars() -> None:
    windows = _read("scripts/bootstrap_tools.ps1")
    linux = _read("scripts/install_linux_prereqs.sh")
    attributes = _read(".gitattributes")

    assert "function Assert-Sha256" in windows
    assert "Get-FileHash -Algorithm SHA256" in windows
    assert 'Download-File -Url "$url.sha256"' in windows
    assert 'Download-File -Url "$url.sha256sum"' in windows
    assert windows.count("Assert-Sha256 -File") >= 5
    assert "archiveHelmPath" in windows
    assert "helm already exists and is verified" in windows

    assert 'HELM_VERSION="${HELM_VERSION:-v3.21.3}"' in linux
    assert "verify_sha256()" in linux
    assert linux.count("verify_sha256 ") == 3
    assert 'curl -fsSL -o "$checksum" "$url.sha256"' in linux
    assert 'curl -fsSL -o "$checksum" "$url.sha256sum"' in linux
    assert 'tar -xzf "$archive"' in linux
    assert "raw.githubusercontent.com/helm/helm" not in linux
    assert "| bash" not in linux
    assert 'installed_version="$(kind_version "$(command -v kind)" || true)"' in linux
    assert 'installed_version="$(kubectl_version "$(command -v kubectl)" || true)"' in linux
    assert 'installed_version="$(helm_version "$(command -v helm)" || true)"' in linux
    assert linux.count("PATH does not resolve to that version") == 3
    assert "*.sh text eol=lf" in attributes


def test_postgresql_credentials_are_chart_managed_and_injected_by_secret_key() -> None:
    values = _read("k8s/values/postgresql-ha-values.yaml")
    manifest = _read("k8s/gitops/base/manifests-ha.yaml")
    secret_ref = """            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: messaging-postgresql-ha-postgresql
                  key: password"""

    assert "password: portfolio" not in values
    assert "repmgrPassword:" not in values
    assert "adminPassword:" not in values
    assert 'DB_PASSWORD: "portfolio"' not in manifest
    assert manifest.count(secret_ref) == 4


def test_postgresql_chart_requests_one_of_two_synchronous_standbys() -> None:
    values = _read("k8s/values/postgresql-ha-values.yaml")

    assert "syncReplication: false" in values
    assert "syncReplicationMode: ANY" in values
    assert "name: POSTGRESQL_NUM_SYNCHRONOUS_REPLICAS" in values
    assert 'value: "1"' in values
    assert "numSynchronousReplicas:" not in values


def test_application_and_alembic_defaults_do_not_embed_a_database_password() -> None:
    config = _read("portfolio/config.py")
    alembic_ini = _read("alembic.ini")

    assert 'os.getenv("DB_PASSWORD", "")' in config
    assert "portfolio:portfolio@" not in alembic_ini


def test_http_body_limit_is_explicit_in_runtime_and_operator_contracts() -> None:
    env_example = _read(".env.example")
    manifest = _read("k8s/gitops/base/manifests-ha.yaml")
    requirements = _read("docs/SERVICE_REQUIREMENTS.md")

    assert "REQUEST_BODY_MAX_BYTES=1048576" in env_example
    assert 'REQUEST_BODY_MAX_BYTES: "1048576"' in manifest
    assert "declared/chunked body" in requirements
    assert "413" in requirements


def test_quick_starts_install_postgresql_before_application_workloads() -> None:
    windows = _read("scripts/quick_start_all.ps1")
    linux = _read("scripts/quick_start_all.sh")
    gitops = _read("scripts/quick_start_gitops.ps1")

    assert windows.index("install-ha.ps1") < windows.index(
        'Invoke-Step "Applying application manifests"'
    )
    assert linux.index("helm upgrade --install messaging-postgresql-ha") < linux.index(
        'log "Applying application manifests"'
    )
    assert gitops.index("install-ha.ps1") < gitops.index("Installing Argo CD")


def test_application_image_and_pods_run_non_root() -> None:
    dockerfile = _read("Dockerfile")
    manifest = _read("k8s/gitops/base/manifests-ha.yaml")
    assert "USER 10001:10001" in dockerfile
    assert manifest.count("automountServiceAccountToken: false") >= 8
    assert manifest.count("runAsNonRoot: true") >= 7
    assert "readOnlyRootFilesystem: true" in manifest
    assert 'drop: ["ALL"]' in manifest


def test_prometheus_discovers_every_worker_replica_and_notification_lag() -> None:
    prometheus = _read("monitoring/prometheus/prometheus.yml")
    alerts = _read("monitoring/prometheus/alerts.yml")
    manifest = _read("k8s/gitops/base/manifests-ha.yaml")
    dashboard = _read("monitoring/grafana/dashboards/messaging-overview.json")
    assert "dns_sd_configs:" in prometheus
    assert "api-metrics.messaging-app.svc.cluster.local" in prometheus
    assert "notification-worker.messaging-app.svc.cluster.local" in prometheus
    assert "MessagingNotificationConsumerLagHigh" in alerts
    assert alerts.count("sum(clamp_min(kafka_consumergroup_lag") >= 3
    assert "sum(kafka_consumergroup_lag" not in alerts
    assert "--group.filter=(message-.*|notification-worker)" in manifest
    assert manifest.count("sum(clamp_min(kafka_consumergroup_lag") >= 3
    assert "sum(kafka_consumergroup_lag" not in manifest
    assert "message-worker|notification-worker" in dashboard
    assert dashboard.count("clamp_min(kafka_consumergroup_lag") >= 2
    assert "sum by (consumergroup, topic) (kafka_consumergroup_lag" not in dashboard
    assert "sum by (consumergroup) (kafka_consumergroup_lag" not in dashboard


def test_argocd_scope_and_replica_ignores_are_minimal() -> None:
    project = _read("k8s/argocd/project-messaging-portfolio.yaml")
    application = _read(
        "k8s/argocd/application-messaging-portfolio-local-ha.example.yaml"
    )
    base = _read("k8s/gitops/base/kustomization.yaml")
    namespace = _read("k8s/gitops/base/namespace.yaml")
    assert '"*"' not in project
    assert "\n    - namespace: argocd" not in project
    assert "name: api" in application
    assert "name: worker" in application
    assert "kind: Namespace" not in _read("k8s/gitops/base/manifests-ha.yaml")
    assert "  - namespace.yaml" in base
    assert "kind: Namespace" in namespace
    assert "name: messaging-app" in namespace
    assert "argocd.argoproj.io/sync-options: Prune=false" in namespace


def test_terraform_uses_consistent_password_and_supported_versions() -> None:
    rds = _read("infra/terraform/modules/rds_postgres/main.tf")
    eks = _read("infra/terraform/modules/eks/main.tf")
    variables = _read("infra/terraform/envs/dev/variables.tf")
    ecr = _read("infra/terraform/modules/ecr/main.tf")
    lockfile = _read("infra/terraform/envs/dev/.terraform.lock.hcl")
    assert "manage_master_user_password = false" in rds
    assert "password                    = random_password.db.result" in rds
    assert 'engine_version       = "16.14"' in rds
    assert "cluster_endpoint_private_access" in eks
    assert "enable_cluster_creator_admin_permissions" in eks
    assert 'default     = "1.36"' in variables
    assert 'default     = "3.9.x"' in variables
    assert 'image_tag_mutability = "IMMUTABLE"' in ecr
    provider_blocks = lockfile.split('\nprovider "')[1:]
    assert len(provider_blocks) == 6
    assert all(block.split("\n}", 1)[0].count('"h1:') == 2 for block in provider_blocks)


def test_verified_toolchain_pins_stay_aligned() -> None:
    dockerfile = _read("Dockerfile")
    python_version = _read(".python-version").strip()
    workflow = _read(".github/workflows/ci.yml")
    windows = _read("scripts/bootstrap_tools.ps1")
    linux = _read("scripts/install_linux_prereqs.sh")

    assert dockerfile.startswith(
        "FROM python:3.11.15-slim-bookworm@sha256:"
        "f5cf0344c9886ff24d34797578d5d7dd6e8911ae0fe5962bb55d0f89603ec361\n"
    )
    assert python_version == "3.11.15"
    assert 'python-version: "3.11.15"' in workflow
    assert 'version: "v3.21.3"' in workflow
    assert '[string]$HelmVersion = "v3.21.3"' in windows
    assert 'HELM_VERSION="${HELM_VERSION:-v3.21.3}"' in linux
    assert 'version: "v1.36.2"' in workflow
    assert '[string]$KubectlVersion = "v1.32.2"' in windows
    assert 'KUBECTL_VERSION="${KUBECTL_VERSION:-v1.32.2}"' in linux
