# Kafka 설계 기록

Kafka-native 설계는 API intake, Worker persistence, PostgreSQL HA, DLQ / replay, 관측 지표를 분리해서 검증합니다.

## Kafka를 선택한 이유

이 포트폴리오의 핵심 주제는 request intake와 persistence를 분리해 장애 전파를 줄이고, 실패한 event를 재처리 가능한 형태로 남기는 것입니다.

Kafka를 선택한 이유:

- event log로 요청 수락 이력을 보존할 수 있습니다.
- partition key로 stream 단위 ordering boundary를 명확히 둘 수 있습니다.
- Worker consumer group으로 처리량을 수평 확장할 수 있습니다.
- consumer lag를 backlog 신호로 사용해 KEDA scaling 기준을 세울 수 있습니다.
- DLQ topic과 replay flow를 운영 가능한 실패 복구 경로로 만들 수 있습니다.

## 현재 흐름

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

## Kafka runtime

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

## 설계 상세

- Kafka ingress topic: `message-ingress`
- Kafka DLQ topic: `message-ingress-dlq`
- Kafka request status compacted topic: `message-request-status`
- Kafka DB snapshot compacted topics: `message-snapshots`, `stream-snapshots`
- Consumer group: `message-worker`
- KEDA lag threshold: `100` for the local demo cluster
- Message key: `stream_id`
- Offset commit: Worker 처리 성공 후 commit
- DLQ listing: `GET /v1/dlq/ingress?limit=5`

## 검증 결과

2026-04-26 실행 결과:

- Kafka broker rollout: pass
- API / Worker / DLQ Replayer Kafka backend rollout: pass
- readiness: `queue_backend=kafka`, Kafka reachable, PostgreSQL reachable 확인
- smoke test: pass
- Kafka DLQ listing: pass
- DLQ replay trace: pass
- HPA / metrics sanity: pass

## 부하 테스트에서 확인한 점

초기 Kafka 실험에서는 API가 request status / idempotency / sequence를 PostgreSQL hot path에 두면서 Pgpool 병목이 먼저 드러났습니다. 이후 API intake를 Kafka append 중심으로 정리했습니다.

최신 Kafka performance baseline은 아래 suite로 측정했습니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_kafka_performance_suite.ps1
```

2026-04-28 실행 결과:

- 순차 검증 이벤트 수: `100`
- 순차 검증 결과: `stream_seq 1..100`, body 순서 일치
- profile: `single500`
- 동시 사용자: `100`
- 실행 시간: `30s`
- idempotency header: 비활성화
- 전체 HTTP 요청 수: `31676`
- event status 200: `31672`
- event status 503: `0`
- 오류율: `0.00%`
- 평균 latency: `44.13ms`
- p95 latency: `80.65ms`
- p99 latency: `103.57ms`
- accepted-to-persisted p95: `7.67ms`
- API HPA 최종 replica: `6`
- Worker KEDA 최종 replica: `4`

비교 진단:

- 초기 진단 구현에서 `X-Idempotency-Key`를 켠 부하에서는 PostgreSQL state-store path가 API hot path로 다시 들어왔습니다.
- 이 경우 낮은 부하에서도 `503`이 발생했고, 100 VU에서는 Pgpool 재시작 압력과 높은 실패율이 나타났습니다.
- Kafka-native 완성형 기준: idempotency / request status state path와 Kafka append path 분리

## 2026-06-09 재실행 메모

동일한 `scripts/run_kafka_performance_suite.ps1` 조건에서 k6를 다시 실행했습니다.

| 지표 | 결과 |
| --- | ---: |
| 전체 HTTP 요청 수 | `34284` |
| event status 200 | `34280` |
| event status 503 | `0` |
| 오류율 | `0.00%` |
| 평균 latency | `36.86ms` |
| p95 latency | `66.06ms` |
| p99 latency | `104.99ms` |
| same-stream ordering | `stream_id=30`, `stream_seq 1..100`, ordering `pass` |
| async persistence sample | `stream_id=31`, 50 events persisted |
| accepted-to-persisted p95 | `73.50ms` |
| 부하 직후 Worker consumer lag | `36394` |
| Worker KEDA max replica | `8` |
| 최종 drain | 약 14분 후 consumer lag `0` |

API intake latency는 좋아졌고, 같은 실행에서 same-stream ordering과 async persistence completion도 통과했습니다. 다만 Worker consumer lag가 크게 쌓인 뒤 천천히 drain되었습니다. 따라서 이 결과는 기존 2차 baseline을 단순 대체하기보다 Worker persistence capacity와 drain time을 별도 튜닝 대상으로 보여주는 signal로 해석합니다.

## 2026-06-09 transaction 통합 후 재실행

Worker success path에서 message persistence와 request status update를 하나의 PostgreSQL transaction으로 묶은 뒤 같은 suite를 다시 실행했습니다. 이후 notification attempt 기록은 핵심 persistence transaction에서 분리해 `message-notifications` topic과 별도 `notification-worker`가 처리하도록 조정했습니다.

| 지표 | 결과 |
| --- | ---: |
| 전체 HTTP 요청 수 | `28839` |
| event status 200 | `28835` |
| event status 503 | `0` |
| 오류율 | `0.00%` |
| 평균 latency | `53.47ms` |
| p95 latency | `108.68ms` |
| p99 latency | `134.53ms` |
| same-stream ordering | `stream_id=34`, `stream_seq 1..100`, ordering `pass` |
| async persistence sample | `stream_id=35`, 50 events persisted |
| accepted-to-persisted p95 | `8.08ms` |
| 부하 직후 Worker consumer lag | `29204` |
| Worker KEDA max replica | `8` |
| 최종 drain | 약 10분 후 consumer lag `0` |

이 변경은 persistence lag와 backlog drain에는 긍정적이었지만, k6 API intake request count와 p95 latency는 악화됐습니다. 따라서 안정 기준선은 기존 2차 baseline을 유지하고, transaction 통합 결과는 Worker persistence path 개선 실험으로 분리해서 해석합니다.

변경 해석:

- API: Kafka 모드에서 stream sequence 선점 제외
- Worker: persistence 시점 sequence 배정
- API accepted status store: 기본값에서 synchronous DB hot path 제외
- request idempotency claim: 기본값에서 API hot path 수행 제외, Worker persistence path에서 최종 idempotency 처리

## 현재 설계 방향

Kafka-native 완성형으로 가려면 event log path와 low-latency state path를 분리해야 합니다.

현재 반영한 항목:

- Kafka compacted topic으로 DB commit 이후 message / stream snapshot local materialized cache 구성
- message read는 cache-first로 조회하고, cache miss / stale이면 PostgreSQL로 fallback
- API는 Kafka append 전에 idempotency claim이나 request status 저장을 위해 PostgreSQL을 선점하지 않음

남은 후보:

- idempotency state를 compacted topic 또는 별도 state backend로 분리
- 별도 low-latency state store 도입
- API 응답 계약을 단순 `accepted event id` 중심으로 줄여 state lookup 최소화
- sequence allocation을 partition-local ordering 기반으로 재설계

이 결과는 Kafka를 선택할 때 event log뿐 아니라 state path까지 함께 설계해야 한다는 결론을 보여줍니다.
