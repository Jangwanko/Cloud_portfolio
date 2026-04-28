from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class TestOperationalDocumentation:
    def test_architecture_docs_include_normal_and_failure_diagrams(self):
        readme = read_text("README.md")
        architecture = read_text("docs/ARCHITECTURE.md")

        for document in (readme, architecture):
            assert "정상 event 흐름" in document
            assert "장애 / DLQ 흐름" in document
            assert "sequenceDiagram" in document
            assert "inline retry" in document

    def test_operations_docs_include_dlq_and_security_policy(self):
        operations = read_text("docs/OPERATIONS.md")

        assert "## DLQ 운영 기준" in operations
        assert "DLQ_REPLAY_MAX_COUNT" in operations
        assert "## 보안 기본선" in operations
        assert "dev-secret-change-me" in operations
        assert "외부 secret manager" in operations

    def test_test_results_and_patch_notes_keep_experiment_rounds(self):
        test_results = read_text("docs/TEST_RESULTS.md")
        patch_notes = read_text("docs/PATCH_NOTES.md")

        for document in (test_results, patch_notes):
            assert "1차 실험: Kafka 이벤트 스트림 기준선" in document
            assert "2차 실험: Pgpool HA와 엄격한 stream 순서 보장" in document
            assert "31710" in document
            assert "31676" in document

    def test_reproducibility_environment_is_documented(self):
        readme = read_text("README.md")
        quick_start = read_text("docs/QUICK_START.md")
        test_results = read_text("docs/TEST_RESULTS.md")

        for document in (readme, quick_start, test_results):
            assert "AMD Ryzen 5 5600" in document
            assert "12 CPU" in document
            assert "15.6GiB" in document

        assert "권장 사양보다 낮은" in quick_start
        assert "권장 사양보다 낮은" in test_results
        assert "리소스 부족 신호" in quick_start
        assert "리소스 부족 가능성" in test_results
        assert "Poison event did not reach Kafka DLQ in time" in quick_start

    def test_public_docs_do_not_describe_redis_migration(self):
        public_docs = [read_text("README.md")]
        public_docs.extend(path.read_text(encoding="utf-8") for path in (ROOT / "docs").glob("*.md"))
        combined = "\n".join(public_docs).lower()

        blocked_terms = ["redis", "elasticache", "마이그레이션", "기존 redis", "처음부터 kafka"]
        for term in blocked_terms:
            assert term not in combined


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


class TestApiContractAndRunbook:
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
        assert "test_api_contracts.ps1" in recommended
        assert "test_api_contracts.ps1" in quick_start
        assert "API contract test" in test_results

    def test_dlq_summary_api_is_documented(self):
        readme = read_text("README.md")
        operations = read_text("docs/OPERATIONS.md")
        runbook = read_text("docs/RUNBOOK.md")
        observability = read_text("docs/OBSERVABILITY.md")
        test_results = read_text("docs/TEST_RESULTS.md")

        for document in (readme, operations, runbook, observability, test_results):
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
            "PostgreSQL Primary",
            "Worker Health",
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

        for metric in (
            "messaging_db_pool_in_use",
            "messaging_worker_last_success_timestamp",
            "messaging_dlq_events_total",
            "messaging_dlq_replay_total",
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
            assert 'targets: ["dlq-replayer:9102"]' in manifest
            assert "Messaging Portfolio Operations Overview" in manifest
            assert "messaging_dlq_replay_total" in manifest

        assert "Messaging Portfolio Operations Overview" in dashboard


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
        test_results = read_text("docs/TEST_RESULTS.md")

        for document in (reliability, runbook, test_results):
            assert "API 5xx" in document
            assert "accepted-to-persisted" in document
            assert "Kafka topic wait" in document
            assert "MessagingDlqReplayBlocked" in document

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
