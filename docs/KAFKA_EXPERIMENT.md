# Kafka Design and Experiment Record

이 문서는 Kafka 전환의 설계 이유와 실험 해석을 기록합니다. 현재 구조는 Kafka-centered이며 PostgreSQL final state와 read model을 유지합니다.

현재 public event model은 `/v2/streams/{stream_id}/events`와 versioned JSON envelope입니다. 주문 lifecycle은 reference adapter이며 아래 성능 결과는 당시 legacy/order contract로 수집된 역사적 증거를 포함합니다.

## Why Kafka

- append-first intake로 API와 PostgreSQL write path 분리
- partition key로 same-stream ordering boundary 정의
- consumer group으로 Worker partition 분산
- consumer lag를 backlog와 KEDA scaling signal로 사용
- DLQ log와 replay path로 실패 event 보존
- notification job을 core persistence와 분리

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
- Core Worker KEDA range: `2..4`
- Notification Worker KEDA range: `1..2`
- KEDA lag threshold: `100` for the local demo cluster

| Topic | Purpose | Cleanup |
| --- | --- | --- |
| `message-ingress` | accepted event log | delete |
| `message-ingress-dlq` | terminal failure log / replay input | delete |
| `message-notifications` | notification work | delete |

기존 cluster에는 제거된 compacted topic 3개가 남을 수 있습니다. 현재 source는 해당 topic을 만들거나 읽고 쓰지 않습니다. 실제 삭제는 보존 기간과 rollback 필요성을 확인한 뒤 별도 운영 작업으로 수행합니다.

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
- status·event read: PostgreSQL source of truth
- PostgreSQL read 장애: `503`; ingress log를 read fallback으로 사용하지 않음

`schema_version`, `event_type`, JSON `payload`, JSON `metadata`는 Kafka envelope에서 Worker persistence와 request status로 전달됩니다. Legacy `body`, `category`, `payment_id`는 기존 client와 과거 row를 위한 compatibility alias이며 generic Worker가 domain taxonomy를 강제하지 않습니다.

물리 식별자인 `message-*` topic과 `message-worker` consumer group은 기존 offset과 rollout compatibility를 위해 유지합니다. 범용 정체성 변경만을 이유로 topic을 교체하지 않습니다.

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
- consumer lag, drain time, request status 관측

exactly-once, partition 간 global ordering, 모든 crash boundary의 무손실, production SLA는 검증된 보장이 아닙니다. poll/rebalance fault injection과 DB commit 이후 notification publish gap은 후속 검증·개선 범위입니다.

## Performance Evidence

### Current simplified v2 candidate — 2026-08-05

- hot single-stream 3회 평균: event `33,201`, error `0.00%`, avg `39.61ms`, p95 `76.57ms`, p99 `111.49ms`, main drain `364.62s`
- 제거 전 v2 recovery 후보 대비: event `13.83%` 증가, p95 `24.39%` 감소, drain `28.31%` 감소
- source: API pod별 cache·snapshot topic과 Worker post-commit snapshot 발행 제거, API·core Worker·notification Worker 동일 local image
- 판정: 단순화 전후 회복 신호. dirty worktree·local image 조건으로 stable baseline 제외

64-stream record-commit candidate:

| Arm | 반복 | Event `202` | p95 | Peak message / notification lag | All-pipeline drain |
| --- | ---: | ---: | ---: | ---: | ---: |
| fixed core `2` | 1회 | `30,566` | `93.55ms` | `28,386` / `73` | `295.99s` |
| KEDA core `2→4`, notification `1→2` | 3회 | `31,644` / `31,853` / `28,605` | `88.06` / `87.64` / `107.41ms` | latest `25,905` / `1,141` | `295.90` / `305.97` / `321.29s` |

