from pathlib import Path
import json
import re


ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class TestOperationalDocumentation:
    def test_service_requirements_define_user_slo_and_operating_purpose(self):
        requirements = read_text("docs/SERVICE_REQUIREMENTS.md")
        readme = read_text("README.md")
        architecture = read_text("docs/ARCHITECTURE.md")
        reliability = read_text("docs/RELIABILITY_POLICY.md")
        repository_structure = read_text("docs/REPOSITORY_STRUCTURE.md")
        test_results = read_text("docs/TEST_RESULTS.md")

        for token in (
            "Reliable Event Processing System",
            "domain-neutral typed event acceptance",
            "`POST /v2/streams/{stream_id}/events`",
            "reference scenario",
            "사용자와 관심사",
            "기능 요구",
            "비기능 요구",
            "SLO 가드레일",
            "API 5xx ratio",
            "accepted-to-persisted p95",
            "Kafka topic wait p95",
            "DLQ oldest age",
            "oldest_sample_age_seconds",
            "unresolved SLO 제외",
            "stream_id",
            "Worker inline retry",
            "Kafka DLQ topic",
            "Argo CD `Synced / Healthy`",
        ):
            assert token in requirements

        for document in (readme, architecture, reliability, repository_structure, test_results):
            assert "SERVICE_REQUIREMENTS.md" in document

        assert "Kafka 기반 고신뢰 이벤트 처리 시스템" in readme
        assert "reference scenario built on the generic event contract" in readme
        assert "## 핵심 요약 / Executive Summary" in readme
        assert "## AWS Migration Blueprint" in readme
        assert "## Trade-offs" in readme
        assert "서비스 문제" in architecture
        assert "서비스 기준" in architecture

    def test_readme_is_interview_friendly_about_boundary_and_tradeoffs(self):
        readme = read_text("README.md")

        for token in (
            "## 핵심 요약 / Executive Summary",
            "## Kubernetes 설계 / Kubernetes Architecture",
            "## Pod 구성 / Workload Inventory",
            "## AWS Migration Blueprint",
            "## 관측 설계 / Observability Map",
            "## STAR 운영 문제 해결 경험 / Operational STAR Cases",
            "STAR 1 — HPA scale-out과 cache hydration 경합 해결",
            "STAR 2 — KEDA scale-out의 병목 이동 확인",
            "STAR 3 — GitOps namespace prune 사고 복구",
            "STAR 4 — CPU HPA에서 queue-depth KEDA로 전환",
            "STAR 5 — Pgpool HA와 same-stream ordering 보강",
            "`5,434` requests, p95 `8,175ms`",
            "`19,528` requests·p95 `1,954ms`",
            "`31,710` requests, p95 `86.95ms`",
            "API → Kafka → Worker → PostgreSQL",
            "### 현재 검증 수치 / Current Evidence",
            "Current generic v2 recovery candidate",
            "Historical Kafka intake baseline",
            "Historical baseline보다 event 수 `7.92%` 낮고 p95 `25.57%` 높습니다",
            "## Demo",
            "### Public demo-lite",
            "## Validation Summary",
            "31,676",
            "same-stream ordering: `100/100`",
            "## Trade-offs",
            "Kafka append-first intake",
            "## Next Improvements",
            "transactional outbox",
            "consumer group rebalance",
        ):
            assert token in readme

        architecture_index = readme.index("## Kubernetes 설계 / Kubernetes Architecture")
        inventory_index = readme.index("## Pod 구성 / Workload Inventory")
        aws_index = readme.index("## AWS Migration Blueprint")
        observability_index = readme.index("## 관측 설계 / Observability Map")
        star_index = readme.index("## STAR 운영 문제 해결 경험 / Operational STAR Cases")
        assert architecture_index < inventory_index < aws_index < observability_index < star_index

    def test_db_snapshot_materialized_cache_is_declared(self):
        config = read_text("portfolio/config.py")
        kafka_client = read_text("portfolio/kafka_client.py")
        cache = read_text("portfolio/materialized_cache.py")
        api = read_text("portfolio/api.py")
        main = read_text("portfolio/main.py")
        worker = read_text("worker/main.py")
        kafka_bootstrap = read_text("k8s/gitops/base/kafka-ha.yaml")
        app_manifest = read_text("k8s/app/manifests-ha.yaml")
        cache_read_test = read_text("scripts/test_cache_read_fallback.ps1")

        assert "kafka_request_status_topic" in config
        assert "kafka_message_snapshot_topic" in config
        assert "kafka_stream_snapshot_topic" in config
        assert "kafka_notification_topic" in config
        assert "snapshot_cache_fresh_seconds" in config
        assert "publish_request_status" in kafka_client
        assert "publish_message_snapshot" in kafka_client
        assert "publish_stream_snapshot" in kafka_client
        assert "publish_notification_job" in kafka_client
        assert "build_notification_consumer" in kafka_client
        assert "build_materialized_cache_consumer" in kafka_client
        assert "get_cached_request_status" in cache
        assert "list_cached_events" in cache
        assert "is_cached_stream_member" in cache
        assert "start_materialized_cache" in cache
        assert "get_cached_request_status(request_id)" in api
        assert "list_cached_events(stream_id, limit, before_id)" in api
        assert "_cached_page_matches_stream_watermark" in api
        assert "snapshot_cache_fresh_seconds" in api
        assert "response_model=EventListResponse" in api
        assert "API started without PostgreSQL startup readiness" in main
        assert "PostgreSQL startup retry failed" in main
        assert "start_materialized_cache()" in main
        assert "publish_request_status(request_id, payload)" in worker
        assert "publish_message_snapshot" in worker
        assert "publish_notification_job(response[\"room_id\"], notification_attempt_payload(response))" in worker
        assert "run_kafka_notification_loop" in worker
        assert "Wait-FreshCacheRead" in cache_read_test
        assert "Expected degraded cache read while DB is down" in cache_read_test
        assert "message-request-status" in kafka_bootstrap
        assert "message-snapshots" in kafka_bootstrap
        assert "stream-snapshots" in kafka_bootstrap
        assert "message-notifications" in kafka_bootstrap
        assert "cleanup.policy=compact" in kafka_bootstrap
        assert "KAFKA_REQUEST_STATUS_TOPIC" in app_manifest
        assert "KAFKA_MESSAGE_SNAPSHOT_TOPIC" in app_manifest
        assert "KAFKA_STREAM_SNAPSHOT_TOPIC" in app_manifest
        assert "KAFKA_NOTIFICATION_TOPIC" in app_manifest
        assert "KAFKA_NOTIFICATION_CONSUMER_GROUP" in app_manifest
        assert "name: notification-worker" in app_manifest
        assert 'value: "notification"' in app_manifest
        assert "job_name: notification-worker" in app_manifest

        architecture = read_text("docs/ARCHITECTURE.md")
        for document in (architecture,):
            assert "DB membership" in document
            assert "watermark" in document
            assert "snapshot_age_seconds" in document
            assert "degraded=true" in document
            assert "message-snapshots" in document
            assert "stream-snapshots" in document
            assert "API local materialized cache" in document

        readme = read_text("README.md")
        assert "DB membership/watermark 검증" in readme
        assert "degraded cache" in readme
        assert "Prometheus --> KEDA" not in readme
        assert "KEDA `type: kafka`" in architecture
        assert "Prometheus / kafka-exporter는 같은 lag를 운영자가 관측" in architecture
        assert "replica 증가만으로 성능 개선을 단정하지 않습니다" in architecture

    def test_read_cache_validation_and_slo_are_documented(self):
        requirements = read_text("docs/SERVICE_REQUIREMENTS.md")
        test_results = read_text("docs/TEST_RESULTS.md")
        metrics_reference = read_text("docs/METRICS_REFERENCE.md")
        observability = read_text("docs/OBSERVABILITY.md")

        for token in (
            "DB Snapshot Cache / Degraded Read 검증 절차",
            "scripts/test_cache_read_fallback.ps1",
            "stream을 생성",
            "Worker가 PostgreSQL commit",
            "message-snapshots",
            "stream-snapshots",
            "source=cache",
            "degraded=false",
            "degraded=true",
            "snapshot_age_seconds",
            "DB failure + cache miss",
            "Membership guard",
        ):
            assert token in test_results

        for token in (
            "Read cache hit ratio",
            "Snapshot age",
            "Cache rebuild time",
            "Stale response count",
            "Degraded read count",
            "Per-pod snapshot replay progress",
            "미구현 custom metric",
            "snapshot_age_seconds > 30s",
            "snapshot_age_seconds > 120s",
            "Degraded read ratio",
        ):
            assert token in requirements

        for token in (
            "Read cache operating signals",
            "Read cache hit ratio",
            "Per-pod snapshot replay progress",
            "consumer group lag와 섞어 해석하지 않습니다",
            "source=cache",
            "degraded=true",
        ):
            assert token in metrics_reference

        for token in (
            "read cache hit ratio",
            "snapshot age",
            "degraded read count",
            "pod별 hydration 상태",
            "captured initial end offset",
        ):
            assert token in observability

        for document in (requirements, metrics_reference, observability):
            assert "| Snapshot consumer lag |" not in document

    def test_architecture_docs_include_normal_and_failure_diagrams(self):
        architecture = read_text("docs/ARCHITECTURE.md")

        assert "정상 event 흐름" in architecture
        assert "장애 / DLQ 흐름" in architecture
        assert "sequenceDiagram" in architecture
        assert "inline retry" in architecture

        readme = read_text("README.md")
        assert "API → Kafka → Worker → PostgreSQL" in readme
        assert "[Architecture](docs/ARCHITECTURE.md)" in readme

    def test_operations_docs_include_dlq_and_security_policy(self):
        operations = read_text("docs/OPERATIONS.md")

        assert "## DLQ 운영 기준" in operations
        assert "DLQ_REPLAY_MAX_COUNT" in operations
        assert "## 보안 기본선" in operations
        assert ".env.example` placeholder" in operations
        assert "32-byte 미만 `AUTH_SECRET_KEY`" in operations
        assert "non-local readiness와 business API에서 차단" in operations
        assert "외부 secret manager" in operations

    def test_test_results_and_patch_notes_keep_experiment_rounds(self):
        test_results = read_text("docs/TEST_RESULTS.md")
        patch_notes = read_text("docs/PATCH_NOTES.md")
        ordering_script = read_text("scripts/ordering_failure_injection.py")
        observability = read_text("docs/OBSERVABILITY.md")
        quick_start = read_text("docs/QUICK_START.md")
        repository_structure = read_text("docs/REPOSITORY_STRUCTURE.md")

        for document in (test_results, patch_notes):
            assert "1차 실험: Kafka 이벤트 스트림 기준선" in document
            assert "2차 실험: Pgpool HA와 엄격한 stream 순서 보장" in document
            assert "2026-06-09 재실행: 정합성 재확인과 backlog drain 관측" in document
            assert "31710" in document
            assert "31676" in document
            assert "34284" in document
            assert "36394 -> 33274 -> 23563 -> 11971 -> 0" in document

        for token in (
            "Ordering / Failure Injection 검증",
            "single_no_failure",
            "multi_no_failure",
            "single_db_failure",
            "multi_db_failure",
            "A001..A100",
            "B001..B100",
            "C001..C100",
            "PostgreSQL row evidence",
            "missing `0`",
            "duplicate `0`",
            "mixed payload `0`",
            "DLQ `0`",
            "results/ordering-failure/latest.json",
            "6.125s",
            "8.438s",
            "22.969s",
            "23.703s",
            "http://127.0.0.1",
            "Host: localhost",
            "Measurement Validity",
            "old ordering / failure injection `~210s` duration",
            "invalid",
            "Kafka intake k6 baseline",
            "valid",
            "api.messaging-app.svc.cluster.local:8000",
            "2026-06-09 Kafka 정합성 검증",
            "stream_id` `30",
            "ordering-event-0001",
            "ordering-event-0100",
            "ordering `pass`",
            "stream_id` `31",
            "36394 -> 33274 -> 23563 -> 11971 -> 0",
        ):
            assert token in test_results

        for token in (
            "ordering",
            "no_loss",
            "no_duplicate",
            "no_mixed_payload",
            "dlq_empty",
            "query_persisted_events",
            "messaging-postgresql-ha-pgpool",
            'default="http://127.0.0.1"',
            'default="localhost"',
        ):
            assert token in ordering_script

        for document in (observability, quick_start, repository_structure):
            assert "ordering_failure_injection.py" in document

    def test_reproducibility_environment_is_documented(self):
        quick_start = read_text("docs/QUICK_START.md")
        test_results = read_text("docs/TEST_RESULTS.md")

        for document in (quick_start, test_results):
            assert "AMD Ryzen 5 5600" in document
            assert "12 CPU" in document
            assert "15.6GiB" in document

        assert "권장 사양보다 낮은" in quick_start
        assert "권장 사양보다 낮은" in test_results
        assert "리소스 부족 신호" in quick_start
        assert "리소스 부족 가능성" in test_results
        assert "Poison event did not reach Kafka DLQ in time" in quick_start

    def test_redis_results_are_kept_as_explicit_historical_context(self):
        architecture = read_text("docs/ARCHITECTURE.md")
        test_results = read_text("docs/TEST_RESULTS.md")
        requirements = read_text("requirements.txt").lower()
        terraform = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "infra" / "terraform").rglob("*.tf")
            if ".terraform" not in path.parts
        ).lower()

        assert "Redis queue 단계" in architecture
        assert "## Redis Historical Context" in test_results
        assert "Kafka append-first baseline, Worker Kafka KEDA 효과와 분리" in test_results
        assert "redis" not in requirements
        assert "elasticache" not in terraform

    def test_public_docs_do_not_use_stale_operating_claims(self):
        public_docs = [read_text("README.md")]
        public_docs.extend(path.read_text(encoding="utf-8") for path in (ROOT / "docs").glob("*.md"))
        combined = "\n".join(public_docs)

        blocked_terms = (
            "Kafka-only ingress path",
            "room sequence",
            "44 passed",
            "56 passed",
            "516f65fdebc5e244332fc8c02839563acb561afe",
            "ecf8f2f70cfc3778ff56d2e4957f3395f04c76ee",
            "저장소에 포함된 도구:",
            "로컬 검증용 바이너리(kind/helm 등)",
        )
        for term in blocked_terms:
            assert term not in combined
        assert "cache-first" not in combined.lower()

        assert "Kafka append-first intake" in combined
        assert "현재 작업에서 `.venv\\Scripts\\python.exe -m pytest -q`를 실행" in read_text("AGENTS.md")
        assert "status `200`" in combined
        assert "`202 Accepted`의 완료 범위는 Kafka append" in combined
        assert "plan` / `apply`는 실행하지 않았" in combined
        assert "현재 AWS에 배포된 Terraform stack은 없습니다" in combined
        assert "Worker persistence capacity 신호" in combined
        assert "DB membership/watermark 검증 뒤 fresh cache" in combined
        assert "DB-down read는 `source=cache`, `degraded=true`" in combined
        assert "`source=cache`, `degraded=true`" in combined
        assert "Worker success path transaction 통합" in combined
        assert "28839" in combined
        assert "8.08ms" in combined
        assert "29204 -> 23597 -> 15111 -> 6893 -> 0" in combined
        assert "전체 intake 기준선 대체 수치로는 사용하지 않습니다" in combined

    def test_windows_quick_start_bootstraps_local_kubernetes_tools(self):
        bootstrap = read_text("scripts/bootstrap_tools.ps1")
        quick_start_script = read_text("scripts/quick_start_all.ps1")
        readme = read_text("README.md")
        quick_start = read_text("docs/QUICK_START.md")
        repository_structure = read_text("docs/REPOSITORY_STRUCTURE.md")
        gitignore = read_text(".gitignore")

        for token in (
            "KindVersion",
            "KubectlVersion",
            "HelmVersion",
            "kubernetes-sigs/kind",
            "dl.k8s.io",
            "get.helm.sh",
            "tools",
            "kind.exe",
            "kubectl.exe",
            "helm.exe",
            "Docker Desktop is required",
        ):
            assert token in bootstrap

        assert "bootstrap_tools.ps1" in quick_start_script
        assert "bootstrap_tools.ps1" in readme
        assert "bootstrap_tools.ps1" in quick_start
        assert "bootstrap_tools.ps1" in repository_structure
        assert "Docker Desktop만 설치하고 실행" in readme
        assert "Docker Desktop만 설치하고 실행" in quick_start
        assert "tools/kubectl.exe" in gitignore
        assert "tools/downloads/" in gitignore


