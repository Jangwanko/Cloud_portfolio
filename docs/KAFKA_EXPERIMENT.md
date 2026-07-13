# Kafka Design and Experiment Record

이 문서는 Kafka 전환의 설계 이유와 실험 해석을 기록합니다. 현재 구조는 Kafka-centered이며 PostgreSQL final state와 read model을 유지합니다.

현재 public event model은 `/v2/streams/{stream_id}/events`와 versioned JSON envelope입니다. 주문 lifecycle은 reference adapter이며 아래 성능 결과는 당시 legacy/order contract로 수집된 역사적 증거를 포함합니다.

## Why Kafka

- append-first intake로 API와 PostgreSQL write path 분리
- partition key로 same-stream ordering boundary 정의
- consumer group으로 Worker partition 분산
- consumer lag를 backlog와 KEDA scaling signal로 사용
- DLQ log와 replay path로 실패 event 보존
- compacted topic으로 DB-committed snapshot materialization

Kafka 도입의 평가 기준은 API request latency, Worker persistence capacity, ordering, recovery를 함께 보는 것입니다.

## Current Flow

```text
Upstream / Demo Client
  -> FastAPI
  -> message-ingress
  -> message-worker
  -> Pgpool
  -> PostgreSQL HA
```

Post-commit flow:

```text
PostgreSQL commit
  -> message-request-status
  -> message-snapshots / stream-snapshots
  -> message-notifications -> notification-worker
```

Failure flow:

```text
Worker inline retry
  -> retry exhausted
  -> message-ingress-dlq
  -> DLQ replayer
  -> message-ingress
```

Scaling and observation are separate consumers of Kafka state:

```text
Kafka broker consumer lag -> KEDA Kafka scaler -> Worker replicas
Kafka exporter / app metrics -> Prometheus -> Grafana / alerts
```

KEDA가 Prometheus를 경유해 scaling하는 구조가 아닙니다.

## Runtime and Topics

Full local profile:

- Kafka: 3-broker KRaft StatefulSet
- partitions: `8`
- replication factor: `3`
- `min.insync.replicas=2`
- Worker KEDA range: `2..8`
- KEDA lag threshold: `100` for the local demo cluster

| Topic | Purpose | Cleanup |
| --- | --- | --- |
| `message-ingress` | accepted event log | delete |
| `message-ingress-dlq` | terminal failure log / replay input | delete |
| `message-request-status` | latest request lifecycle | compact |
| `message-snapshots` | committed message snapshot | compact |
| `stream-snapshots` | committed stream snapshot | compact |
| `message-notifications` | notification work | delete |

Consumer groups:

- `message-worker`: persistence
- `notification-worker`: notification attempt

Current GitOps source:

- runtime/topic bootstrap: `k8s/gitops/base/kafka-ha.yaml`
- Worker/KEDA manifests: `k8s/gitops/base/manifests-ha.yaml`
- local overlay: `k8s/gitops/overlays/local-ha`
- `k8s/app` copy: manual local bootstrap path

## Ordering and Offset Boundary

- message key: `stream_id`
- generic endpoint: `/v2/streams/{stream_id}/events`
- order reference adapter: `order_id` → `stream_id`
- transient failure: same record inline retry
- terminal validation rejection / DLQ result: explicit outcome
- processed record: partition offset `message.offset + 1` commit
- unexpected exception: failed record seek-back, later records from the same polled partition skipped for that batch
- global ordering across partitions: excluded

이 경계는 source-level 보강까지 반영됐습니다. poll batch 처리 중 process crash와 rebalance를 포함한 end-to-end fault injection은 추가 검증 과제입니다.

## State-path Decision

초기 Kafka 실험에서는 request status, idempotency claim, sequence allocation이 API의 PostgreSQL hot path에 남아 Pgpool 압력을 만들었습니다.

현재 경계:

- API: Kafka append 전에 PostgreSQL idempotency claim / request-status write 제외
- `X-Idempotency-Key`: Kafka payload 포함
- Worker: PostgreSQL state에서 최종 deduplication
- Worker: persistence 시점 stream sequence 배정
- read fallback: ingress log 미사용
- cache source: DB commit 이후 snapshot topic

`schema_version`, `event_type`, JSON `payload`, JSON `metadata`는 Kafka envelope에서 Worker persistence, status, snapshot으로 전달됩니다. Legacy `body`, `category`, `payment_id`는 기존 client와 과거 row를 위한 compatibility alias이며 generic Worker가 domain taxonomy를 강제하지 않습니다.

물리 식별자인 `message-*` topic과 `message-worker` consumer group은 기존 offset, compacted state, rollout compatibility를 위해 유지합니다. 범용 정체성 변경만을 이유로 topic을 교체하지 않습니다.

Generic v2는 순서가 있는 rollout입니다.

- GitOps: gate-false `messaging-env` Secret wave `-3` → 일반 Sync Alembic migration Job wave `-2` → dual-read/dual-write Worker wave `-1` → overlay API-true wave `0`
- 수동 local: app manifest gate `false` → API startup migration / Worker rollout → quick start가 API gate `true`로 전환

