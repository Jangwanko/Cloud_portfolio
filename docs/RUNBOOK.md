# 운영 Runbook

이 문서는 Kafka event stream pipeline을 운영할 때 장애를 빠르게 분류하고 복구하기 위한 절차입니다. 기본 순서는 `증상 확인 -> 영향 범위 판단 -> 원인 확인 -> 조치 -> 복구 확인`입니다.

## 공통 확인 순서

1. API readiness를 확인합니다.

```powershell
Invoke-RestMethod http://localhost/health/ready
```

2. Kubernetes workload 상태를 확인합니다.

```powershell
kubectl get pods -n messaging-app
kubectl get deploy,statefulset -n messaging-app
kubectl get hpa -n messaging-app
```

3. Kafka / Worker / PostgreSQL 지표를 함께 봅니다.

```powershell
kubectl -n messaging-app logs deploy/api --tail=100
kubectl -n messaging-app logs deploy/worker --tail=100
kubectl -n messaging-app logs deploy/dlq-replayer --tail=100
```

4. DLQ에 event가 쌓였는지 확인합니다.

```powershell
Invoke-RestMethod -Headers @{ Authorization = "Bearer <token>" } http://localhost/v1/dlq/ingress?limit=20
```

## Kafka Intake 장애

대표 증상:
- `/health/ready`가 `not_ready`로 응답합니다.
- event write가 `503 Kafka unavailable`로 실패합니다.
- k6 또는 smoke test에서 produce timeout이 발생합니다.

확인:

```powershell
kubectl get statefulset kafka -n messaging-app
kubectl get pods -n messaging-app -l app=kafka
kubectl logs -n messaging-app statefulset/kafka --tail=100
kubectl get job kafka-topic-bootstrap -n messaging-app
```

조치:
- Kafka broker pod가 `Ready`가 아니면 rollout / restart / resource 부족 여부를 먼저 확인합니다.
- topic bootstrap job이 실패했다면 job log를 확인하고 topic 설정을 다시 적용합니다.
- Kafka가 복구되기 전에는 API가 새 event를 fail-fast로 거절하는 것이 정상입니다.

복구 확인:
- `/health/ready`의 Kafka 항목이 정상으로 돌아와야 합니다.
- `scripts/smoke_test.ps1 -SkipReset`이 통과해야 합니다.
- `scripts/test_api_contracts.ps1 -SkipReset`이 통과해야 합니다.

## PostgreSQL / Pgpool 장애

대표 증상:
- `/health/ready`가 `degraded` 또는 `not_ready`로 응답합니다.
- Worker가 persistence retry를 반복합니다.
- request status가 `queued`에 머물거나 DLQ로 이동합니다.

확인:

```powershell
kubectl get pods -n messaging-app -l app.kubernetes.io/name=postgresql-ha
kubectl get pods -n messaging-app -l app.kubernetes.io/component=pgpool
kubectl logs -n messaging-app deploy/messaging-postgresql-ha-pgpool --tail=100
kubectl logs -n messaging-app statefulset/messaging-postgresql-ha-postgresql --tail=100
```

조치:
- Pgpool pod 재시작, PostgreSQL primary reachable 여부, standby 수, replication lag를 순서대로 확인합니다.
- PostgreSQL write path가 불안정하면 Worker는 inline retry를 수행합니다.
- inline retry 한도를 넘은 event는 DLQ로 이동하므로, DB 복구 후 DLQ reason을 확인합니다.

복구 확인:
- `/health/ready`가 `ready`로 돌아와야 합니다.
- `scripts/test_db_down.ps1 -SkipReset`이 통과해야 합니다.
- DLQ event의 `failed_reason`이 반복 증가하지 않아야 합니다.

## Worker Consumer Lag 증가

대표 증상:
- event request는 `202 Accepted`를 받지만 persisted 상태 전환이 느립니다.
- `messaging_event_persist_lag_seconds` p95/p99가 증가합니다.
- Kafka consumer lag 기반으로 Worker replica가 증가합니다.

확인:

```powershell
kubectl get deploy worker -n messaging-app
kubectl get scaledobject -n messaging-app
kubectl logs -n messaging-app deploy/worker --tail=100
```