class TestManifestContracts:
    def test_dlq_replay_limit_is_set_in_kubernetes_manifests(self):
        app_manifest = read_text("k8s/app/manifests-ha.yaml")
        gitops_manifest = read_text("k8s/gitops/base/manifests-ha.yaml")

        for manifest in (app_manifest, gitops_manifest):
            assert 'DLQ_REPLAY_MAX_COUNT: "3"' in manifest

    def test_runtime_secret_is_used_by_app_workloads(self):
        manifest = read_text("k8s/gitops/base/manifests-ha.yaml")
        install_script = read_text("k8s/scripts/install-runtime-secrets.ps1")

        assert "messaging-runtime-secrets" in manifest
        assert "secretRef:" in manifest
        assert "AUTH_SECRET_KEY" in install_script
        assert "GRAFANA_ADMIN_PASSWORD" in install_script

    def test_dev_kafka_gitops_uses_registry_image_and_action_tag_update(self):
        kustomization = read_text("k8s/gitops/overlays/local-ha/kustomization.yaml")
        workflow = read_text(".github/workflows/ci.yml")

        assert "images:" in kustomization
        assert "name: messaging-portfolio" in kustomization
        assert "newName: ghcr.io/jangwanko/cloud_portfolio" in kustomization
        assert "newTag:" in kustomization

        assert "publish-dev-kafka-image:" in workflow
        dev_publish = workflow.split("publish-dev-kafka-image:", 1)[1].split(
            "publish-master-image:", 1
        )[0]
        assert "needs: validate" in dev_publish
        assert "github.ref == 'refs/heads/dev-kafka'" in dev_publish
        assert "ref: ${{ github.sha }}" in dev_publish
        assert "ghcr.io/jangwanko/cloud_portfolio" in workflow
        assert "[skip dev-kafka image]" in dev_publish
        assert "docker/build-push-action" in dev_publish
        assert "Verify published candidate digest" in dev_publish
        assert "docker buildx imagetools create" in dev_publish
        assert "git rev-parse origin/dev-kafka" in dev_publish
        assert "k8s/gitops/overlays/local-ha/kustomization.yaml" in dev_publish
        assert not (ROOT / ".github/workflows/dev-kafka-image.yml").exists()

    def test_terraform_uses_msk_instead_of_redis(self):
        terraform_files = [
            path.read_text(encoding="utf-8").lower()
            for path in (ROOT / "infra" / "terraform").rglob("*.tf")
            if ".terraform" not in path.parts
        ]
        combined = "\n".join(terraform_files)

        assert "aws_msk_cluster" in combined
        assert "module \"msk_kafka\"" in combined
        assert "kafka_bootstrap_servers" in combined
        assert "aws_elasticache" not in combined
        assert "redis" not in combined

    def test_kafka_exporter_is_wired_to_prometheus_and_manifests(self):
        prometheus = read_text("monitoring/prometheus/prometheus.yml")
        alerts = read_text("monitoring/prometheus/alerts.yml")
        app_manifest = read_text("k8s/app/manifests-ha.yaml")
        gitops_manifest = read_text("k8s/gitops/base/manifests-ha.yaml")

        for manifest in (app_manifest, gitops_manifest):
            assert "name: kafka-exporter" in manifest
            assert "danielqsj/kafka-exporter:v1.7.0" in manifest
            assert "--kafka.server=kafka.messaging-app.svc.cluster.local:9092" in manifest
            assert 'targets: ["kafka-exporter:9308"]' in manifest

        assert "job_name: kafka-exporter" in prometheus
        assert 'targets: ["kafka-exporter:9308"]' in prometheus
        assert "MessagingKafkaExporterDown" in alerts
        assert "MessagingKafkaConsumerLagHigh" in alerts
        assert alerts.count("sum(clamp_min(kafka_consumergroup_lag") == 3
        assert "sum(kafka_consumergroup_lag" not in alerts

        status_script = read_text("scripts/check_portfolio_status.ps1")
        suite_script = read_text("scripts/run_kafka_performance_suite.ps1")
        k6_script = read_text("scripts/load_test_k6.js")
        k6_runner = read_text("scripts/test_k6_load.ps1")
        assert status_script.count("sum(clamp_min(kafka_consumergroup_lag") == 2
        assert suite_script.count("sum(clamp_min(kafka_consumergroup_lag") == 2
        for script in (status_script, suite_script):
            assert "sum(kafka_consumergroup_lag" not in script
        assert 'checks: ["rate==1"]' in k6_script
        assert "K6_STREAM_COUNT" in k6_script
        assert "data.streamIds[streamIndex]" in k6_script
        assert "-K6StreamCount $K6StreamCount" in suite_script
        assert '[ValidateSet("keda", "fixed")]' in suite_script
        assert "Restore-WorkerScaling" in suite_script
        assert "k6 job did not finish within $TimeoutSec seconds" in k6_runner
        assert "-AllowThresholdFailure" not in suite_script
        assert "$failedResultPath" in suite_script
        assert "$overallSucceeded = $suiteSucceeded -and $null -eq $resetError" in suite_script
        assert "if ($overallSucceeded) { $resultPath } else { $failedResultPath }" in suite_script
        assert "Final reset failed:" in suite_script
        assert suite_script.index("Final reset failed:") < suite_script.index(
            "[System.IO.File]::WriteAllLines"
        ) < suite_script.index("if ($null -ne $suiteError)")
        assert suite_script.index("if ($null -ne $suiteError)") < suite_script.index(
            "if ($null -ne $resetError)"
        ) < suite_script.index("if ($null -ne $writeError)")

    def test_kafka_performance_suite_drains_post_hpa_backlog(self):
        suite_script = read_text("scripts/run_kafka_performance_suite.ps1")

        assert "function Wait-ConsumerLagDrain" in suite_script
        assert '-Phase "main_load"' in suite_script
        assert '-Phase "post_hpa"' in suite_script
        assert suite_script.count("-RequiredConsecutiveZeroSamples 2") == 2
        assert "function Get-ConsumerLagSample" in suite_script
        assert suite_script.count("min(timestamp(kafka_consumergroup_lag") == 2
        assert "$sourceTimestampBefore -eq $sourceTimestampAfter" in suite_script
        assert "after 3 attempts" in suite_script
        assert "$workerSample.Fresh -and $notificationSample.Fresh -and $timestampsAdvanced" in suite_script
        assert "$workerSample.SourceTimestampSeconds -gt $lastAcceptedWorkerSourceTimestamp" in suite_script
        assert "$notificationSample.SourceTimestampSeconds -gt $lastAcceptedNotificationSourceTimestamp" in suite_script
        assert "max_lag_metric_age_seconds:" in suite_script
        for metric_suffix in (
            "_message_worker_peak_consumer_lag",
            "_notification_worker_peak_consumer_lag",
            "_all_consumer_backlog_drain_seconds",
            "_message_worker_final_consumer_lag",
            "_notification_worker_final_consumer_lag",
        ):
            assert metric_suffix in suite_script
        assert "consumer lag did not drain to zero within" in suite_script
        assert suite_script.index('Invoke-SuiteStep "HPA and metrics sanity"') < suite_script.index(
            'Invoke-SuiteStep "Post-HPA Kafka consumer lag drain"'
        )
        assert suite_script.index(
            'Invoke-SuiteStep "Post-HPA Kafka consumer lag drain"'
        ) < suite_script.index('Invoke-SuiteStep "Final runtime snapshot"')
        assert "[System.Text.UTF8Encoding]::new($false)" in suite_script
        assert "[System.IO.File]::WriteAllLines" in suite_script
        assert "$temporaryOutputPath" in suite_script
        assert "Move-Item -LiteralPath $temporaryOutputPath -Destination $outputPath -Force" in suite_script

    def test_api_contract_waits_for_real_materialized_cache_hydration(self):
        contract_script = read_text("scripts/test_api_contracts.ps1")

        assert "function Wait-MaterializedCacheHydrated" in contract_script
        assert 'Assert-HasProperty $health "materialized_cache" "readiness"' in contract_script
        assert (
            'Assert-HasProperty $health.materialized_cache "hydrated" '
            '"readiness.materialized_cache"'
        ) in contract_script
        assert "$lastCache.ready -eq $true -and $lastCache.hydrated -eq $true" in contract_script
        assert (
            'Assert-Equal $health.materialized_cache.hydrated $true '
            '"readiness.materialized_cache.hydrated"'
        ) in contract_script

    def test_argocd_gitops_contract_matches_local_ha_runtime(self):
        install_script = read_text("k8s/scripts/install-argocd.ps1")
        bootstrap_script = read_text("k8s/scripts/bootstrap-argocd-app.ps1")
        quick_start = read_text("scripts/quick_start_gitops.ps1")
        app_example = read_text("k8s/argocd/application-messaging-portfolio-local-ha.example.yaml")
        gitops_docs = read_text("docs/GITOPS.md")

        for document in (bootstrap_script, quick_start, app_example):
            assert "master" in document
            assert "dev-kafka" not in document

        assert "master" in gitops_docs
        assert "dev-kafka" in gitops_docs
        assert "ghcr.io/jangwanko/cloud_portfolio:<12-char-sha>" in gitops_docs

        for manifest in (bootstrap_script, app_example):
            assert "RespectIgnoreDifferences=true" in manifest
            assert "ignoreDifferences:" in manifest
            assert "/spec/replicas" in manifest

        assert "--server-side --force-conflicts" in install_script
        assert "Clear-ProxyForKubectlDownload" in install_script
        assert "WaitForFirstConsumer" in install_script
        assert "postgres-backups" in install_script
        assert "Synced / Healthy" in gitops_docs

    def test_worker_keda_demo_threshold_is_visible_in_manifests_and_docs(self):
        app_manifest = read_text("k8s/app/manifests-ha.yaml")
        gitops_manifest = read_text("k8s/gitops/base/manifests-ha.yaml")
        architecture = read_text("docs/ARCHITECTURE.md")
        kafka_experiment = read_text("docs/KAFKA_EXPERIMENT.md")

        for manifest in (app_manifest, gitops_manifest):
            assert "name: worker-keda" in manifest
            assert "consumerGroup: message-worker" in manifest
            assert "topic: message-ingress" in manifest
            assert 'lagThreshold: "100"' in manifest
            assert "minReplicaCount: 2" in manifest
            assert "maxReplicaCount: 8" in manifest

        assert "lag threshold: `100` for the local demo cluster" in architecture
        assert "KEDA lag threshold: `100` for the local demo cluster" in kafka_experiment

    def test_portfolio_status_check_covers_runtime_control_plane(self):
        script = read_text("scripts/check_portfolio_status.ps1")
        readme = read_text("README.md")
        quick_start = read_text("docs/QUICK_START.md")
        runbook = read_text("docs/RUNBOOK.md")
        gitops_docs = read_text("docs/GITOPS.md")
        test_results = read_text("docs/TEST_RESULTS.md")

        for token in (
            "Argo CD GitOps",
            "Synced",
            "Healthy",
            "kafka_brokers",
            "kafka_consumergroup_lag",
            "worker-keda",
            "notification-worker",
            "postgres-backups",
            "WaitForFirstConsumer",
        ):
            assert token in script

        for document in (readme, quick_start, runbook, gitops_docs, test_results):
            assert "check_portfolio_status.ps1" in document

        assert "Portfolio Status Check" in test_results
        assert "message-worker consumer_lag=0" in test_results
        assert "notification-worker consumer_lag=0" in test_results

    def test_service_process_checklist_covers_full_operating_flow(self):
        checklist = read_text("docs/SERVICE_PROCESS_CHECKLIST.md")
        readme = read_text("README.md")
        quick_start = read_text("docs/QUICK_START.md")
        runbook = read_text("docs/RUNBOOK.md")
        operations = read_text("docs/OPERATIONS.md")
        repository_structure = read_text("docs/REPOSITORY_STRUCTURE.md")

        for process in (
            "처음 실행하는 경우",
            "정상 출력 예시",
            "이상 신호를 읽는 법",
            "Cluster / GitOps",
            "API readiness",
            "API 계약",
            "Event intake",
            "Kafka broker",
            "Consumer lag",
            "Worker persistence",
            "Stream ordering",
            "DLQ flow",
            "DLQ replay guard",
            "Autoscaling",
            "Observability",
            "Alert wiring",
            "Backup",
            "Restore",
            "Performance baseline",
        ):
            assert process in checklist

        for command in (
            "scripts/quick_start_all.ps1",
            "scripts/quick_start_gitops.ps1",
            "scripts/check_portfolio_status.ps1",
            "scripts/smoke_test.ps1",
            "scripts/test_stream_ordering.ps1",
            "scripts/test_dlq_flow.ps1",
            "scripts/test_incident_signals.ps1",
            "scripts/run_kafka_performance_suite.ps1",
        ):
            assert command in checklist

        for document in (readme, quick_start, runbook, operations, repository_structure):
            assert "SERVICE_PROCESS_CHECKLIST.md" in document

        assert "-Revision master" in quick_start
        assert "consumer_lag > 100" in checklist
        assert "Fresh install에서 PVC가 첫 consumer를 기다릴 때만" in checklist
        assert "2026-07-21 현재 local PVC는 수동 backup Job 실행 뒤 `Bound`" in checklist


