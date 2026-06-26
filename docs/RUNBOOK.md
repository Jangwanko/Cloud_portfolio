# 운영 Runbook

Kafka event stream pipeline 장애 대응 순서:

- 증상 확인
- 영향 범위 판단
- 원인 확인
- 조치
- 복구 확인

## 공통 확인 순서

0. 전체 상태 한 번에 확인

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_portfolio_status.ps1
```

확인 항목:

- API readiness
- Argo CD `Synced / Healthy`
- workload ready
- KEDA
- Prometheus scrape
- kafka-exporter 지표

정상 운영 프로세스 전체 점검: [SERVICE_PROCESS_CHECKLIST.md](SERVICE_PROCESS_CHECKLIST.md)

1. API readiness 확인

```powershell
Invoke-RestMethod http://localhost/health/ready
```

2. Kubernetes workload 상태 확인

```powershell
kubectl get pods -n messaging-app
kubectl get deploy,statefulset -n messaging-app
kubectl get hpa -n messaging-app
```

3. Kafka / Worker / PostgreSQL 지표 동시 확인

```powershell
kubectl -n messaging-app logs deploy/api --tail=100
kubectl -n messaging-app logs deploy/worker --tail=100
kubectl -n messaging-app logs deploy/dlq-replayer --tail=100
```

4. DLQ event 적재 여부 확인

```powershell
Invoke-RestMethod -Headers @{ Authorization = "Bearer <token>" } http://localhost/v1/dlq/ingress?limit=20
```

## Kafka Intake 장애

대표 증상:
- `/health/ready` `not_ready` 응답
- event write `503 Kafka unavailable` 실패
- k6 또는 smoke test produce timeout 발생

확인:

```powershell
kubectl get statefulset kafka -n messaging-app
kubectl get pods -n messaging-app -l app=kafka
kubectl logs -n messaging-app statefulset/kafka --tail=100
kubectl get job kafka-topic-bootstrap -n messaging-app
```

조치:
- Kafka broker pod `Ready` 아님: rollout / restart / resource 부족 여부 확인
- topic bootstrap job 실패: job log 확인, topic 설정 재적용
- Kafka 복구 전: API 새 event fail-fast 거절 정상

복구 확인:
- `/health/ready` Kafka 항목 정상
- `scripts/smoke_test.ps1 -SkipReset` 통과
- `scripts/test_api_contracts.ps1 -SkipReset` 통과

## PostgreSQL / Pgpool 장애

대표 증상:
- `/health/ready` `degraded` 또는 `not_ready` 응답
- Worker persistence retry 반복
- request status `queued` 정체 또는 DLQ 이동

확인:

```powershell
kubectl get pods -n messaging-app -l app.kubernetes.io/name=postgresql-ha
kubectl get pods -n messaging-app -l app.kubernetes.io/component=pgpool
kubectl logs -n messaging-app deploy/messaging-postgresql-ha-pgpool --tail=100
kubectl logs -n messaging-app statefulset/messaging-postgresql-ha-postgresql --tail=100
```

조치:
- Pgpool pod 재시작, PostgreSQL primary reachable, standby 수, replication lag 순서 확인
- PostgreSQL write path 불안정: Worker inline retry
- inline retry 한도 초과 event: DLQ 이동
- DB 복구 후: DLQ reason 확인

복구 확인:
- `/health/ready` `ready` 복귀
- `scripts/test_db_down.ps1 -SkipReset` 통과
- DLQ event `failed_reason` 반복 증가 없음

## Worker Consumer Lag 증가

대표 증상:
- event request는 `202 Accepted`를 받지만 persisted 상태 전환이 느립니다.
- `messaging_event_persist_lag_seconds` p95/p99 증가
- Kafka consumer lag 기반 Worker replica 증가

확인:

```powershell
kubectl get deploy worker -n messaging-app
kubectl get scaledobject -n messaging-app
kubectl logs -n messaging-app deploy/worker --tail=100
```

조치:
- Worker replica 미증가: KEDA external metric과 ScaledObject 상태 확인
- Worker 증가 후 lag 유지: PostgreSQL persistence path 병목 우선 의심
- 같은 stream 앞 event retry 중: 같은 partition 뒤 event 대기 가능
- 위 대기: stream ordering을 지키기 위한 정상 backpressure

복구 확인:
- `scripts/test_stream_ordering.ps1 -EventCount 100 -SkipReset` 통과
- persisted timeout 또는 consumer lag 안정 구간 복귀

## DLQ 증가 / Replay Guard

대표 증상:
- `/v1/dlq/ingress` `count` 증가
- `failed_reason` `room_sequence_gap` 또는 `transient_error_max_retries:*` 반복
- `replayable=false` event 노출

확인:

```powershell
Invoke-RestMethod -Headers @{ Authorization = "Bearer <token>" } http://localhost/v1/dlq/ingress?limit=20
kubectl logs -n messaging-app deploy/dlq-replayer --tail=100
kubectl -n messaging-app exec deploy/dlq-replayer -- printenv DLQ_REPLAY_MAX_COUNT
```

조치:
- `replay_count < max_replay_count` + 원인 복구: DLQ Replayer ingress topic 재주입
- `replayable=false`: 자동 replay 중단 상태
- payload와 `failed_reason`: 데이터 보정 또는 수동 처리 여부 판단
- 같은 reason 반복: replay보다 원인 수정 우선

복구 확인:
- `scripts/test_dlq_flow.ps1 -SkipReset` 통과
- `scripts/test_dlq_replay_guard.ps1 -SkipReset` 통과

## API Contract 실패

대표 증상:
- smoke test 통과, client 예상 field 누락
- 인증 실패, 권한 실패, 없는 stream 처리의 HTTP status가 바뀝니다.
- event request status 또는 DLQ summary 형태가 달라집니다.

확인:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_api_contracts.ps1 -SkipReset
```