KEDA 실행 간 drain 편차와 node restart 이후 intake 저하를 확인했습니다. 성능 우위는 확정하지 않습니다. core `2→8`은 downstream backlog와 single-node DB 경합을 키워 current 상한에서 제외했습니다. notification Worker에 별도 lag scaler를 두고 core `4`, notification `2`를 현재 상한으로 사용합니다. poll-batch offset commit 실험도 paired KEDA drain이 fixed보다 `9.24%` 길어 폐기했고 record 단위 explicit commit을 유지했습니다.

### Generic v2 recovery candidate — 2026-07-21

- 조건: clean DB/topic, 100 VU / 30초, hot single stream, API min `6`
- 3회 평균: event `29,168`, error `0.00%`, avg `51.81ms`, p95 `101.27ms`, p99 `140.59ms`
- main drain 평균: `508.58s`
- 판정: 첫 v2 후보보다 세 실행 모두 개선, historical stable baseline 미달
- 채택 상태: dirty local image와 API floor 변경 포함, stable baseline 제외

### Multi-stream Worker A/B candidate — 2026-07-21

| Arm | Event | p95 | Peak message / notification lag | All-pipeline drain |
| --- | ---: | ---: | ---: | ---: |
| Fixed `2` | `22,125` | `169.24ms` | `21,170` / `45` | `301.42s` |
| KEDA `2→8` | `20,499` | `212.60ms` | `18,950` / `11,536` | `261.17s` |

KEDA arm의 drain은 `13.35%` 짧았고 intake event와 p95는 악화됐습니다. 각 arm 1회와 dirty image 조건이므로 안정 결론에서 제외합니다.

### First generic v2 candidate — 2026-07-21

- event `25,378`, HTTP `202`, error `0.00%`
- avg `67.83ms`, p95 `123.96ms`, p99 `153.10ms`
- Worker peak lag `24,504`, main drain `751.76s`
- Fresh DB 단일 실행, stable baseline 제외

### Historical legacy sequence

| Date / run | Requests | p95 | Persistence evidence | Interpretation |
| --- | ---: | ---: | --- | --- |
| 2026-06-18 notification split | `27,795` | `119.28ms` | row-visible proxy `22.13ms`, drain 약 16분 | 장애 범위 분리, stable baseline 제외 |
| 2026-06-09 transaction tuning | `28,839` | `108.68ms` | row-visible proxy `8.08ms`, drain 약 10분 | persistence-path 신호, causal A/B 제외 |
| 2026-06-09 capacity rerun | `34,284` | `66.06ms` | row-visible proxy `73.50ms`, drain 약 14분 | intake와 persistence capacity 차이 |
| 2026-04-28 stable intake | `31,676` | `80.65ms` | row-visible proxy `7.67ms` | historical stable Kafka intake baseline |

Historical sequence는 legacy/order request와 HTTP `200` 계약을 사용했습니다. `row-visible proxy`는 API accepted 시각과 PostgreSQL row의 `created_at` 또는 조회 가능 시점을 비교하며 DB commit timestamp가 아닙니다. Generic v2 결과와 직접 비교하지 않습니다. 전체 조건과 원본은 [TEST_RESULTS.md](TEST_RESULTS.md)와 [results evidence guide](../results/README.md)에 있습니다.

## KEDA Interpretation

확인 지표:

- peak consumer lag
- lag 감소 곡선
- row-visible 또는 향후 commit-aware persistence latency
- backlog drain time
- PostgreSQL throughput / commit / lock wait

확인되지 않은 주장:

- KEDA가 API request count를 증가시켰다는 직접 인과
- fixed Worker보다 KEDA가 전 지표에서 우수하다는 안정 결론

2026-08-05 current source에서 core와 notification scaling을 분리하고 세 workload image 일치를 preflight로 강제했습니다. core `2→4`, notification `1→2`의 3회 drain은 `295.90~321.29s`였습니다. fixed core `2` 1회는 `295.99s`였습니다. 현재 표본은 KEDA 성능 향상을 입증하지 않습니다. lag가 각 consumer에서 어디로 이동하고 최종 `0`까지 drain되는지, replica 증가가 DB 처리량에 어떤 영향을 주는지를 운영 판단에 사용합니다.

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