class TestApiContractAndRunbook:
    def test_windows_web_requests_use_basic_parsing(self):
        for path in (ROOT / "scripts").glob("*.ps1"):
            source = path.read_text(encoding="utf-8")
            for match in re.finditer(r"Invoke-WebRequest", source):
                assert "-UseBasicParsing" in source[match.start() : match.start() + 160], path

    def test_api_contract_script_is_in_recommended_flow_and_docs(self):
        script = read_text("scripts/test_api_contracts.ps1")
        recommended = read_text("scripts/run_recommended_tests.ps1")
        quick_start = read_text("docs/QUICK_START.md")
        test_results = read_text("docs/TEST_RESULTS.md")

        assert "Assert-HasProperty" in script
        assert "/v1/auth/login" in script
        assert "/v1/streams/" in script
        assert "/v1/dlq/ingress" in script
        assert "/v1/dlq/ingress/summary" in script
        assert "Expected HTTP $ExpectedStatus" in script
        assert (
            '"$BaseUrl/v1/streams/$($stream.id)/events" -ExpectedStatus 404 '
            "-Headers $outsiderHeaders"
        ) in script
        assert "test_api_contracts.ps1" in recommended
        assert "test_api_contracts.ps1" in quick_start
        assert "API contract test" in test_results

    def test_dlq_summary_api_is_documented(self):
        readme = read_text("README.md")
        operations = read_text("docs/OPERATIONS.md")
        runbook = read_text("docs/RUNBOOK.md")
        observability = read_text("docs/OBSERVABILITY.md")
        test_results = read_text("docs/TEST_RESULTS.md")

        assert "DLQ summary" in readme
        for token in ("by_reason", "replayable", "blocked"):
            assert token in readme

        for document in (operations, runbook, observability, test_results):
            assert "/v1/dlq/ingress/summary" in document
            assert "by_reason" in document
            assert "replayable" in document
            assert "blocked" in document

    def test_response_models_and_incident_probe_are_documented(self):
        schemas = read_text("portfolio/schemas.py")
        api = read_text("portfolio/api.py")
        main = read_text("portfolio/main.py")
        readme = read_text("README.md")
        runbook = read_text("docs/RUNBOOK.md")
        operations = read_text("docs/OPERATIONS.md")
        test_results = read_text("docs/TEST_RESULTS.md")
        incident_script = read_text("scripts/test_incident_signals.ps1")

        for model in (
            "ReadinessResponse",
            "DlqListResponse",
            "DlqSummaryResponse",
            "EventRequestStatusResponse",
        ):
            assert model in schemas
            assert model in api or model in main

        assert "test_incident_signals.ps1" in runbook
        assert "test_incident_signals.ps1" in test_results
        assert "MessagingDeploymentUnavailableReplicas" in incident_script
        assert "messaging-portfolio:incident-probe-missing" in incident_script
        assert "response_model" in operations
        assert "/docs" in readme
        assert "/openapi.json" in operations
        assert "OpenAPI" in test_results

    def test_runbook_is_linked_and_covers_incident_paths(self):
        readme = read_text("README.md")
        operations = read_text("docs/OPERATIONS.md")
        runbook = read_text("docs/RUNBOOK.md")

        assert "RUNBOOK.md" in readme
        assert "RUNBOOK.md" in operations
        for heading in (
            "Kafka Intake",
            "PostgreSQL / Pgpool",
            "Worker Consumer Lag",
            "DLQ",
            "API Contract",
            "Resource Contention",
        ):
            assert heading in runbook

        for command in (
            "kubectl get pods -n messaging-app",
            "scripts/test_api_contracts.ps1 -SkipReset",
            "scripts/test_dlq_replay_guard.ps1 -SkipReset",
            "scripts/run_recommended_tests.ps1 -SkipK6",
        ):
            assert command in runbook