조치:
- 공개 API 응답 field 변경: 문서, client 예제, test expectation 함께 갱신
- 의도하지 않은 변경: endpoint response shape 기존 contract 복구
- 실패 status code 변경: 인증 / membership / state-store 경로 우선 확인

복구 확인:
- `scripts/test_api_contracts.ps1 -SkipReset` 통과
- `scripts/run_recommended_tests.ps1 -SkipK6` 통과

## 낮은 사양 / Resource Contention

대표 증상:
- rollout timeout, `CrashLoopBackOff`, `OOMKilled` 발생
- `/health/ready` timeout, `degraded`, `not_ready` 장기 지속
- persisted timeout, DLQ timeout, k6 p95/p99 threshold 실패

확인:

```powershell
kubectl top nodes
kubectl top pods -n messaging-app
kubectl describe pod -n messaging-app <pod-name>
```

조치:
- Docker Desktop CPU / memory 할당량 확인
- 기능 검증: `scripts/run_recommended_tests.ps1 -SkipK6`로 먼저 분리
- 성능 기준선 재현: Kafka / PostgreSQL / Worker 안정화 후 별도 실행

복구 확인:
- pod restart count 추가 증가 없음
- 기능 검증 우선 통과
- 성능 suite 별도 기준으로 재측정

## 장애 후 최종 검증

장애 복구 후 확인 순서:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_recommended_tests.ps1 -SkipK6
powershell -ExecutionPolicy Bypass -File scripts/run_kafka_performance_suite.ps1
```

성능 suite 기준:

- 기능 검증 제외
- 기준선 측정용
- 기능 검증 실패 시 원인 수정 우선
- 기능 검증 통과 후 성능 수치 재기록

## Alert 기준값과 첫 확인 지점

운영 알림 확인 순서:

- Grafana `Messaging Portfolio Operations Overview`에서 같은 이름의 지표 흐름 확인
- 관련 runbook 절차 진입
- 첫 조치 후 복구 확인 command 실행

| Alert | 기준 | 먼저 볼 패널 | 첫 조치 |
| --- | --- | --- | --- |
| `MessagingApi5xxRateWarning` | API 5xx ratio `> 1%` for 5m | API 5xx Ratio | API log와 Kafka/PostgreSQL health를 같이 확인 |
| `MessagingApiHigh5xxRate` | API 5xx ratio `> 5%` for 5m | API 5xx Ratio | intake 장애로 보고 Kafka Intake / PostgreSQL 절차 진입 |
| `MessagingApiP95LatencyHigh` | API p95 `> 2s` for 10m | API Latency | API pod CPU, DB pool, Kafka publish 지연 확인 |
| `MessagingApiP95LatencyCritical` | API p95 `> 4s` for 5m | API Latency | client 영향 장애로 보고 scale/resource 상태 확인 |
| `MessagingEventPersistLagHigh` | accepted-to-persisted p95 `> 5s` for 5m | Accepted To Persisted Lag | Worker 처리량과 PostgreSQL 상태 확인 |
| `MessagingEventPersistLagCritical` | accepted-to-persisted p95 `> 15s` for 5m | Accepted To Persisted Lag | persistence 장애로 보고 Worker / PostgreSQL 절차 진입 |
| `MessagingQueueWaitHigh` | Kafka topic wait p95 `> 10s` for 5m | Kafka Topic Wait Time | Worker replica와 KEDA desired replica 확인 |
| `MessagingQueueWaitCritical` | Kafka topic wait p95 `> 30s` for 5m | Kafka Topic Wait Time | backlog 장애로 보고 Worker Consumer Lag 절차 진입 |
| `MessagingDlqEventsIncreasing` | DLQ event 1건 이상 증가 | DLQ Events And Replay | failed_reason을 확인하고 replay 가능 여부 판단 |
| `MessagingDlqReplayBlocked` | `skipped_max_replay` 누적값 `> 0` | DLQ Events And Replay | 자동 replay 중단 상태로 보고 원인 수정 전 수동 재시도 금지 |
| `MessagingPodRestarting` | 15분 안에 pod restart 증가 | Pod Restarts (15m) | `kubectl describe pod`로 OOMKilled/CrashLoopBackOff 확인 |
| `MessagingDeploymentUnavailableReplicas` | 2분 이상 unavailable replica 존재 | Unavailable Replicas | rollout, PDB, node resource 상태 확인 |

## 운영 Alert Probe

운영 Alert Probe 목적:

- Prometheus rule 로드 확인에서 종료하지 않음
- 짧은 장애 신호 생성
- 실제 alert 상태 `firing` 전환 확인

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_operational_alerts.ps1 -SkipReset
```

