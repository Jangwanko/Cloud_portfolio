# 테스트 결과

Kafka Event Stream Systems 포트폴리오의 로컬 Kubernetes/kind 검증 결과입니다.

## 전체 검증 요약

| 시나리오 | 결과 | 비고 |
| --- | --- | --- |
| 전체 quick start | 통과 | `scripts/quick_start_all.ps1` 정상 완료 |
| Kafka broker rollout | 통과 | `apache/kafka:3.7.0` 3-broker KRaft StatefulSet, `3/3` ready |
| Kafka topic bootstrap | 통과 | `message-ingress`, `message-ingress-dlq`, partitions `8`, replication factor `3`, `min.insync.replicas=2` |
| API Kafka intake | 통과 | API가 Kafka-only ingress path로 event를 수락 |
| Worker consumer group | 통과 | `message-worker`가 `message-ingress` partition을 소비하고 PostgreSQL에 영속화 |
| Smoke test | 통과 | event accepted -> Kafka ingress topic -> Worker -> PostgreSQL persisted |
| PostgreSQL 장애 테스트 | 통과 | DB primary 중단 중에도 API가 degraded mode로 ready를 유지하고 cached-stream event를 수락 |
| Kafka DLQ flow | 통과 | poison event가 `message-ingress-dlq`에 도달하고 DLQ API로 조회됨 |
| HPA metrics test | 통과 | API HPA metric 유효 |
| Unit tests | 통과 | `.venv\Scripts\python.exe -m pytest -q`, `12 passed` |
| 같은 stream 순차 보증 | 통과 | 100개 순차 이벤트가 `stream_seq 1..100`으로 영속화 |
| Kafka 성능 suite | 통과 | 100 VU / 30s Kafka intake 기준선, `0.00%` error |

## 1차 실험: Kafka 이벤트 스트림 기준선

목적:

- Kafka ingress topic 중심의 request intake 경로를 검증한다.
- Worker consumer group이 Kafka partition을 소비해 PostgreSQL HA에 영속화하는지 확인한다.
- 같은 stream 이벤트가 `stream_id` key를 통해 같은 ordering boundary에 들어가는지 확인한다.
- DLQ, degraded readiness, autoscaling, 기본 성능 기준선을 함께 확인한다.

측정 조건:

| 항목 | 값 |
| --- | ---: |
| 부하 profile | `single500` |
| 동시 사용자 | `100` |
| 실행 시간 | `30s` |
| idempotency header | 비활성화 |
| 순차 검증 이벤트 수 | `100` |
| 비동기 latency sample size | `50 events` |

1차 결과:

| 지표 | 결과 |
| --- | ---: |
| 순차 검증 결과 | `stream_seq 1..100` |
| 전체 HTTP 요청 수 | `31710` |
| Event status 200 | `31706` |
| Event status 503 | `0` |
| 오류율 | `0.00%` |
| 평균 latency | `44.04ms` |
| p95 latency | `86.95ms` |
| p99 latency | `113.78ms` |
| 비동기 accept 평균 / p95 / 최대 | `55.68ms` / `65.83ms` / `86.55ms` |
| Accepted-to-persisted 평균 / p95 / 최대 | `7.51ms` / `8.04ms` / `10.92ms` |
| API HPA 최종 replica | `8` |
| Worker KEDA 최종 replica | `8` |

1차에서 확인한 한계:

- Pgpool이 `1 replica`라 PostgreSQL HA 앞단의 단일 장애점으로 남아 있었다.
- idempotency header를 켠 부하에서는 PostgreSQL state-store path가 API hot path에 들어와 Pgpool 압박과 `503`이 발생했다.
- Worker가 transient persistence failure를 만나면 실패 이벤트를 Kafka tail로 재발행할 수 있어, 같은 stream의 뒤 이벤트가 앞 이벤트를 추월할 가능성이 있었다.

## 2차 실험: Pgpool HA와 엄격한 stream 순서 보장

목적:

- Pgpool 단일 장애점을 줄인다.
- Pgpool replica 증가가 PostgreSQL connection pressure로 이어지지 않도록 pool 값을 낮춘다.
- 같은 stream 안에서 앞 이벤트가 실패해도 뒤 이벤트가 먼저 영속화되지 않도록 Worker retry 방식을 보강한다.
- 보강 후 같은 순차 보증 테스트와 성능 suite를 다시 실행한다.

보강 내용:

- Pgpool `replicaCount`: `1 -> 2`
- Pgpool PDB: `minAvailable=1`
- PostgreSQL PDB: `minAvailable=2`
- Pgpool `numInitChildren`: `128 -> 64`
- Pgpool `maxPool`: `4 -> 2`
- Pgpool `childMaxConnections`: `200 -> 100`
- Worker retry: Kafka tail 재발행 대신 같은 offset에서 inline retry
- k6 summary: p99 latency 출력 추가
- performance suite: 같은 stream 순차 보증 테스트 포함

측정 조건:

