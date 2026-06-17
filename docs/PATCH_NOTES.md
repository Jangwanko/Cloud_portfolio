# 패치 노트

Kafka Event Stream Systems 포트폴리오의 주요 구현, 검증, 튜닝 기록입니다.

## 1차 실험: Kafka 이벤트 스트림 기준선

목표:

- API request intake를 Kafka ingress topic 중심으로 구성한다.
- Worker consumer group이 Kafka partition을 소비해 PostgreSQL HA에 비동기로 영속화한다.
- `stream_id`를 Kafka message key로 사용해 같은 stream 이벤트가 같은 순서 보장 경계에 들어가도록 한다.
- Kafka DLQ topic과 DLQ Replayer로 실패 이벤트의 복구 경로를 만든다.
- 기본 기능, DLQ, readiness, autoscaling, 성능 기준선을 한 번에 검증한다.

구현 범위:

- FastAPI event request API
- Kafka ingress topic: `message-ingress`
- Kafka DLQ topic: `message-ingress-dlq`
- Worker consumer group: `message-worker`
- 3-broker KRaft Kafka StatefulSet
- topic partitions `8`, replication factor `3`, `min.insync.replicas=2`
- API CPU HPA
- Worker KEDA Kafka lag scaler
- Prometheus / Grafana observability
- PostgreSQL HA + Pgpool persistence path

검증 결과:

- Kafka broker rollout: 통과
- Kafka topic bootstrap: 통과
- API Kafka intake: 통과
- Worker consume and PostgreSQL persist: 통과
- Smoke test: 통과
- API contract test: 통과
- Kafka DLQ flow: 통과
- PostgreSQL 장애 시 degraded readiness 시나리오: 통과
- Unit tests: 통과
- k6 Kafka intake 기준선: 통과

1차 성능 기준선:

- 부하 프로필: `single500`
- 동시 사용자: `100`
- 실행 시간: `30s`
- idempotency header: 비활성화
- 순차 검증 이벤트 수: `100`
- 순차 검증 결과: `stream_seq 1..100`
- 전체 HTTP 요청 수: `31710`
- event status 200: `31706`
- event status 503: `0`
- 오류율: `0.00%`
- 평균 latency: `44.04ms`
- p95 latency: `86.95ms`
- p99 latency: `113.78ms`
- 비동기 수락 latency 평균 / p95 / 최대: `55.68ms` / `65.83ms` / `86.55ms`
- accepted-to-persisted 평균 / p95 / 최대: `7.51ms` / `8.04ms` / `10.92ms`
- API HPA 최종 replica: `8`
- Worker KEDA 최종 replica: `8`

1차에서 확인한 한계:

- Pgpool이 `1 replica`라 PostgreSQL HA 앞단의 단일 장애점으로 남아 있었다.
- 초기 진단 구현에서 idempotency header를 켠 부하에서는 PostgreSQL state-store path가 API hot path에 들어와 Pgpool 압박과 `503`이 발생했다.
- Worker가 transient persistence failure를 만나면 실패 이벤트를 Kafka tail로 재발행할 수 있어, 같은 stream의 뒤 이벤트가 앞 이벤트를 추월할 가능성이 있었다.

## 2차 실험: Pgpool HA와 엄격한 stream 순서 보장

목표:

- Pgpool 단일 장애점을 줄인다.
- Pgpool replica 증가가 PostgreSQL connection pressure로 이어지지 않도록 pool 값을 낮춘다.
- 같은 stream 안에서는 앞 이벤트가 실패해도 뒤 이벤트가 먼저 영속화되지 않도록 순서 보장을 강화한다.
- 보강 후 같은 순차 보증 테스트와 성능 suite를 다시 실행한다.

구현 변경:

- Pgpool `replicaCount`: `1 -> 2`
- Pgpool PDB 추가: `minAvailable=1`
- PostgreSQL PDB 명시: `minAvailable=2`
- Pgpool `numInitChildren`: `128 -> 64`
- Pgpool `maxPool`: `4 -> 2`
- Pgpool `childMaxConnections`: `200 -> 100`
- Pgpool `reservedConnections`: `2 -> 4`
- Pgpool idle/lifetime timeout 추가
- Worker retry 방식을 Kafka tail 재발행에서 inline retry로 변경
- 같은 Kafka offset에서 retry/backoff를 수행한 뒤 성공 또는 DLQ 처리 후 offset commit
- performance suite에 같은 stream 순차 보증 테스트 포함
- k6 summary에 p99 latency 출력 추가

2차 검증 결과:

- Pgpool deployment: `2/2` ready
- Pgpool PDB: `minAvailable=1`
- PostgreSQL StatefulSet: `3/3` ready
- PostgreSQL PDB: `minAvailable=2`
- readiness: `ready`
- Kafka bootstrap reachable: `true`
- PostgreSQL primary reachable: `true`
- PostgreSQL standby count: `2`
- 같은 stream 순차 보증: 통과
- Unit tests: `58 passed`
- k6 Kafka intake 기준선: 통과

2차 성능 기준선:

