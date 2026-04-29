# 서비스 프로세스 점검표

이 문서는 Kafka 이벤트 스트림 포트폴리오를 서비스 운영 흐름 기준으로 점검하기 위한 체크리스트입니다. 목적은 단순히 Pod가 떠 있는지 보는 것이 아니라, request intake부터 persistence, 장애 격리, 복구, 관측, GitOps 반영까지 전체 흐름이 연결되어 있는지 확인하는 것입니다.

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
  -Revision dev-kafka
```

설치가 끝난 뒤에는 이 문서의 점검 명령을 실행합니다. `check_portfolio_status.ps1`가 실패하면 출력의 마지막 실패 구간을 먼저 봅니다. 예를 들어 `Argo CD GitOps`에서 실패하면 GitOps sync 문제이고, `Prometheus and Kafka exporter`에서 실패하면 metric scrape 또는 Kafka exporter 문제입니다.

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

`postgres-backups` PVC가 `Pending`이어도 첫 backup CronJob consumer 전이라면 local-path `WaitForFirstConsumer`의 정상 warning으로 봅니다.

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

Portfolio status check passed with warnings:
- postgres-backups PVC is Pending until the first backup CronJob consumer is scheduled. This is expected with local-path WaitForFirstConsumer.
```

`passed with warnings`는 실패가 아닙니다. 현재 warning은 backup PVC가 아직 첫 CronJob 실행 전이라 binding을 기다리는 상태를 의미합니다.

### 이상 신호를 읽는 법

| 출력 구간 | 이상 신호 | 의미 |
| --- | --- | --- |
| `Application readiness` | `status`가 `ready` 아님 | API intake 또는 persistence dependency 문제 |
| `Argo CD GitOps` | `Synced / Healthy` 아님 | Git desired state와 live state 불일치 또는 unhealthy resource |
| `Core workloads` | ready 수가 desired보다 낮음 | rollout, readiness, resource 부족, image 문제 |
| `Autoscaling` | `worker-keda` Ready가 아님 | KEDA 또는 Kafka external metric 문제 |
| `Prometheus and Kafka exporter` | `up=0` 또는 query no data | scrape target, service, exporter, Prometheus 설정 문제 |
| `Prometheus and Kafka exporter` | `kafka_brokers < 3` | 로컬 Kafka HA topology 약화 |
| `Prometheus and Kafka exporter` | `consumer_lag > 100` | Worker가 ingress topic을 따라잡지 못하는 상태 |
| `Backup PVC` | `Pending` 외 다른 비정상 phase | backup storage 점검 필요 |

## 프로세스별 점검

| 프로세스 | 확인 명령 / 지표 | 통과 기준 | 실패 시 이동 |
| --- | --- | --- | --- |
| Cluster / GitOps | `scripts/check_portfolio_status.ps1` | Argo CD `Synced / Healthy` | [RUNBOOK.md](RUNBOOK.md)의 공통 확인 |
| API readiness | `Invoke-RestMethod http://localhost/health/ready` | `status=ready` | Kafka Intake / PostgreSQL 절차 |
| API 계약 | `scripts/test_api_contracts.ps1 -SkipReset` | auth, stream, request status, DLQ summary 계약 통과 | API Contract 실패 |
| Event intake | `scripts/smoke_test.ps1 -SkipReset` | `202 Accepted` 후 persisted | Kafka Intake 장애 |
| Kafka broker | Prometheus `kafka_brokers` | 로컬 HA 기준 `3` | Kafka Intake 장애 |
| Consumer lag | Prometheus `kafka_consumergroup_lag` | lag가 낮거나 감소 | Worker Consumer Lag 증가 |
| Worker persistence | `messaging_event_persist_lag_seconds` | p95가 기준선 안에 있음 | PostgreSQL / Pgpool 장애 |
| Stream ordering | `scripts/test_stream_ordering.ps1 -EventCount 100 -SkipReset` | `stream_seq 1..100` | Worker retry / ordering 경계 확인 |
| DLQ flow | `scripts/test_dlq_flow.ps1 -SkipReset` | poison event가 Kafka DLQ에 도달 | DLQ 증가 / Replay Guard |
| DLQ replay guard | `scripts/test_dlq_replay_guard.ps1 -SkipReset` | max replay event가 자동 replay에서 제외 | DLQ 증가 / Replay Guard |
| Autoscaling | `kubectl get hpa -n messaging-app`, `kubectl get scaledobject -n messaging-app` | API HPA / Worker KEDA 조회 가능 | Resource Contention / KEDA 확인 |
| Observability | Grafana `Messaging Portfolio Operations Overview` | 주요 패널 값 표시 | [OBSERVABILITY.md](OBSERVABILITY.md) |
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
- accepted-to-persisted lag가 기준선과 크게 벗어나지 않는지
- 부하 중 API HPA와 Worker KEDA가 조회 가능한지

## 운영자가 보는 화면

| 화면 | 먼저 볼 항목 |
| --- | --- |
| Argo CD | `messaging-portfolio-local-ha` `Synced / Healthy` |
| Grafana | Kafka Broker Count, Kafka Consumer Group Lag, Accepted To Persisted Lag, DLQ Events And Replay |
| Prometheus | `up`, alert rules, `kafka_brokers`, `kafka_consumergroup_lag` |
| Kubernetes | Deployment ready, StatefulSet ready, HPA desired/current, ScaledObject Ready |
| API | `/health/ready`, `/docs`, `/openapi.json`, `/v1/event-requests/{request_id}` |

## 실패 해석 기준

- `check_portfolio_status.ps1` 실패: 먼저 클러스터 / Argo CD / readiness / scrape 중 어디서 멈췄는지 봅니다.
- Smoke 실패: Kafka append, Worker consume, PostgreSQL persistence 경로를 순서대로 봅니다.
- Ordering 실패: Kafka key, partition, Worker inline retry, offset commit 경계를 봅니다.
- DLQ 실패: Worker failure reason, DLQ topic, DLQ Replayer 상태를 봅니다.
- Alert probe 실패: metric scrape, Prometheus rule load, alert `for` 시간, kube-state-metrics 상태를 봅니다.
- Performance 실패: 기능 오류가 아니라 capacity / resource contention / DB path 병목일 수 있으므로 [TEST_RESULTS.md](TEST_RESULTS.md)의 측정 환경과 비교합니다.

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