class TestOperationsDashboard:
    def test_dashboard_uses_operational_metrics_without_fake_kafka_signals(self):
        dashboard = json.loads(read_text("monitoring/grafana/dashboards/messaging-overview.json"))
        serialized = json.dumps(dashboard)
        titles = {panel["title"] for panel in dashboard["panels"]}

        expected_titles = {
            "Kafka Intake Health",
            "Kafka Broker Count",
            "Kafka Consumer Group Lag",
            "Kafka Topic Partitions",
            "PostgreSQL Primary",
            "Worker Availability",
            "API 5xx Ratio",
            "Worker Failure Ratio",
            "Worker Last Success Age",
            "DB Pool In Use",
            "DLQ Events And Replay",
            "Pod Restarts (15m)",
            "Unavailable Replicas",
            "DLQ Operator Links",
        }
        assert expected_titles.issubset(titles)

        assert "{{queue}}" not in serialized
        assert "producer_append_path" not in serialized
        assert "consumer_read_path" not in serialized
        assert "worker_consumer_group" not in serialized

        panels = {panel["title"]: panel for panel in dashboard["panels"]}
        for title in (
            "Kafka Intake Health",
            "PostgreSQL Primary",
            "Worker Availability",
            "Kafka Broker Availability",
        ):
            target = panels[title]["targets"][0]
            assert target["instant"] is True
            assert target["expr"].startswith("min(")

        api_5xx_defaults = panels["API 5xx Ratio"]["fieldConfig"]["defaults"]
        assert api_5xx_defaults["unit"] == "percentunit"
        assert api_5xx_defaults["min"] == 0
        assert api_5xx_defaults["max"] == 1

        assert (
            "kube_deployment_status_replicas_available"
            in panels["Worker Availability"]["targets"][0]["expr"]
        )
        assert panels["Kafka Broker Availability"]["targets"][0]["expr"] == "min(kafka_brokers)"
        assert "[5m]" in panels["API Latency"]["targets"][0]["expr"]
        assert "[5m]" in panels["API Stage Latency"]["targets"][0]["expr"]

        for metric in (
            "messaging_db_pool_in_use",
            "messaging_worker_last_success_timestamp",
            "messaging_dlq_events_total",
            "messaging_dlq_replay_total",
            "kafka_brokers",
            "kafka_consumergroup_lag",
            "kafka_topic_partition_current_offset",
            "kube_pod_container_status_restarts_total",
            "kube_deployment_status_replicas_unavailable",
            "/v1/dlq/ingress/summary",
        ):
            assert metric in serialized

    def test_dashboard_is_embedded_in_both_kubernetes_manifest_paths(self):
        dashboard = read_text("monitoring/grafana/dashboards/messaging-overview.json")
        app_manifest = read_text("k8s/app/manifests-ha.yaml")
        gitops_manifest = read_text("k8s/gitops/base/manifests-ha.yaml")

        for manifest in (app_manifest, gitops_manifest):
            assert "name: dlq-replayer" in manifest
            assert "dlq-replayer.messaging-app.svc.cluster.local" in manifest
            assert "dns_sd_configs:" in manifest
            assert "Reliable Event Processing Operations Overview" in manifest
            assert "messaging_dlq_replay_total" in manifest

        assert "Reliable Event Processing Operations Overview" in dashboard
        assert "Messaging Portfolio Operations Overview" not in dashboard