- 실행 시각: `2026-04-28T02:40:29+09:00`
- 부하 프로필: `single500`
- 동시 사용자: `100`
- 실행 시간: `30s`
- idempotency header: 비활성화
- 순차 검증 이벤트 수: `100`
- 순차 검증 결과: `stream_seq 1..100`, body 순서 일치
- 전체 HTTP 요청 수: `31676`
- event status 200: `31672`
- event status 503: `0`
- 오류율: `0.00%`
- 평균 latency: `44.13ms`
- p95 latency: `80.65ms`
- p99 latency: `103.57ms`
- 비동기 수락 latency 평균 / p95 / 최대: `53.34ms` / `63.59ms` / `75.22ms`
- accepted-to-persisted 평균 / p95 / 최대: `7.29ms` / `7.67ms` / `8.14ms`
- API HPA 최종 replica: `6`
- Worker KEDA 최종 replica: `4`

2차 해석:

- Pgpool을 2개로 늘리면서도 pool 폭을 낮춰 DB connection pressure를 제어했다.
- 같은 stream 순서 보장은 Kafka partition key만으로 끝나지 않고, Worker failure handling까지 함께 맞아야 한다는 점을 확인했다.
- inline retry는 같은 partition의 뒤 이벤트를 막기 때문에 엄격한 순서 보장에는 유리하다.
- 대신 앞 이벤트가 오래 막히면 같은 stream 경계의 뒤 이벤트도 함께 대기한다. 이 trade-off는 순서 보장을 선택한 결과다.
- 최신 baseline에서는 Pgpool HA 보강 후에도 `503` 없이 100 VU / 30s를 통과했다.

## 2026-06-09 재실행: 정합성 재확인과 backlog drain 관측

목표:

- 현재 클러스터에서 Kafka append-first intake baseline이 크게 흔들리지 않는지 확인한다.
- 같은 실행 안에서 same-stream ordering과 async persistence completion을 다시 확인한다.
- 부하 직후 Worker consumer lag가 얼마나 쌓이고, KEDA max scale 이후 얼마나 걸려 drain되는지 본다.

