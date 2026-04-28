from pathlib import Path


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
        assert "Expected HTTP $ExpectedStatus" in script
        assert "test_api_contracts.ps1" in recommended
        assert "test_api_contracts.ps1" in quick_start
        assert "API contract test" in test_results

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
