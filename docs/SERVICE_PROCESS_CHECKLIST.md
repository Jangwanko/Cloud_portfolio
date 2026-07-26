# 서비스 프로세스 점검표

Reliable Event Processing System 포트폴리오는 Pod 상태와 함께 generic event intake, persistence, 장애 격리, 복구, 관측, GitOps 반영까지 확인합니다.

## 빠른 전체 점검

### 처음 실행하는 경우

아직 로컬 클러스터를 만들지 않았다면 먼저 quick start를 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1
```

Argo CD 기반 GitOps 흐름까지 한 번에 확인하려면, 이 저장소를 접근 가능한 Git remote에 push한 뒤 아래처럼 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_gitops.ps1 `
  -RepoUrl https://github.com/<your-account>/<your-repo>.git `
  -Revision master
```

설치가 끝난 뒤에는 아래 점검 명령을 실행합니다. `check_portfolio_status.ps1`가 실패하면 출력의 마지막 실패 구간을 먼저 봅니다. 예를 들어 `Argo CD GitOps`에서 실패하면 GitOps sync 문제이고, `Prometheus and Kafka exporter`에서 실패하면 metric scrape 또는 Kafka exporter 문제입니다.

먼저 현재 클러스터가 데모와 운영 점검을 진행할 수 있는 상태인지 확인합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_portfolio_status.ps1
```

통과 기준:
- API readiness가 `ready`
- Argo CD Application이 `Synced / Healthy`
- API, Worker, Kafka, PostgreSQL, Pgpool, Grafana, Prometheus, kafka-exporter가 ready
- Prometheus scrape가 `up=1`
- `kafka_brokers=3`
- `message-worker consumer_lag=0` 또는 낮은 값

새 cluster의 `postgres-backups` PVC가 `Pending`이어도 첫 backup consumer 전이라면 local-path `WaitForFirstConsumer`의 정상 warning으로 봅니다. 2026-07-21 현재 local PVC는 수동 backup Job 실행 뒤 `Bound`입니다.

정상 출력 예시는 아래와 같습니다.

```text
==> Application readiness
api readiness=ready

==> Argo CD GitOps
application/messaging-portfolio-local-ha sync=Synced health=Healthy revision=<commit>

==> Prometheus and Kafka exporter
up{job="api"}=1
up{job="worker"}=1
up{job="kafka-exporter"}=1
kafka_brokers=3
message-worker consumer_lag=0

Portfolio status check passed.
```

Fresh install에서 PVC가 첫 consumer를 기다릴 때만 위 `Pending` warning을 허용합니다. Backup Job 실행 뒤에도 `Pending`이거나 다른 비정상 phase이면 통과로 간주하지 않습니다.

Readiness state는 HTTP status와 reason을 함께 읽습니다.

- `ready` / HTTP `200`: schema startup 완료, Kafka reachable, PostgreSQL HA guardrail 충족, non-local 기본 secret 미사용
- `degraded` / HTTP `200`: PostgreSQL primary unavailable, ready/synchronous standby 부족, replication byte lag 초과
- `not_ready` / HTTP `503`: schema startup 미완료, Kafka unreachable, non-local 환경의 unsafe auth secret 사용
- Worker, notification-worker, materialized cache 상태: response의 운영 정보이며 readiness state 결정에서는 제외

### 이상 신호를 읽는 법

| 출력 구간 | 이상 신호 | 의미 |
| --- | --- | --- |
| `Application readiness` | `status=degraded` | Kafka intake는 가능하지만 PostgreSQL HA / replication guardrail 이탈 |
| `Application readiness` | `status=not_ready`, HTTP `503` | schema, Kafka 또는 non-local auth secret hard failure |
| `Argo CD GitOps` | `Synced / Healthy` 아님 | Git desired state와 live state 불일치 또는 unhealthy resource |
| `Core workloads` | ready 수가 desired보다 낮음 | rollout, readiness, resource 부족, image 문제 |
| `Autoscaling` | `worker-keda` Ready가 아님 | KEDA 또는 Kafka external metric 문제 |
| `Prometheus and Kafka exporter` | `up=0` 또는 query no data | scrape target, service, exporter, Prometheus 설정 문제 |
| `Prometheus and Kafka exporter` | `kafka_brokers < 3` | 로컬 Kafka HA topology 약화 |
| `Prometheus and Kafka exporter` | `consumer_lag > 100` | Worker가 ingress topic을 따라잡지 못하는 상태 |
| `Backup PVC` | 첫 backup consumer 실행 뒤에도 phase가 `Bound`가 아님 | backup Job consumer와 storage provisioning 점검 필요 |

