# Kafka Design Notes

이 문서는 Kafka-native 설계와 검증 기록입니다.

## Why Kafka

이 포트폴리오의 핵심 주제는 request intake와 persistence를 분리해 장애 전파를 줄이고, 실패한 event를 재처리 가능한 형태로 남기는 것입니다.

Kafka를 선택한 이유:

- event log로 요청 수락 이력을 보존할 수 있습니다.
- partition key로 stream 단위 ordering boundary를 명확히 둘 수 있습니다.
- Worker consumer group으로 처리량을 수평 확장할 수 있습니다.
- consumer lag를 backlog 신호로 사용해 KEDA scaling 기준을 세울 수 있습니다.
- DLQ topic과 replay flow를 운영 가능한 실패 복구 경로로 만들 수 있습니다.

## Current Flow

```text
Client
-> Ingress nginx
-> FastAPI API
-> Kafka ingress topic
-> Worker consumer group
-> Pgpool
-> PostgreSQL HA
```

실패 시:

```text
Worker failure
-> retry
-> Kafka DLQ topic
-> DLQ Replayer
-> Kafka ingress topic
-> Worker reprocess
```

관측 / 스케일링:

```text
API / Worker metrics
-> Prometheus scrape
-> Grafana dashboard
-> KEDA Kafka scaler
-> Worker replica scale-out
```

## Kafka Runtime

현재 dev 환경은 3-broker KRaft Kafka StatefulSet을 사용합니다.

```powershell
kubectl apply -f k8s/gitops/base/kafka-ha.yaml
kubectl -n messaging-app rollout status statefulset/kafka --timeout=600s
kubectl -n messaging-app wait --for=condition=complete job/kafka-topic-bootstrap --timeout=300s
```

API / Worker / DLQ Replayer는 Kafka backend 환경변수로 실행합니다.

```powershell
kubectl -n messaging-app set env deployment/api KAFKA_BOOTSTRAP_SERVERS=kafka.messaging-app.svc.cluster.local:9092
kubectl -n messaging-app set env deployment/worker KAFKA_BOOTSTRAP_SERVERS=kafka.messaging-app.svc.cluster.local:9092
kubectl -n messaging-app set env deployment/dlq-replayer KAFKA_BOOTSTRAP_SERVERS=kafka.messaging-app.svc.cluster.local:9092
```

Worker autoscaling은 `k8s/app/manifests-ha.yaml`에 포함된 Kafka lag 기준 ScaledObject를 사용합니다.

## Design Details

- Kafka ingress topic: `message-ingress`
- Kafka DLQ topic: `message-ingress-dlq`
- Consumer group: `message-worker`
- KEDA lag threshold: `400`
- Message key: `stream_id`
- Offset commit: Worker 처리 성공 후 commit
- DLQ listing: `GET /v1/dlq/ingress?limit=5`

## Verified Result

2026-04-26 실행 결과:

- Kafka broker rollout: pass
- API / Worker / DLQ Replayer Kafka backend rollout: pass
- readiness: `queue_backend=kafka`, Kafka reachable, PostgreSQL reachable 확인
- smoke test: pass
- Kafka DLQ listing: pass
- DLQ replay trace: pass
- HPA / metrics sanity: pass

## Load Test Finding

초기 Kafka 실험에서는 API가 request status / idempotency / sequence를 PostgreSQL hot path에 두면서 Pgpool 병목이 먼저 드러났습니다. 이후 API intake를 Kafka append 중심으로 정리했습니다.

최신 Kafka performance baseline은 아래 suite로 측정했습니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_kafka_performance_suite.ps1
```

2026-04-28 실행 결과:

- profile: `single500`
- concurrent users: `100`
- duration: `30s`
- idempotency header: disabled
- total HTTP requests: `30922`
- event status 200: `30916`
- event status 503: `2`
- error rate: `0.01%`
- average latency: `46.50ms`
- p95 latency: `92.25ms`
- API HPA final replicas: `8`
- Worker KEDA final replicas: `8`

비교 진단:

- `X-Idempotency-Key`를 켠 부하에서는 PostgreSQL state-store path가 API hot path로 다시 들어옵니다.
- 이 경우 낮은 부하에서도 `503`이 발생했고, 100 VU에서는 Pgpool 재시작 압력과 높은 실패율이 나타났습니다.
- 따라서 Kafka-native 완성형에서는 idempotency / request status state path를 Kafka append path와 분리하는 설계가 중요합니다.

변경 해석:

- API는 Kafka 모드에서 stream sequence를 선점하지 않습니다.
- Worker가 persistence 시점에 sequence를 배정합니다.
- API의 accepted status store는 기본값에서 synchronous DB hot path에 두지 않습니다.
- request idempotency claim도 기본값에서는 API hot path에서 수행하지 않고 Worker persistence path가 최종 idempotency를 처리합니다.

## Design Direction

Kafka-native 완성형으로 가려면 event log path와 low-latency state path를 분리해야 합니다.

후보:

- Kafka compacted topic으로 request status / idempotency state 관리
- 별도 low-latency state store 도입
- API 응답 계약을 단순 `accepted event id` 중심으로 줄여 state lookup 최소화
- sequence allocation을 partition-local ordering 기반으로 재설계

이 결과는 Kafka를 선택할 때 event log뿐 아니라 state path까지 함께 설계해야 한다는 결론을 보여줍니다.