검증하는 신호:

| Scenario | Expected alert | 확인 의미 |
| --- | --- | --- |
| poison event를 Kafka DLQ로 이동 | `MessagingDlqEventsIncreasing` | Worker -> Kafka DLQ metric과 alert rule이 연결됨 |
| max replay guard event 생성 | `MessagingDlqReplayBlocked` | 자동 replay 차단 상태가 운영 알림으로 노출됨 |
| 잘못된 `dlq-replayer` image rollout | `MessagingDeploymentUnavailableReplicas` | kube-state-metrics unavailable replica 지표와 alert rule이 연결됨 |

옵션:

- unavailable replica 시나리오 생략: `-SkipUnavailableReplicaScenario`
- 성격: 성능 측정 제외, 운영 신호 배선 검증

## DLQ Summary Triage

DLQ 알림 시 첫 확인:

- summary endpoint
- blocked / replayable / by_reason / oldest_age_seconds
- sample payload

```powershell
Invoke-RestMethod -Headers @{ Authorization = "Bearer <token>" } http://localhost/v1/dlq/ingress/summary?limit=200&sample_limit=5
```

Endpoint: `GET /v1/dlq/ingress/summary`

판단 순서:

| 확인 값 | 판단 |
| --- | --- |
| `blocked > 0` | 자동 replay가 막힌 event가 있으므로 `by_reason`과 sample payload를 먼저 확인 |
| `replayable > 0` | 원인이 복구된 뒤 DLQ Replayer가 ingress topic으로 재주입할 수 있는 후보 |
| `by_reason.room_sequence_gap` 증가 | 같은 stream ordering 경계에서 앞 event 실패 또는 잘못된 sequence 입력 확인 |
| `by_reason.transient_error_max_retries:*` 증가 | PostgreSQL / Pgpool / persistence path 장애 확인 |
| `oldest_age_seconds > 600` | DLQ event가 10분 이상 남은 warning 상태이므로 replay 조건과 원인 수정 여부 확인 |
| `oldest_age_seconds > 1800` | DLQ event가 30분 이상 남은 critical 상태이므로 자동 replay만 기다리지 말고 수동 처리 또는 원인 수정 결정 |
| `by_stream` 특정 stream 집중 | 해당 stream의 앞 event, membership, sequence 상태를 우선 조사 |

## Incident Signal Suite

Incident Signal Suite 목적:

- 개별 장애 테스트 제외
- 운영 신호 연결 상태 일괄 확인
- 장애 신호 배선과 복구 절차 검증

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_incident_signals.ps1 -SkipDbOutage
```

기본 구성:

| Scenario | 확인 신호 |
| --- | --- |
| PostgreSQL outage / recovery | DB 장애 중 accepted event가 복구 후 persisted 되는지 확인 |
| DLQ alert probe | `MessagingDlqEventsIncreasing`, `MessagingDlqReplayBlocked` alert 확인 |
| Worker bad rollout | `MessagingDeploymentUnavailableReplicas` pending/firing 확인 후 image 복구 |

옵션:

- 긴 DB 장애 시나리오 제외: `-SkipDbOutage`
- 성격: 성능 측정 제외, 장애 신호 배선과 복구 절차 검증

## Kafka Exporter 확인

Kafka backlog 의심 시 확인 기준:

- 앱 지표만으로 판단 보류
- kafka-exporter 지표 우선 확인
- Worker throughput과 DB persist latency 함께 확인

```powershell
Invoke-RestMethod "http://localhost/prometheus/api/v1/query?query=kafka_brokers"
Invoke-RestMethod "http://localhost/prometheus/api/v1/query?query=sum(kafka_consumergroup_lag%7Bconsumergroup%3D%22message-worker%22%7D)"
```

해석:

| Signal | 판단 |
| --- | --- |
| `kafka_brokers < 3` | broker topology가 로컬 HA 기준보다 낮음 |
| `kafka_consumergroup_lag` 증가 | Worker consumer group이 ingress topic을 따라잡지 못함 |
| lag 증가 + Worker throughput 낮음 | Worker replica / consumer loop 확인 |
| lag 증가 + DB persist latency 증가 | PostgreSQL / Pgpool persistence path 확인 |