조치:
- Worker replica가 늘지 않으면 KEDA external metric과 ScaledObject 상태를 확인합니다.
- Worker가 늘었는데도 lag가 줄지 않으면 PostgreSQL persistence path 병목을 의심합니다.
- 같은 stream의 앞 event가 retry 중이면 같은 partition 뒤 event는 대기할 수 있습니다. 이 동작은 stream ordering을 지키기 위한 정상적인 backpressure입니다.

복구 확인:
- `scripts/test_stream_ordering.ps1 -EventCount 100 -SkipReset`이 통과해야 합니다.
- persisted timeout이나 consumer lag가 안정 구간으로 내려와야 합니다.

## DLQ 증가 / Replay Guard

대표 증상:
- `/v1/dlq/ingress`의 `count`가 증가합니다.
- `failed_reason`이 `room_sequence_gap` 또는 `transient_error_max_retries:*`로 반복됩니다.
- `replayable=false`인 event가 보입니다.

확인:

```powershell
Invoke-RestMethod -Headers @{ Authorization = "Bearer <token>" } http://localhost/v1/dlq/ingress?limit=20
kubectl logs -n messaging-app deploy/dlq-replayer --tail=100
kubectl -n messaging-app exec deploy/dlq-replayer -- printenv DLQ_REPLAY_MAX_COUNT
```

조치:
- `replay_count < max_replay_count`이고 원인이 복구되었다면 DLQ Replayer가 ingress topic으로 재주입합니다.
- `replayable=false`이면 자동 replay를 멈춘 상태입니다. payload와 `failed_reason`을 보고 데이터 보정 또는 수동 처리 여부를 결정합니다.
- 같은 reason이 반복되면 replay보다 원인 수정이 먼저입니다.

복구 확인:
- `scripts/test_dlq_flow.ps1 -SkipReset`이 통과해야 합니다.
- `scripts/test_dlq_replay_guard.ps1 -SkipReset`이 통과해야 합니다.

## API Contract 실패

대표 증상:
- smoke test는 통과하지만 client가 예상하던 field를 찾지 못합니다.
- 인증 실패, 권한 실패, 없는 stream 처리의 HTTP status가 바뀝니다.
- event request status 또는 DLQ summary 형태가 달라집니다.

확인:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_api_contracts.ps1 -SkipReset
```

조치:
- 공개 API 응답 field를 바꿨다면 문서, client 예제, test expectation을 함께 갱신합니다.
- 의도하지 않은 변경이면 endpoint response shape을 기존 contract로 되돌립니다.
- 실패 status code가 바뀌면 인증 / membership / state-store 경로를 우선 확인합니다.

복구 확인:
- `scripts/test_api_contracts.ps1 -SkipReset`이 통과해야 합니다.
- `scripts/run_recommended_tests.ps1 -SkipK6`가 통과해야 합니다.

## 낮은 사양 / Resource Contention

대표 증상:
- rollout timeout, `CrashLoopBackOff`, `OOMKilled`가 발생합니다.
- `/health/ready` timeout, `degraded`, `not_ready`가 길게 지속됩니다.
- persisted timeout, DLQ timeout, k6 p95/p99 threshold 실패가 발생합니다.

확인:

```powershell
kubectl top nodes
kubectl top pods -n messaging-app
kubectl describe pod -n messaging-app <pod-name>
```

조치:
- Docker Desktop에 할당된 CPU / memory가 현재 기준선보다 낮은지 확인합니다.
- 기능 검증은 `scripts/run_recommended_tests.ps1 -SkipK6`로 먼저 분리합니다.
- 성능 기준선 재현은 Kafka / PostgreSQL / Worker가 안정화된 뒤 별도로 실행합니다.

복구 확인:
- pod restart count가 증가하지 않아야 합니다.
- 기능 검증이 먼저 통과하고, 성능 suite는 별도 기준으로 다시 측정합니다.

## 장애 후 최종 검증

장애 복구 후에는 아래 순서로 확인합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_recommended_tests.ps1 -SkipK6
powershell -ExecutionPolicy Bypass -File scripts/run_kafka_performance_suite.ps1
```

성능 suite는 기능 검증이 아니라 기준선 측정입니다. 기능 검증이 실패하면 먼저 원인을 수정하고, 기능 검증이 통과한 뒤 성능 수치를 다시 기록합니다.