class TestAlertPolicy:
    def test_prometheus_alerts_define_operational_thresholds(self):
        alerts = read_text("monitoring/prometheus/alerts.yml")

        expected_alerts = (
            "MessagingApi5xxRateWarning",
            "MessagingApiHigh5xxRate",
            "MessagingEventPersistLagHigh",
            "MessagingEventPersistLagCritical",
            "MessagingQueueWaitHigh",
            "MessagingQueueWaitCritical",
            "MessagingWorkerLastSuccessStale",
            "MessagingDlqEventsIncreasing",
            "MessagingDlqReplayBlocked",
            "MessagingPodRestarting",
            "MessagingDeploymentUnavailableReplicas",
        )
        for alert in expected_alerts:
            assert alert in alerts

        for threshold in (
            "> 0.01",
            "> 0.05",
            "> 5",
            "> 15",
            "> 10",
            "> 30",
            "skipped_max_replay",
            "kube_pod_container_status_restarts_total",
            "kube_deployment_status_replicas_unavailable",
        ):
            assert threshold in alerts

    def test_alert_rules_are_embedded_in_both_kubernetes_manifest_paths(self):
        app_manifest = read_text("k8s/app/manifests-ha.yaml")
        gitops_manifest = read_text("k8s/gitops/base/manifests-ha.yaml")

        for manifest in (app_manifest, gitops_manifest):
            for alert in (
                "MessagingApi5xxRateWarning",
                "MessagingEventPersistLagCritical",
                "MessagingQueueWaitCritical",
                "MessagingDlqReplayBlocked",
                "MessagingPodRestarting",
                "MessagingDeploymentUnavailableReplicas",
            ):
                assert alert in manifest

    def test_operational_docs_describe_alert_thresholds_and_metric_probe(self):
        reliability = read_text("docs/RELIABILITY_POLICY.md")
        runbook = read_text("docs/RUNBOOK.md")
        observability = read_text("docs/OBSERVABILITY.md")
        metrics_reference = read_text("docs/METRICS_REFERENCE.md")
        test_results = read_text("docs/TEST_RESULTS.md")

        assert "API 5xx" in reliability
        assert "accepted-to-persisted" in reliability
        assert "Kafka topic wait" in reliability
        assert "MessagingDlqReplayBlocked" in reliability
        assert "oldest_sample_age_seconds" in reliability

        assert "API 5xx" in runbook
        assert "accepted-to-commit" in runbook
        assert "Kafka topic wait" in runbook
        assert "MessagingDlqReplayBlocked" in runbook
        assert "oldest_sample_age_seconds" in runbook

        assert "accepted-to-persisted" in test_results
        assert "MessagingDlqReplayBlocked" in test_results
        assert "oldest_sample_age_seconds" in test_results
        assert "현재 DLQ sample age는 alert SLO가 아닙니다" in test_results

        for document in (observability, metrics_reference, runbook, test_results):
            assert "kafka-exporter" in document
            assert "kafka_consumergroup_lag" in document
            assert "kafka_brokers" in document

        for document in (metrics_reference, runbook, test_results):
            assert "GET /v1/dlq/ingress/summary" in document
            assert "oldest_sample_age_seconds" in document

        assert "unresolved SLO로 사용하지 않습니다" in reliability
        assert "unresolved event age 또는 backlog SLO로 사용 제외" in runbook
        assert "현재 DLQ sample age는 alert SLO가 아닙니다" in test_results

        assert "3974 -> 4008" in test_results
        assert "stream_seq 1..20" in test_results

    def test_operational_alert_probe_is_documented(self):
        script = read_text("scripts/test_operational_alerts.ps1")
        runbook = read_text("docs/RUNBOOK.md")
        observability = read_text("docs/OBSERVABILITY.md")
        test_results = read_text("docs/TEST_RESULTS.md")

        for alert in (
            "MessagingDlqEventsIncreasing",
            "MessagingDlqReplayBlocked",
            "MessagingDeploymentUnavailableReplicas",
        ):
            assert alert in script
            assert alert in runbook
            assert alert in test_results

        assert "test_operational_alerts.ps1" in runbook
        assert "test_operational_alerts.ps1" in observability
        assert "test_operational_alerts.ps1" in test_results
        assert "messaging-portfolio:alert-probe-missing" in script
        assert "messaging-portfolio:local" in script
