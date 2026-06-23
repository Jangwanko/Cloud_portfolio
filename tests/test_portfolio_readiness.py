from pathlib import Path
import json


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
            "실시간 협업 메시징",
            "적용 가능한 서비스 관점",
            "주문 이후 이벤트 처리",
            "알림 발송 파이프라인",
            "고객 문의 / CS 이벤트",
            "감사 로그 / 활동 로그",
            "IoT / 센서 수집",
            "사용자와 관심사",
            "기능 요구",
            "비기능 요구",
            "SLO 가드레일",
            "API 5xx ratio",
            "accepted-to-persisted p95",
            "Kafka topic wait p95",
            "DLQ oldest age",
            "oldest_age_seconds > 600",
            "oldest_age_seconds > 1800",
            "stream_id",
            "Worker inline retry",
            "Kafka DLQ topic",
            "Argo CD `Synced / Healthy`",
        ):
            assert token in requirements

        for document in (readme, architecture, reliability, repository_structure, test_results):
            assert "SERVICE_REQUIREMENTS.md" in document

        assert "쇼핑몰에서 결제와 주문 완료 이후 발생하는 이벤트" in readme
        assert "AWS Managed Service Mapping" in readme
        assert "## TL;DR" in readme
        assert "## Trade-off" in readme
        assert "서비스 문제" in architecture
        assert "서비스 기준" in readme

    def test_readme_is_interview_friendly_about_boundary_and_tradeoffs(self):
        readme = read_text("README.md")

        for token in (
            "## TL;DR",
            "## Problem",
            "## Solution",
            "## Architecture Boundary",
            "Kafka-only 구조가 아니라 Kafka-centered 구조",
            "PostgreSQL state path",
            "## Validation Summary",
            "31,676",
            "100/100 pass",
            "## Trade-off",
            "API -> Kafka append",
            "Worker async persistence",
            "## Ordering Guarantee",
            "multi-partition 전체 global ordering은 보장하지 않습니다",
            "## Intake Boundary: Idempotency State Path",
            "X-Idempotency-Key",
            "Kafka append 전에 PostgreSQL claim",
            "## What I Learned",
            "## Current Bottleneck",
            "Worker DB write throughput",
            "room_sequences",
            "## Next Improvements",
            "Kafka compacted topic",
            "consumer group rebalance",
        ):
            assert token in readme

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

        readme = read_text("README.md")
        architecture = read_text("docs/ARCHITECTURE.md")
        for document in (readme, architecture):
            assert "cache-first" in document
            assert "snapshot_age_seconds" in document
            assert "degraded=true" in document
            assert "message-snapshots" in document
            assert "stream-snapshots" in document
            assert "API local materialized cache" in document

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
            "Snapshot consumer lag",
            "snapshot_age_seconds > 30s",
            "snapshot_age_seconds > 120s",
            "Degraded read ratio",
        ):
            assert token in requirements

        for token in (
            "Read cache operating signals",
            "Read cache hit ratio",
            "Snapshot consumer lag",
            "source=cache",
            "degraded=true",
        ):
            assert token in metrics_reference

        for token in (
            "read cache hit ratio",
            "snapshot age",
            "degraded read count",
            "snapshot consumer lag",
        ):
            assert token in observability

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

        assert "Kafka append-first path" in combined
        assert "60 passed" in combined
        assert "9f7fc62be6f202abf98e12c8c108075502cd29a6" in combined
        assert "Worker persistence capacity 신호" in combined
        assert "fresh cache read는 `source=cache`, `degraded=false`" in combined
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

    def test_static_order_dashboard_demo_exists(self):
        demo = read_text("demo/order-dashboard.html")
        readme = read_text("README.md")
        repository_structure = read_text("docs/REPOSITORY_STRUCTURE.md")
        main = read_text("portfolio/main.py")
        dockerfile = read_text("Dockerfile")

        for token in (
            "Post-Order Event Console",
            "demo-version",
            "DEMO_UI_VERSION",
            "setDemoVersion",
            "ver.",
            "api",
            "language-toggle",
            "setLanguage",
            "translations",
            "data-i18n",
            "KO",
            "EN",
            "결제 완료",
            "주문 완료",
            "운영자 이벤트 큐",
            "/v1/orders/",
            "/v1/dlq/ingress/summary",
            "payment_completed",
            "delivery_started",
            "needs_review",
            "Pipeline Evidence",
            "API accepted",
            "Kafka appended",
            "Worker persisted",
            "1. API 접수됨",
            "2. Kafka 적재됨",
            "3. Worker 처리 중",
            "4. DB 저장됨",
            "DLQ summary",
            "/v1/event-requests/",
            "/v1/streams",
            "createDemoOrderStream",
            "10개 추가",
            "100개 추가",
            "1000개 추가",
            "Add 10",
            "Add 100",
            "Add 1000",
            "주문 이후 업무 이벤트 종류입니다.",
            "API가 노출된 주소입니다.",
            "데모 주문 stream id로 자동 갱신됩니다.",
            "전송 전 예약 비우기",
            "clear-event-list",
            "sample-actions",
            "send-action",
            "처리 증거",
            "처리 현황",
            "예약 건수",
            "Kafka 적재",
            "DB 저장",
            "총 소요시간",
            "queue-metrics",
            "grid-template-columns: repeat(4, minmax(0, 1fr))",
            "grid-template-columns: minmax(300px, 0.82fr) minmax(420px, 1fr) minmax(300px, 0.78fr) minmax(300px, 0.78fr)",
            "height: 170px",
            "max-height: 170px",
            "queued-count",
            "processed-count",
            "db-persisted-count",
            "elapsed-seconds",
            "result-panel",
            "result-requested",
            "result-kafka",
            "result-persisted",
            "result-cancelled",
            "result-status",
            "결과 정리",
            "Result Summary",
            "recordQueueEnqueued",
            "recordQueueProcessed",
            "recordKafkaAppended",
            "recordDbPersisted",
            "finishProcessingRun",
            "updateQueueMetrics",
            "startProcessingRun",
            "lastRunHadFailures",
            "runStartedAt",
            "runCompletedAt",
            "runProcessed",
            "markEventStatus",
            "db_row",
            "pollRequestStatus(baseUrl, token, event, uiSession, acceptedCount, activeEvents.length)",
            "아직 시작하지 않은 예약은 취소할 수 있고, 시작된 작업은 계속 추적합니다.",
            "processReservedEvents",
            "buildFormEvent",
            "sendQueuedEvent",
            "SEND_CONCURRENCY",
            "sendNextReservedEvent",
            "activeEvents",
            "shouldRenderBatchProgress",
            "Promise.allSettled(Array.from({ length: senderCount }, () => sendNextReservedEvent()))",
            "await Promise.allSettled(pollTasks)",
            "sendFailures",
            "queueStats.queued > 0 || queueStats.runProcessed < queueStats.runTarget",
            "예약 큐 처리 시작",
            "cancelPendingReservations",
            "events.splice(index, 1)",
            "queueStats.cancelled += cancelledCount",
            "queueStats.runTarget = Math.max(queueStats.runTarget - cancelledCount, queueStats.runProcessed)",
            "proof-grid",
            "Kafka topic",
            "DB row",
            "운영 링크는 확인용 보조 링크입니다.",
            "운영 상태 확인",
            "Readiness와 DLQ summary를 페이지 이동 없이 요약합니다.",
            "상태 새로고침",
            "5초마다",
            "10초마다",
            "Every 5s",
            "Every 10s",
            "Operational Checks",
            "Refresh status",
            "refresh-ops-status",
            "ops-refresh-interval",
            "ops-ready-status",
            "ops-kafka-status",
            "ops-postgres-status",
            "ops-worker-status",
            "ops-dlq-status",
            "ops-last-checked",
            "{available}/{max} 실행 중",
            "{available}/{max} running",
            "Operations Advisor",
            "operations-advisor",
            "advisor-status",
            "advisor-reason",
            "advisor-next-step",
            "advisor-signal-count",
            "advisor-signal-history",
            "advisorHistoryTitle",
            "advisorHistoryEmpty",
            "advisorOccurrenceCount",
            "advisorSituation",
            "advisorExpectedFix",
            "advisorDemoLiteReadinessReason",
            "advisorDemoLiteReadinessNext",
            "advisorDemoLiteReadinessSituation",
            "isDemoLiteProfile",
            "readiness.deployment_profile === \"k8s-demo-lite\"",
            "advisorSignalStats",
            "recordAdvisorSignal",
            "renderAdvisorSignalHistory",
            "updateOperationsAdvisor",
            "AI API는 호출하지 않습니다.",
            "No AI API is called.",
            "refreshOpsStatus",
            "restartOpsAutoRefresh",
            "window.setInterval(refreshOpsStatus, intervalMs)",
            "updateOperationLinks",
            'document.querySelector("#base-url").addEventListener("input", updateOperationLinks)',
            "updateReadinessPanel",
            "updateDlqSummaryPanel",
            "queueStats.runTarget > 0 ? `${queueStats.queued}/${queueStats.runTarget}` : String(queueStats.queued)",
            "reset_dlq_topic",
            "/health/ready",
            "브라우저가 API 요청을 막았습니다.",
            "http://localhost/demo/order-dashboard.html",
            "deriveDefaultBaseUrl",
            "describeFetchFailure",
            "addSampleBatch",
            "add-sample-1000",
            "add1000",
            "clearEvents",
        ):
            assert token in demo

        assert 'document.documentElement.lang = language' in demo
        assert 'document.querySelectorAll("[data-i18n]")' in demo
        assert 'document.querySelectorAll("[data-language]")' in demo
        assert "../docs/RUNBOOK.md" not in demo
        sample_batch = demo.split("function addSampleBatch(count) {", 1)[1].split("function cancelPendingReservations()", 1)[0]
        assert "recordQueueEnqueued(count)" in sample_batch
        assert "startQueueDrain" not in sample_batch
        process_reserved = demo.split("async function processReservedEvents()", 1)[1].split("async function sendOrderEvent()", 1)[0]
        assert "reservedEvents.length === 0" in process_reserved
        assert "recordQueueEnqueued(1)" in process_reserved
        assert "startProcessingRun(reservedEvents.length)" in process_reserved
        assert "const token = await ensureToken" in process_reserved
        assert process_reserved.index("startProcessingRun(reservedEvents.length)") < process_reserved.index("const token = await ensureToken")
        assert "const activeEvents = reservedEvents.filter" in process_reserved
        assert "async function sendNextReservedEvent()" in process_reserved
        assert "await sendQueuedEvent" in process_reserved
        assert process_reserved.index('event.status = "sending"') < process_reserved.index("await sendQueuedEvent")
        assert "recordKafkaAppended(1, uiSession)" in process_reserved
        assert "pollTasks.push(pollRequestStatus(baseUrl, token, event, uiSession, acceptedCount, activeEvents.length))" in process_reserved
        assert "const senderCount = Math.min(SEND_CONCURRENCY, activeEvents.length)" in process_reserved
        assert "await Promise.allSettled(Array.from({ length: senderCount }, () => sendNextReservedEvent()))" in process_reserved
        assert "const pollResults = await Promise.allSettled(pollTasks)" in process_reserved
        assert "finishProcessingRun(uiSession, sendFailures > 0 || pollResults.some((result) => result.status === \"rejected\"))" in process_reserved
        record_kafka_appended = demo.split("function recordKafkaAppended(count, uiSession) {", 1)[1].split("function recordDbPersisted", 1)[0]
        assert "queueStats.queued -= appended" in record_kafka_appended
        record_db_persisted = demo.split("function recordDbPersisted(count, uiSession) {", 1)[1].split("function recordQueueProcessed", 1)[0]
        assert "queueStats.queued -=" not in record_db_persisted
        assert "queueStats.dbPersisted += count" in record_db_persisted
        finish_run = demo.split("function finishProcessingRun(uiSession, hadFailures = false) {", 1)[1].split("function markEventStatus", 1)[0]
        assert "queueStats.lastRunHadFailures = hadFailures" in finish_run
        assert "queueStats.runCompletedAt = Date.now()" in finish_run
        advisor_logic = demo.split("function updateOperationsAdvisor(readiness, dlqSummary) {", 1)[1].split("let opsRefreshTimer", 1)[0]
        assert 'readiness.status === "degraded" && isDemoLiteProfile(readiness)' in advisor_logic
        assert "advisorDemoLiteReadinessReason" in advisor_logic
        assert "advisorDemoLiteReadinessNext" in advisor_logic
        assert 'statusKey === "advisorAttention" || statusKey === "advisorCritical"' in advisor_logic
        assert "recordAdvisorSignal(statusKey, reasonKey, nextKey, readiness, dlqSummary)" in advisor_logic
        assert "lastAdvisorSignalSignature = null" in advisor_logic
        signal_recorder = demo.split("function recordAdvisorSignal(", 1)[1].split("function renderAdvisorSignalHistory", 1)[0]
        assert "if (signature === lastAdvisorSignalSignature)" in signal_recorder
        assert "existing.count += 1" in signal_recorder
        assert "existing.lastSeenAt = now" in signal_recorder
        reset_demo_db = demo.split("async function resetDemoEventDb()", 1)[1].split('document.querySelector("#send-event")', 1)[0]
        assert "advisorSignalStats.clear()" in reset_demo_db
        assert "renderAdvisorSignalHistory()" in reset_demo_db
        send_order_event = demo.split("async function sendOrderEvent()", 1)[1].split("function buildSampleEvent", 1)[0]
        assert "processReservedEvents" in send_order_event
        clear_events = demo.split("function clearEvents()", 1)[1].split("async function resetDemoEventDb()", 1)[0]
        assert "cancelPendingReservations()" in clear_events
        assert "events.length = 0" not in clear_events
        result_panel_markup = demo.split('<section class="stack result-panel waiting" id="result-panel">', 1)[1].split("</section>", 1)[0]
        assert result_panel_markup.index("operations-advisor") < result_panel_markup.index("resultTitle")
        links_markup = demo.split('<div class="links">', 1)[1].split("</div>", 1)[0]
        assert 'data-ops-link="/docs"' in links_markup
        assert 'data-ops-link="/grafana/d/messaging-portfolio-overview/messaging-portfolio-operations-overview?orgId=1&refresh=5s"' in links_markup
        assert "http://localhost/docs" not in links_markup
        assert "http://localhost/grafana" not in links_markup
        assert "/v1/dlq/ingress/summary" not in links_markup
        assert "/health/ready" not in links_markup

        assert "demo/order-dashboard.html" in readme
        assert "demo/order-dashboard.html" in repository_structure
        assert 'app.mount("/demo"' in main
        assert "COPY demo ./demo" in dockerfile
        return

        for token in (
            "Post-Order Event Console",
            "결제 완료",
            "주문 완료",
            "운영자 이벤트 큐",
            "/v1/orders/",
            "/v1/dlq/ingress/summary",
            "payment_completed",
            "delivery_started",
            "needs_review",
            "Pipeline Evidence",
            "API accepted",
            "Kafka appended",
            "Worker persisted",
            "1. API 접수됨",
            "2. Kafka 적재됨",
            "3. Worker 처리 중",
            "4. DB 저장됨",
            "DLQ summary",
            "/v1/event-requests/",
            "/v1/streams",
            "createDemoOrderStream",
            "샘플 1개 추가",
            "샘플 10개 추가",
            "샘플 100개 추가",
            "주문 이후 업무 이벤트 종류입니다.",
            "API가 노출된 주소입니다.",
            "데모 주문 stream id로 자동 갱신됩니다.",
            "운영자 이벤트 큐 비우기",
            "clear-event-list",
            "sample-actions",
            "send-action",
            "처리 증거",
            "처리 현황",
            "예약 건수",
            "Kafka 적재",
            "DB 저장",
            "총 소요시간",
            "처리량/sec",
            "queue-metrics",
            "height: 280px",
            "align-content: start",
            "height: 160px",
            "max-height: 160px",
            "queued-count",
            "processed-count",
            "db-persisted-count",
            "elapsed-seconds",
            "throughput-rate",
            "recordQueueEnqueued",
            "recordQueueProcessed",
            "recordKafkaAppended",
            "recordDbPersisted",
            "updateQueueMetrics",
            "startProcessingRun",
            "runStartedAt",
            "runCompletedAt",
            "runProcessed",
            "이번 처리 시퀀스",
            "markEventStatus",
            "db_row",
            "pollRequestStatus(baseUrl, token, event)",
            "샘플은 전송 전 예약 큐에 추가됩니다.",
            "Kafka 적재와 DB 저장을 분리해서 집계합니다.",
            "processReservedEvents",
            "buildFormEvent",
            "sendQueuedEvent",
            "예약 큐 처리 시작",
            "proof-grid",
            "Kafka topic",
            "DB row",
            "운영 링크는 확인용 보조 링크입니다.",
            "/health/ready",
            "브라우저가 API 요청을 막았습니다.",
            "http://localhost/demo/order-dashboard.html",
            "deriveDefaultBaseUrl",
            "describeFetchFailure",
            "addSampleBatch",
            "clearEvents",
        ):
            assert token in demo

        assert "../docs/RUNBOOK.md" not in demo
        sample_batch = demo.split("function addSampleBatch(count) {", 1)[1].split("function addSample()", 1)[0]
        assert "recordQueueEnqueued(count)" in sample_batch
        assert "startQueueDrain" not in sample_batch
        process_reserved = demo.split("async function processReservedEvents()", 1)[1].split("async function sendOrderEvent()", 1)[0]
        assert "reservedEvents.length === 0" in process_reserved
        assert "recordQueueEnqueued(1)" in process_reserved
        assert "startProcessingRun(reservedEvents.length)" in process_reserved
        assert "const token = await ensureToken" in process_reserved
        assert process_reserved.index("startProcessingRun(reservedEvents.length)") < process_reserved.index("const token = await ensureToken")
        assert "for (const event of reservedEvents)" in process_reserved
        assert "await sendQueuedEvent" in process_reserved
        assert "recordKafkaAppended(1)" in process_reserved
        assert "pollTasks.push(pollRequestStatus(baseUrl, token, event))" in process_reserved
        assert "await Promise.all(pollTasks)" in process_reserved
        send_order_event = demo.split("async function sendOrderEvent()", 1)[1].split("function buildSampleEvent", 1)[0]
        assert "processReservedEvents" in send_order_event

        assert "demo/order-dashboard.html" in readme
        assert "demo/order-dashboard.html" in repository_structure
        assert 'app.mount("/demo"' in main
        assert "COPY demo ./demo" in dockerfile


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

    def test_lite_deploy_preserves_existing_runtime_auth_secret(self):
        script = read_text("scripts/deploy_lite_k3s.sh")

        assert "existing_auth_secret" in script
        assert "Reusing existing AUTH_SECRET_KEY" in script
        assert "kubectl_cmd create secret generic messaging-runtime-secrets" in script
        assert "openssl rand -base64 48" in script

    def test_demo_lite_gitops_uses_registry_image_and_action_tag_update(self):
        kustomization = read_text("k8s/gitops/overlays/demo-lite/kustomization.yaml")
        env_patch = read_text("k8s/gitops/overlays/demo-lite/patches/messaging-env-lite.yaml")
        workflow = read_text(".github/workflows/demo-lite-image.yml")

        assert "images:" in kustomization
        assert "name: messaging-portfolio" in kustomization
        assert "newName: ghcr.io/jangwanko/cloud_portfolio" in kustomization
        assert "newTag:" in kustomization
        assert "APP_VERSION:" in env_patch

        assert "branches: [demo-lite]" in workflow
        assert "ghcr.io/jangwanko/cloud_portfolio" in workflow
        assert "[skip demo-lite image]" in workflow
        assert "docker/build-push-action" in workflow
        assert "yq -i" in workflow
        assert "k8s/gitops/overlays/demo-lite/kustomization.yaml" in workflow
        assert "DEMO_LITE_ENV_PATCH" in workflow
        assert ".stringData.APP_VERSION" in workflow

    def test_k3s_profile_reconcile_script_detects_specs_and_updates_argocd(self):
        script = read_text("scripts/reconcile_profile_k3s.sh")
        gitops_docs = read_text("docs/GITOPS.md")
        demo_lite_docs = read_text("docs/DEMO_LITE.md")

        for token in (
            "detect_node_profile",
            "LOCAL_HA_MIN_MILLICORES",
            "LOCAL_HA_MIN_MEMORY_MIB",
            "Detected server",
            "Recommended profile",
            "Current Argo CD path",
            "k8s/gitops/overlays/demo-lite-k3s",
            "k8s/gitops/overlays/local-ha",
            "targetRevision",
            "kubectl apply -f -",
            "--dry-run",
            "--profile",
        ):
            assert token in script

        for document in (gitops_docs, demo_lite_docs):
            assert "scripts/reconcile_profile_k3s.sh" in document
            assert "demo-lite" in document
            assert "local-ha" in document

    def test_terraform_uses_msk_instead_of_redis(self):
        terraform_files = [
            path.read_text(encoding="utf-8").lower()
            for path in (ROOT / "infra" / "terraform").rglob("*.tf")
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
        assert 'kafka_consumergroup_lag{consumergroup="message-worker"}' in alerts

    def test_argocd_gitops_contract_matches_local_ha_runtime(self):
        install_script = read_text("k8s/scripts/install-argocd.ps1")
        bootstrap_script = read_text("k8s/scripts/bootstrap-argocd-app.ps1")
        quick_start = read_text("scripts/quick_start_gitops.ps1")
        app_example = read_text("k8s/argocd/application-messaging-portfolio-local-ha.example.yaml")
        gitops_docs = read_text("docs/GITOPS.md")

        for document in (bootstrap_script, quick_start, app_example, gitops_docs):
            assert "master" in document
            assert "dev-kafka" not in document

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
        assert "`passed with warnings`는 실패가 아닙니다" in checklist


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
            "Kafka Broker Count",
            "Kafka Consumer Group Lag",
            "Kafka Topic Partitions",
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
            'messaging_deployment_profile_info{profile="k8s-demo-lite"} == 1',
            "unless on()",
        ):
            assert threshold in alerts

    def test_alert_rules_are_embedded_in_both_kubernetes_manifest_paths(self):
        app_manifest = read_text("k8s/app/manifests-ha.yaml")
        gitops_manifest = read_text("k8s/gitops/base/manifests-ha.yaml")

        for manifest in (app_manifest, gitops_manifest):
            assert 'messaging_deployment_profile_info{profile="k8s-demo-lite"} == 1' in manifest
            assert "unless on()" in manifest
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

        for document in (reliability, runbook, test_results):
            assert "API 5xx" in document
            assert "accepted-to-persisted" in document
            assert "Kafka topic wait" in document
            assert "MessagingDlqReplayBlocked" in document
            assert "oldest_age_seconds" in document
            assert "> 600" in document
            assert "> 1800" in document

        for document in (observability, metrics_reference, runbook, test_results):
            assert "kafka-exporter" in document
            assert "kafka_consumergroup_lag" in document
            assert "kafka_brokers" in document

        for document in (metrics_reference, runbook, test_results):
            assert "GET /v1/dlq/ingress/summary" in document
            assert "oldest_age_seconds" in document

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
