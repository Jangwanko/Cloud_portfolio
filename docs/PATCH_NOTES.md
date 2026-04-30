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

## 현재 운영 기준선

현재 기준으로 이 프로젝트는 다음 구조를 기본값으로 둡니다.

- API는 Kafka ingress topic에 append하고 `202 Accepted`를 반환한다.
- Kafka는 ingress와 DLQ transport를 담당한다.
- Worker는 Kafka consumer group으로 partition을 소비한다.
- 같은 stream은 `stream_id` key를 통해 같은 Kafka partition ordering boundary에 들어간다.
- Worker는 persistence 실패 시 같은 offset에서 inline retry를 수행해 같은 stream의 뒤 이벤트가 앞지르지 못하게 한다.
- PostgreSQL HA는 최종 durable source of truth 역할을 맡는다.
- DB commit 이후 snapshot은 `message-snapshots` / `stream-snapshots` compacted topic으로 발행하고, API는 local materialized cache를 cache-first read에 사용한다.
- Pgpool은 2 replica로 구성하고 PDB와 보수적인 pool 값을 사용한다.
- kafka-exporter로 broker count, topic partition, `message-worker` consumer lag를 직접 관측한다.
- 핵심 운영 API는 FastAPI response model과 OpenAPI schema test로 계약을 고정한다.
- AWS IaC 골격은 EKS + RDS PostgreSQL + Amazon MSK + Secrets Manager 기준으로 정렬한다.

## 남은 튜닝 항목

- idempotency-enabled write load에서 Worker deduplication과 Kafka append-first 계약을 재검증
- Pgpool replica별 connection usage와 PostgreSQL `max_connections` 예산 계산
- DLQ topic depth / replay rate 전용 Grafana panel 강화
- 장시간 500+ VU capacity profile 측정
- multi-node Kubernetes 기준 anti-affinity / topology spread 검증
