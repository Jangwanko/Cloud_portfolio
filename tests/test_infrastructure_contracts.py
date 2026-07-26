from pathlib import Path
import hashlib


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


def _config_hash(*relative_paths: str) -> str:
    combined = "\0".join(_read(path).replace("\r\n", "\n") for path in relative_paths)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


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
    assert _literal_block(manifest, "datasource.yml") == _read(
        "monitoring/grafana/provisioning/datasources/datasource.yml"
    )
    assert _literal_block(manifest, "dashboard.yml") == _read(
        "monitoring/grafana/provisioning/dashboards/dashboard.yml"
    )

    prometheus_hash = _config_hash(
        "monitoring/prometheus/prometheus.yml",
        "monitoring/prometheus/alerts.yml",
    )
    grafana_hash = _config_hash(
        "monitoring/grafana/provisioning/datasources/datasource.yml",
        "monitoring/grafana/provisioning/dashboards/dashboard.yml",
        "monitoring/grafana/dashboards/messaging-overview.json",
    )
    assert f'portfolio.jangwanko.dev/config-hash: "{prometheus_hash}"' in manifest
    assert f'portfolio.jangwanko.dev/config-hash: "{grafana_hash}"' in manifest


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


def test_linux_recovery_flows_restore_persisted_postgresql_sync_configuration() -> None:
    helper = _read("scripts/configure_postgres_sync.sh")
    quick_start = _read("scripts/quick_start_all.sh")
    db_recovery = _read("scripts/test_db_down.sh")

    assert "SELECT NOT pg_is_in_recovery();" in helper
    assert "ALTER SYSTEM SET synchronous_standby_names" in helper
    assert "ALTER SYSTEM SET synchronous_commit = 'on'" in helper
    assert 'SYNCHRONOUS_STANDBY_NAMES="ANY 1 (*)"' in helper
    assert "SELECT pg_reload_conf();" in helper
    assert "sync_state IN ('sync', 'quorum')" in helper
    assert "Streaming synchronous standby count" in helper
    assert 'POSTGRES_STATEFULSET="${POSTGRES_STATEFULSET:-messaging-postgresql-ha-postgresql}"' in helper
    assert "{.spec.replicas}" in helper
    assert "{.status.readyReplicas}" in helper
    assert "index < expected_replicas" in helper
    assert 'postgres_pods+="${POSTGRES_STATEFULSET}-${index} "' in helper
    assert "POSTGRES_POSTGRES_PASSWORD_FILE" in helper
    assert "kubectl -n \"$NAMESPACE\" get secret" not in helper
    assert "postgres_password" not in helper
    assert "set -x" not in helper

    quick_start_configure = 'bash "$ROOT_DIR/scripts/configure_postgres_sync.sh"'
    assert quick_start_configure in quick_start
    assert quick_start.index("helm upgrade --install messaging-postgresql-ha") < quick_start.index(
        quick_start_configure
    ) < quick_start.rindex("\ngrant_pg_monitor\n")
    assert "POSTGRES_POSTGRES_PASSWORD_FILE" in quick_start
    assert "base64 -d" not in quick_start
    assert "postgres_password" not in quick_start

    assert "db_was_scaled_down=true" in db_recovery
    assert "wait_workload_replicas" in db_recovery
    assert "ready=\"${ready:-0}\"" in db_recovery
    assert db_recovery.count('wait_workload_replicas "$db_ref" "$target_replicas"') == 2
    assert "POSTGRES_SYNC_TIMEOUT_SEC=60 configure_postgres_sync" in db_recovery
    assert db_recovery.rindex('rollout status "$db_ref"') < db_recovery.rindex(
        "\nconfigure_postgres_sync\n"
    ) < db_recovery.index("wait_db_query 240")


def test_powershell_postgresql_recovery_restores_sync_replication_safely() -> None:
    configure = _read("scripts/configure_postgres_sync.ps1")
    install = _read("k8s/scripts/install-ha.ps1")
    recovery_scripts = [
        _read("scripts/reset_k8s_state.ps1"),
        _read("scripts/test_db_down.ps1"),
        _read("scripts/test_cache_read_fallback.ps1"),
    ]

    assert ".status.readyReplicas" in configure
    assert "foreach ($pod in $expectedPods)" in configure
    assert "ALTER SYSTEM SET synchronous_commit = 'on';" in configure
    assert "ALTER SYSTEM SET synchronous_standby_names" in configure
    assert "ANY 1 (" in configure
    assert "SELECT pg_reload_conf();" in configure
    assert "current_setting('synchronous_commit')" in configure
    assert "current_setting('synchronous_standby_names')" in configure
    assert "pg_is_in_recovery()" in configure
    assert "state = 'streaming'" in configure
    assert "sync_state IN ('sync', 'quorum')" in configure
    assert "POSTGRES_POSTGRES_PASSWORD_FILE" in configure
    assert "/opt/bitnami/postgresql/secrets/postgres-password" in configure
    assert "PostgresShellBase64" in configure
    assert "base64 -d | bash" in configure
    assert "kubectl -n $Namespace get secret" not in configure
    assert configure.index(".status.readyReplicas") < configure.index(
        "foreach ($pod in $expectedPods)"
    )

    configured_pods = configure.split("foreach ($pod in $expectedPods)", 1)[1].split(
        "$expectedPodSql", 1
    )[0]
    assert configured_pods.index("ALTER SYSTEM SET synchronous_commit") < configured_pods.index(
        "ALTER SYSTEM SET synchronous_standby_names"
    ) < configured_pods.index("SELECT pg_reload_conf()")

    assert "configure_postgres_sync.ps1" in install
    assert install.index("--wait --timeout 15m") < install.index(
        "configure_postgres_sync.ps1"
    ) < install.index(
        "Grant-PostgresMonitorRole -Namespace"
    )
    assert "/opt/bitnami/postgresql/secrets/postgres-password" in install
    assert "SQL_BASE64" in install
    assert "PostgresAdminShellBase64" in install
    assert "base64 -d | bash" in install
    for forbidden in (
        "Decode-Base64",
        "encodedPassword",
        "postgresPassword",
        "get secret messaging-postgresql-ha-postgresql",
    ):
        assert forbidden not in install

    for recovery in recovery_scripts:
        assert "configure_postgres_sync.ps1" in recovery

    reset_script = _read("scripts/reset_k8s_state.ps1")
    assert "Failed to scale $ref to $Replicas replicas" in reset_script
    assert "Timed out or failed waiting for $ref rollout" in reset_script