새 Worker는 old/new envelope를 모두 처리합니다. 구 Worker는 v2 job의 legacy body preview만 저장하고 구조화 JSON을 보존하지 못하므로 대칭 rolling compatibility는 제공하지 않습니다.

## Reliability Interpretation

이 Kafka 설계에서 `reliable`이 가리키는 범위:

- per-stream partition ordering과 failed record inline retry
- processed/terminal record 단위 explicit offset commit
- PostgreSQL idempotent persistence
- retry exhaustion 뒤 DLQ 격리와 replay guard
- consumer lag, drain time, status/snapshot 관측

exactly-once, partition 간 global ordering, 모든 crash boundary의 무손실, production SLA는 검증된 보장이 아닙니다. poll/rebalance fault injection과 DB commit 이후 best-effort publish gap은 후속 검증·개선 범위입니다.

## Performance Evidence

Generic v2 workload는 아직 같은 조건으로 실행하지 않았습니다. 아래 결과는 legacy/order request shape와 당시 HTTP `200` 계약으로 수집된 역사적 evidence이며, v2 serialization/validation 비용이나 v2 route 성능을 측정한 값이 아닙니다.

### Stable intake baseline — 2026-04-28

| Metric | Result |
| --- | ---: |
| Workload | 100 VU / 30s |
| Total requests | `31,676` |
| Event success | `31,672`, historical HTTP `200` |
| Error rate | `0.00%` |
| Avg / p95 / p99 | `44.13ms` / `80.65ms` / `103.57ms` |
| Same-stream ordering | 100 events, pass |
| Row-visible proxy p95 | `7.67ms` |
| Worker KEDA end snapshot | `4` |

`row-visible proxy`는 API accepted 시각과 PostgreSQL row의 `created_at`/조회 가능 시점을 비교한 값입니다. 실제 DB commit timestamp 측정값이 아닙니다.

HTTP `200`은 route에 `202 Accepted`를 명시하기 전의 역사적 원본입니다. 현재 build의 `202`는 새 suite로 재확인해야 합니다.

### Capacity rerun — 2026-06-09

| Metric | Result |
| --- | ---: |
| Requests | `34,284` |
| Avg / p95 / p99 | `36.86ms` / `66.06ms` / `104.99ms` |
| Ordering | stream `30`, `1..100`, pass |
| Row-visible proxy p95 | `73.50ms` |
| Peak message-worker lag | `36,394` |
| KEDA max | `8` |
| Drain | 약 14분 뒤 `0` |

API intake burst와 Worker/RDB persistence capacity의 차이를 드러낸 결과입니다. stable baseline 대체값과 KEDA throughput 개선 증거에서 제외합니다.

### Transaction tuning — 2026-06-09

message persistence와 request status update를 하나의 DB transaction으로 묶은 뒤 실행했습니다.

| Metric | Result |
| --- | ---: |
| Requests | `28,839` |
| Avg / p95 / p99 | `53.47ms` / `108.68ms` / `134.53ms` |
| Ordering | stream `34`, `1..100`, pass |
| Row-visible proxy p95 | `8.08ms` |
| Peak message-worker lag | `29,204` |
| Drain | 약 10분 뒤 `0` |

persistence proxy와 drain은 개선됐고 API intake는 악화됐습니다. 동일 조건 causal A/B가 아니므로 persistence-path 개선 신호로만 기록합니다.

### Notification split — 2026-06-18

| Metric | Result |
| --- | ---: |
| Requests | `27,795` |
| Avg / p95 / p99 | `57.64ms` / `119.28ms` / `150.60ms` |
| Ordering | stream `38`, `1..100`, pass |
| Row-visible proxy p95 | `22.13ms` |
| message-worker drain | 약 16분 뒤 `0` |
| notification-worker lag | `0` |

core persistence와 notification attempt의 장애 범위 분리가 목적이었습니다. 성능 수치가 악화돼 stable baseline으로 채택하지 않았습니다. DB commit 뒤 notification publish는 best-effort이며 transactional outbox는 없습니다.

## KEDA Interpretation

확인 지표:

- peak consumer lag
- lag 감소 곡선
- row-visible 또는 향후 commit-aware persistence latency
- backlog drain time
- PostgreSQL throughput / commit / lock wait

확인되지 않은 주장:

- KEDA가 API request count를 증가시켰다는 직접 인과
- fixed Worker replica보다 KEDA가 우수하다는 동일 조건 수치

다음 실험은 fixed replica와 KEDA를 같은 workload, image, DB pool, partition, 초기 backlog 조건에서 반복해야 합니다.

## DLQ Interpretation

DLQ topic은 append-only failure log입니다.

- list / summary: 최근 조회 sample
- sample age: unresolved event age 제외
- replay success: 원본 DLQ record 삭제 제외
- current unresolved state: 별도 모델 필요

## Reproduction

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_kafka_performance_suite.ps1
```

원본과 상세 조건:

- [TEST_RESULTS.md](TEST_RESULTS.md)
- [results evidence guide](../results/README.md)
- [IMPROVEMENT_ROADMAP.md](IMPROVEMENT_ROADMAP.md)