PostgreSQL StatefulSet 재시작/scale-up 뒤에는 pod ready 수와 함께 `postgres.sync_standby_count >= 1`을 확인합니다. `0`이면 `scripts/configure_postgres_sync.ps1`로 모든 pod의 persisted sync 설정을 복원하고 readiness가 `ready`로 돌아온 뒤 다음 절차를 진행합니다.

## 프로세스별 점검

| 프로세스 | 확인 명령 / 지표 | 통과 기준 | 실패 시 이동 |
| --- | --- | --- | --- |
| Cluster / GitOps | `scripts/check_portfolio_status.ps1` | Argo CD `Synced / Healthy` | [RUNBOOK.md](RUNBOOK.md)의 공통 확인 |
| API readiness | `Invoke-RestMethod http://localhost/health/ready` | HTTP `200`, `status=ready` | reason별 schema / Kafka / secret / PostgreSQL 절차 |
| API 계약 | `scripts/test_api_contracts.ps1 -SkipReset` | auth, stream, request status, DLQ summary 계약 통과 | API Contract 실패 |
| Event intake | `scripts/smoke_test.ps1 -SkipReset` | `202 Accepted` 후 persisted | Kafka Intake 장애 |
| Kafka broker | Prometheus `kafka_brokers` | 로컬 HA 기준 `3` | Kafka Intake 장애 |
| Consumer lag | Prometheus `kafka_consumergroup_lag` | lag가 낮거나 감소 | Worker Consumer Lag 증가 |
| Worker persistence / commit-observed lag | `messaging_event_persist_lag_seconds{job="worker"}` | p95가 기준선 안에 있음 | PostgreSQL / Pgpool persistence 구간 |
| Stream ordering | `scripts/test_stream_ordering.ps1 -EventCount 100 -SkipReset` | `stream_seq 1..100` | Worker retry / ordering 경계 확인 |
| DLQ flow | `scripts/test_dlq_flow.ps1 -SkipReset` | poison event가 Kafka DLQ에 도달 | DLQ 증가 / Replay Guard |
| DLQ replay guard | `scripts/test_dlq_replay_guard.ps1 -SkipReset` | max replay event가 자동 replay에서 제외 | DLQ 증가 / Replay Guard |
| Autoscaling | `kubectl get hpa -n messaging-app`, `kubectl get scaledobject -n messaging-app` | API HPA / Worker KEDA 조회 가능 | Resource Contention / KEDA 확인 |
| Observability | Grafana `Reliable Event Processing Operations Overview` | 주요 패널 값 표시 | [OBSERVABILITY.md](OBSERVABILITY.md) |
| Alert wiring | `scripts/test_operational_alerts.ps1 -SkipReset` | DLQ / unavailable replica alert 관측 | 운영 Alert Probe |
| Backup | `kubectl get cronjob,pvc -n messaging-app` | `postgres-weekly-backup` 존재 | PostgreSQL 백업 절차 |
| Restore | `scripts/restore_postgres_k8s.ps1 -BackupFile ... -Force` | backup SQL 적용 가능 | PostgreSQL 복구 절차 |
| Performance baseline | `scripts/run_kafka_performance_suite.ps1` | TEST_RESULTS 기준선과 비교 | 성능 튜닝 항목 |

## 권장 점검 순서

### 1. 데모 전 2분 점검

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_portfolio_status.ps1
powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1 -SkipReset
```

확인하는 것:
- 클러스터와 GitOps desired state가 정상인지
- API가 Kafka ingress topic에 event를 append하는지
- Worker가 Kafka event를 PostgreSQL에 persisted 하는지

### 2. 기능 정확성 점검

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_api_contracts.ps1 -SkipReset
powershell -ExecutionPolicy Bypass -File scripts/test_stream_ordering.ps1 -EventCount 100 -SkipReset
powershell -ExecutionPolicy Bypass -File scripts/test_dlq_flow.ps1 -SkipReset
powershell -ExecutionPolicy Bypass -File scripts/test_dlq_replay_guard.ps1 -SkipReset
```