| 항목 | 값 |
| --- | ---: |
| 실행 시각 | `2026-04-28T02:40:29+09:00` |
| 부하 profile | `single500` |
| 동시 사용자 | `100` |
| 실행 시간 | `30s` |
| idempotency header | 비활성화 |
| 순차 검증 이벤트 수 | `100` |
| 비동기 latency sample size | `50 events` |

2차 결과:

| 지표 | 결과 |
| --- | ---: |
| 순차 검증 결과 | `stream_seq 1..100`, body 순서 일치 |
| Pgpool 상태 | `2/2` ready, PDB `minAvailable=1` |
| PostgreSQL 상태 | `3/3` ready, standby count `2` |
| 전체 HTTP 요청 수 | `31676` |
| Event status 200 | `31672` |
| Event status 503 | `0` |
| 오류율 | `0.00%` |
| 평균 latency | `44.13ms` |
| p95 latency | `80.65ms` |
| p99 latency | `103.57ms` |
| 비동기 accept 평균 / p95 / 최대 | `53.34ms` / `63.59ms` / `75.22ms` |
| Accepted-to-persisted 평균 / p95 / 최대 | `7.29ms` / `7.67ms` / `8.14ms` |
| API HPA 최종 replica | `6` |
| Worker KEDA 최종 replica | `4` |

2차 해석:

- Pgpool을 2개로 늘리면서도 pool 폭을 낮춰 DB connection pressure를 제어했다.
- 같은 stream 순서 보장은 Kafka partition key뿐 아니라 Worker failure handling까지 함께 맞아야 한다는 점을 확인했다.
- inline retry는 같은 partition의 뒤 이벤트를 막기 때문에 엄격한 순서 보장에는 유리하다.
- 대신 앞 이벤트가 오래 막히면 같은 stream 경계의 뒤 이벤트도 함께 대기한다.
- 2차 baseline에서도 `503` 없이 100 VU / 30s를 통과했다.

## 측정 방법

Kafka-native 측정 suite는 기본 기능 검증과 분리해서 봅니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_kafka_performance_suite.ps1
```

이 suite는 한 번의 실행에서 네 가지를 측정합니다.

| 측정 항목 | 측정 방법 | 통과 기준 |
| --- | --- | --- |
| 같은 stream 순차 보증 | stream 하나를 만들고 public API로 `ordering-event-0001..0100`을 순차 전송한 뒤, 영속화된 event를 조회해 `stream_seq`로 정렬 | `stream_seq`가 정확히 `1..100`이고 각 body가 같은 index와 일치 |
| 비동기 영속화 latency | public API로 50개 event를 보내 API accept latency를 기록하고, request status가 `persisted`가 될 때까지 polling | 50개 요청이 timeout 전에 모두 persisted |
| Kafka intake 부하 | cluster 내부 k6가 `http://api.messaging-app.svc.cluster.local:8000` 대상으로 `single500` profile, 100 VU, 30초 실행 | k6 threshold 통과: failure rate `<5%`, p95 `<1000ms`, p99 `<2000ms` |
| HPA / KEDA 점검 | 부하 이후 Kubernetes HPA 상태 확인 | metrics 조회 가능, API/Worker scaling 상태 확인 가능 |

측정 범위:

- 부하 기준선은 Kafka append 중심 intake path를 측정합니다.
- 이 기준선에서 k6는 `X-Idempotency-Key`를 보내지 않습니다.
- idempotency-enabled write는 Kafka append 전에 PostgreSQL state-store 작업을 수행하므로 별도 state-path profile로 측정합니다.

## 검증 명령

- `.venv\Scripts\python.exe -m pytest -q`
- `.venv\Scripts\python.exe -m compileall portfolio worker observer alembic`
- `kubectl apply --dry-run=client -f k8s\gitops\base\kafka-ha.yaml -o yaml`
- `kubectl kustomize k8s\gitops\overlays\local-ha`
- `powershell -ExecutionPolicy Bypass -File scripts\quick_start_all.ps1`
- `powershell -ExecutionPolicy Bypass -File scripts\test_db_down.ps1 -SkipReset`
- `powershell -ExecutionPolicy Bypass -File scripts\test_dlq_flow.ps1 -SkipReset`
- `powershell -ExecutionPolicy Bypass -File scripts\test_stream_ordering.ps1 -EventCount 100`
- `powershell -ExecutionPolicy Bypass -File scripts\test_hpa_scaling.ps1 -TimeoutSec 60`
- `powershell -ExecutionPolicy Bypass -File scripts\run_kafka_performance_suite.ps1`
- `git diff --check`

## 운영 메모

- 최신 출력은 `results/kafka-performance/latest.txt`에 저장됩니다.
- `X-Idempotency-Key`를 켠 경우 API가 Kafka append 전에 PostgreSQL state-store 작업을 수행합니다.
- 진단 실행에서 이 DB state path가 병목이 되었고, `503` 응답과 Pgpool 재시작 압력이 함께 나타났습니다.
- 따라서 idempotency-state 설계는 별도 Kafka-native 보강 과제로 다룹니다.
- 장시간 500+ VU 테스트는 별도 capacity profile로 다룹니다.