def test_backup_restore_clients_do_not_expose_or_reencode_database_credentials() -> None:
    backup = _read("scripts/backup_postgres_k8s.ps1")
    restore = _read("scripts/restore_postgres_k8s.ps1")

    for source in (backup, restore):
        assert "secretKeyRef" in source
        assert 'name = "messaging-postgresql-ha-postgresql"' in source
        assert 'key = "password"' in source
        assert "automountServiceAccountToken = $false" in source
        assert "runAsNonRoot = $true" in source
        assert "allowPrivilegeEscalation = $false" in source
        assert "ConvertTo-Json -Depth 12 -Compress | kubectl create -f -" in source
        assert "FromBase64String" not in source
        assert "PGPASSWORD=$password" not in source
        assert "get secret messaging-postgresql-ha-postgresql" not in source

    assert "--file=/tmp/postgres-backup.sql" in backup
    assert 'kubectl cp "$Namespace/$backupPod`:/tmp/postgres-backup.sql"' in backup
    assert 'kubectl cp $backupName "$Namespace/$restorePod`:/tmp/postgres-restore.sql"' in restore
    assert "--file=/tmp/postgres-restore.sql" in restore
    assert "Get-Content -LiteralPath $resolvedBackupFile -Raw" not in restore
    reset_block = restore.split("if ($ResetSchema)", 1)[1].split(
        "--file=/tmp/postgres-restore.sql", 1
    )[0]
    assert 'if ($LASTEXITCODE -ne 0)' in reset_block
    assert "Failed to reset the public schema before restore" in reset_block


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


def test_api_hpa_avoids_cache_hydration_scale_out_storms() -> None:
    for path in (
        "k8s/app/manifests-ha.yaml",
        "k8s/gitops/base/manifests-ha.yaml",
    ):
        manifest = _read(path)
        hpa = manifest.split("name: api-hpa", 1)[1].split("---", 1)[0]
        assert "minReplicas: 6" in hpa
        assert "stabilizationWindowSeconds: 60" in hpa
        assert "type: Pods" in hpa
        assert "value: 2" in hpa
        assert "stabilizationWindowSeconds: 120" in hpa


def test_destructive_benchmark_reset_is_explicit_and_restores_workers() -> None:
    reset = _read("scripts/reset_kafka_benchmark_state.ps1")
    suite = _read("scripts/run_kafka_performance_suite.ps1")
    runner = _read("scripts/test_k6_load.ps1")
    load = _read("scripts/load_test_k6.js")
    job = _read("k8s/app/k6-job.yaml")
    fixed_evidence = _read("results/kafka-performance/worker-ab-fixed.txt")
    keda_evidence = _read("results/kafka-performance/worker-ab-keda.txt")

    assert "[switch]$ConfirmDataLoss" in reset
    assert "if (-not $ConfirmDataLoss)" in reset
    assert 'autoscaling.keda.sh/paused-replicas="0"' in reset
    assert "autoscaling.keda.sh/paused-replicas-" in reset
    assert "_reset_demo_event_data" in reset
    assert "cleanup.policy" in reset
    assert "[int]$KafkaCleanupQuietSec = 75" in reset
    assert "Start-Sleep -Seconds $KafkaCleanupQuietSec" in reset
    assert '"rollout", "restart", "deployment/api"' in reset
    assert "Assert-KubectlSuccess" in reset
    assert "Invoke-CleanupKubectl" in reset
    assert "Cleanup failures:" in reset
    assert "[switch]$CleanBenchmarkState" in suite
    assert "reset_kafka_benchmark_state.ps1" in suite
    assert "-ConfirmDataLoss" in suite
    assert '[ValidateSet("keda", "fixed")]' in suite
    assert "Set-WorkerScalingExperimentMode" in suite
    assert "Restore-WorkerScaling" in suite
    assert "$scalingRestoreError" in suite
    assert "K6StreamCount" in suite
    assert "K6StreamCount" in runner
    assert "K6_STREAM_COUNT" in load
    assert "data.streamIds[streamIndex]" in load
    assert "name: K6_STREAM_COUNT" in job
    for evidence in (fixed_evidence, keda_evidence):
        assert "k6_stream_count: 64" in evidence
        assert "Error rate         : 0.00%" in evidence
        assert "main_load_message_worker_final_consumer_lag: 0" in evidence
        assert "main_load_notification_worker_final_consumer_lag: 0" in evidence
    assert "worker_scaling_mode: fixed" in fixed_evidence
    assert "main_load_all_consumer_backlog_drain_seconds: 301.42" in fixed_evidence
    assert "worker_scaling_mode: keda" in keda_evidence
    assert "main_load_all_consumer_backlog_drain_seconds: 261.17" in keda_evidence
    assert "worker-keda-hpa   Deployment/worker" in keda_evidence


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
    assert dashboard.count("clamp_min(kafka_consumergroup_lag") == 1
    assert "sum by (consumergroup)" in dashboard
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