확인하는 것:
- API 응답 계약이 유지되는지
- 같은 stream의 순차 보장이 유지되는지
- 실패 event가 DLQ로 격리되는지
- replay guard가 무한 재주입을 막는지

### 3. 장애 복구 점검

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_db_down.ps1 -SkipReset
powershell -ExecutionPolicy Bypass -File scripts/test_incident_signals.ps1 -SkipDbOutage
```

확인하는 것:
- PostgreSQL write path 장애 중에도 Kafka append path 기준 degraded 동작이 유지되는지
- 장애 신호가 Prometheus alert와 Grafana 패널에 연결되는지
- 잘못된 rollout이 unavailable replica 신호로 잡히는지

### 4. 성능 기준선 점검

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_kafka_performance_suite.ps1
```

확인하는 것:
- Kafka intake 100 VU / 30초 기준선을 재현하는지
- Worker histogram이 payload `queued_at`부터 PostgreSQL `commit()` 반환 직후까지의 기준선과 크게 벗어나지 않는지
- 현재 suite의 `accepted_to_status_observed_ms`가 polling/network 포함 client 관측 지연으로 기록되는지
- 2026-06 `accepted-to-persisted` 값은 PostgreSQL row `created_at` / row-visible proxy로만 비교되는지
- 부하 중 API HPA와 Worker KEDA가 조회 가능한지

2026-06 raw performance output의 event status `200`은 route에 `202 Accepted` 계약을 명시하기 전 수집한 historical pre-contract-fix evidence입니다. 현재 HTTP status는 새 build의 contract test와 새 performance run으로 검증합니다.

## 운영자가 보는 화면

| 화면 | 먼저 볼 항목 |
| --- | --- |
| Argo CD | `messaging-portfolio-local-ha` `Synced / Healthy` |
| Grafana | Kafka Broker Count, Kafka Consumer Group Lag, `API Queue To DB Commit`(Worker commit-observed lag), DLQ Events And Replay |
| Prometheus | `up`, alert rules, `kafka_brokers`, `kafka_consumergroup_lag` |
| Kubernetes | Deployment ready, StatefulSet ready, HPA desired/current, ScaledObject Ready |
| API | `/health/ready`, `/docs`, `/openapi.json`, `/v1/event-requests/{request_id}` |

## 실패 해석 기준

- `check_portfolio_status.ps1` 실패: 먼저 클러스터 / Argo CD / readiness / scrape 중 어디서 멈췄는지 봅니다.
- Smoke 실패: Kafka append, Worker consume, PostgreSQL persistence 경로를 순서대로 봅니다.
- Ordering 실패: Kafka key, partition, Worker inline retry, offset commit 경계를 봅니다.
- DLQ 실패: Worker failure reason, DLQ topic, DLQ Replayer 상태를 봅니다.
- DLQ summary: `scope=recent_log_sample`, `user_filtered=true`, `oldest_sample_age_seconds` 확인. unresolved depth나 미해결 event SLO로 해석 제외
- Alert probe 실패: metric scrape, Prometheus rule load, alert `for` 시간, kube-state-metrics 상태를 봅니다.
- Performance 실패: capacity / resource contention / DB path 병목부터 확인하고 [TEST_RESULTS.md](TEST_RESULTS.md)의 측정 환경과 비교합니다. 기능 오류 판정은 contract와 persistence 검증 결과를 함께 사용합니다.

## 최종 판정

아래가 모두 성립하면 로컬 운영형 포트폴리오로 설명할 수 있는 상태로 봅니다.

- GitOps desired state가 `Synced / Healthy`
- Kafka broker 3개와 topic bootstrap이 정상
- API request가 Kafka ingress topic을 거쳐 PostgreSQL persisted로 이어짐
- 같은 stream ordering이 유지됨
- DLQ와 replay guard가 동작함
- Prometheus / Grafana / kafka-exporter가 운영 지표를 보여줌
- 장애 probe가 alert 신호로 연결됨
- backup / restore 절차가 문서화되어 있음
- 성능 기준선은 기능 검증과 분리해서 측정됨