실행 명령:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_kafka_performance_suite.ps1
```

검증 결과:

- 전체 HTTP 요청 수: `34284`
- event status 200: `34280`
- event status 503: `0`
- 오류율: `0.00%`
- 평균 latency: `36.86ms`
- p95 latency: `66.06ms`
- p99 latency: `104.99ms`
- same-stream ordering: `stream_id=30`, 100 events, `stream_seq 1..100`, ordering `pass`
- async persistence sample: `stream_id=31`, 50 events persisted
- accepted-to-persisted p95: `73.50ms`
- 부하 직후 Worker consumer lag: `36394`
- drain 경로: `36394 -> 33274 -> 23563 -> 11971 -> 0`
- Worker KEDA max replica: `8`
- 최종 drain: 약 14분 후 consumer lag `0`

해석:

- API intake latency와 요청 수는 개선됐지만, Worker consumer lag가 크게 쌓여 drain time이 새 튜닝 후보로 드러났다.
- 이 결과는 기존 2차 baseline을 대체하지 않고, API intake와 Worker persistence capacity를 분리해서 봐야 한다는 운영 신호로 기록한다.
- Worker scaling 효과는 API throughput 증가로 단정하지 않고 consumer lag, accepted-to-persisted latency, backlog drain time으로 판단한다.

## 2026-06-09 튜닝: Worker success path transaction 통합

목표:

- Worker replica가 max `8`까지 늘어도 backlog drain에 시간이 걸린 원인 후보 중 하나인 message 1건당 DB commit 비용을 줄인다.
- message persistence와 request status update를 같은 PostgreSQL transaction boundary로 묶는다.
- notification attempt 기록은 핵심 persistence transaction에서 분리한다.
- DB commit 이후에만 Kafka request status와 DB snapshot topic publish를 수행해 read cache 원본이 committed row 기준이라는 계약을 유지한다.

변경 내용:

- `persist_ingress_job()`을 추가해 Worker success path를 통합했습니다.
- 기존 `persist_message()` 내부 SQL을 cursor 기반 helper로 분리했습니다.
- `request_statuses` upsert는 cursor 기반 `upsert_request_status()`를 사용해 같은 transaction에 포함했습니다.
- 이후 `notification_attempts` insert는 `message-notifications` topic과 별도 `notification-worker` 처리로 분리했습니다.
- Kafka `message-request-status`와 `message-snapshots` publish는 commit 이후에 수행합니다.

검증:

- success path fake DB test에서 commit `1`회를 확인했습니다.
- `.venv\Scripts\python.exe -m pytest -q`: `60 passed`

Post-tuning performance suite:

- 실행 시각: `2026-06-09T02:17:11+09:00`
- same-stream ordering: `stream_id=34`, 100 events, `stream_seq 1..100`, ordering `pass`
- async persistence sample: `stream_id=35`, 50 events persisted
- 전체 HTTP 요청 수: `28839`
- event status 200: `28835`
- event status 503: `0`
- 오류율: `0.00%`
- 평균 latency: `53.47ms`
- p95 latency: `108.68ms`
- p99 latency: `134.53ms`
- accepted-to-persisted p95: `8.08ms`
- 부하 직후 Worker consumer lag: `29204`
- drain 경로: `29204 -> 23597 -> 15111 -> 6893 -> 0`
- Worker KEDA max replica: `8`
- 최종 drain: 약 10분 후 consumer lag `0`

해석:

- Worker persistence lag와 drain time은 개선됐습니다.
- API intake request count와 p95 latency는 악화됐습니다.
- 따라서 transaction 통합은 persistence path에는 효과가 있지만, 전체 k6 intake 기준선 개선으로는 아직 부족합니다. notification path는 별도 topic/worker로 분리했으며, 다음 측정은 API/Kafka publish path 영향과 notification-worker backlog를 분리해서 봐야 합니다.

## 현재 운영 기준선

현재 기준으로 이 프로젝트는 다음 구조를 기본값으로 둡니다.

- API는 Kafka ingress topic에 append하고 `202 Accepted`를 반환한다.
- Kafka는 ingress와 DLQ transport를 담당한다.
- Worker는 Kafka consumer group으로 partition을 소비한다.
- Worker success path는 message persistence와 request status update를 하나의 PostgreSQL transaction으로 처리한다.
- notification attempt 기록은 `message-notifications` topic과 별도 `notification-worker`가 처리한다.
- 같은 stream은 `stream_id` key를 통해 같은 Kafka partition ordering boundary에 들어간다.
- Worker는 persistence 실패 시 같은 offset에서 inline retry를 수행해 같은 stream의 뒤 이벤트가 앞지르지 못하게 한다.
- PostgreSQL HA는 최종 durable source of truth 역할을 맡는다.
- DB commit 이후 snapshot은 `message-snapshots` / `stream-snapshots` compacted topic으로 발행하고, API는 local materialized cache를 cache-first read에 사용한다.
- Pgpool은 2 replica로 구성하고 PDB와 보수적인 pool 값을 사용한다.
- kafka-exporter로 broker count, topic partition, `message-worker` consumer lag를 직접 관측한다.
- 핵심 운영 API는 FastAPI response model과 OpenAPI schema test로 계약을 고정한다.
- AWS IaC 골격은 EKS + RDS PostgreSQL + Amazon MSK + Secrets Manager 기준으로 정렬한다.

## 2026-06-18 튜닝: notification path 분리

목표:

- 알림 기록 실패가 핵심 message persistence transaction을 rollback시키지 않도록 분리한다.
- Worker success path는 message persistence와 request status update만 같은 transaction으로 처리한다.
- 알림은 DB commit 이후 `message-notifications` topic으로 넘기고 별도 `notification-worker`가 처리한다.

변경 내용:

- `KAFKA_NOTIFICATION_TOPIC=message-notifications`와 `KAFKA_NOTIFICATION_CONSUMER_GROUP=notification-worker` 설정을 추가했습니다.
- Kafka topic bootstrap에 `message-notifications` topic을 추가했습니다.
- `publish_notification_job()`과 `build_notification_consumer()`를 추가했습니다.
- `notification-worker` Deployment / Service를 추가했습니다.
- Prometheus scrape job과 `check_portfolio_status.ps1`에 `notification-worker`를 추가했습니다.

검증:

- `.venv\Scripts\python.exe -m pytest -q`: `60 passed`
- `scripts\check_portfolio_status.ps1`: `Portfolio status check passed`
- `notification-worker` readiness: `1/1`
- `up{job="notification-worker"}=1`
- `message-worker consumer_lag=0`
- `notification-worker consumer_lag=0`

Performance suite:

- 실행 시각: `2026-06-18T03:29:47+09:00`
- same-stream ordering: `stream_id=38`, 100 events, `stream_seq 1..100`, ordering `pass`
- async persistence sample: `stream_id=39`, 50 events
- 전체 HTTP 요청 수: `27795`
- event status 200: `27791`
- event status 503: `0`
- 오류율: `0.00%`
- 평균 latency: `57.64ms`
- p95 latency: `119.28ms`
- p99 latency: `150.60ms`
- accepted-to-persisted p95: `22.13ms`
- Worker KEDA max replica: `8`
- message-worker lag: 약 16분 후 `0`
- notification-worker lag: `0`

해석:

- notification path 분리는 성능 개선보다 장애 격리 개선입니다.
- 알림 기록 실패가 핵심 persistence transaction을 망가뜨리지 않는 구조가 됐습니다.
- 반면 이번 성능 suite에서는 API intake와 accepted-to-persisted latency가 개선되지 않았습니다.
- 다음 튜닝 후보는 Worker DB write throughput, Kafka consumer batch 처리, PostgreSQL lock/commit 비용 분리 측정입니다.

## 남은 튜닝 항목

- idempotency-enabled write load에서 Worker deduplication과 Kafka append-first 계약을 재검증
- Pgpool replica별 connection usage와 PostgreSQL `max_connections` 예산 계산
- DLQ topic depth / replay rate 전용 Grafana panel 강화
- 장시간 500+ VU capacity profile 측정
- multi-node Kubernetes 기준 anti-affinity / topology spread 검증
