# Metrics 기준표

Kafka 기반 포트폴리오 지표 해석 기준:

- Prometheus / Grafana 지표
- 장애별 확인 순서
- 장애 대응 흐름: [OBSERVABILITY.md](OBSERVABILITY.md)

## Dashboard 그룹

운영 dashboard 확인 흐름:

- API request rate / latency / 5xx ratio
- API hot path stage latency
- Worker throughput / failure ratio / last success age
- Worker accepted-to-commit-observed async lag
- Kafka topic wait time
- DB pool pressure / DB failure reason
- DLQ event / replay result
- PostgreSQL primary / standby / replication state
- Worker / API replica count and HPA desired replicas
- Pod restart / unavailable replica signal

Kafka broker/topic/consumer group 지표:

- 제공: kafka-exporter
- 직접 확인: `kafka_consumergroup_lag`, `kafka_brokers`, `kafka_topic_partition_current_offset`
- 보조 신호: `messaging_queue_wait_seconds`, Worker throughput, KEDA desired replica

## Health

### `messaging_health_status`

component별 health 신호:

- `component="kafka"`: Kafka bootstrap reachable
- `component="db"`: PostgreSQL writable primary 확인 기준
- `component="worker"`: Worker 처리 loop 상태

```promql
messaging_health_status{job="api",component="kafka"}
messaging_health_status{job="api",component="db"}
messaging_health_status{job="worker",component="worker"}
```

## API metrics

### `messaging_api_requests_total`

HTTP status별 API request counter.

```promql
sum(rate(messaging_api_requests_total[1m])) by (status)
```

5xx ratio:

```promql
sum(rate(messaging_api_requests_total{status=~"5.."}[5m]))
/
clamp_min(sum(rate(messaging_api_requests_total[5m])), 0.001)
```

### `messaging_api_request_latency_seconds`

API request 수신부터 response 반환까지의 시간.

- Worker PostgreSQL persisted 완료 시간 제외

```promql
histogram_quantile(0.95, sum(rate(messaging_api_request_latency_seconds_bucket[1m])) by (le))
```

### `messaging_api_stage_latency_seconds`

API hot path 내부 구간별 latency.

주요 stage:

- `membership_check`: 조회 API 등 PostgreSQL state 확인이 필요한 경로의 stream membership 확인
- `postgres_idempotency`: legacy / 진단용 idempotency state path. 현재 event intake 기본 경로에서는 Kafka append 전에 사용하지 않음
- `kafka_publish`: Kafka ingress topic publish

```promql
histogram_quantile(0.95, sum(rate(messaging_api_stage_latency_seconds_bucket[1m])) by (le, stage))
```

## Worker metrics

### `messaging_worker_processed_total`

Worker event 처리 누적 건수.

```promql
sum(rate(messaging_worker_processed_total{job="worker"}[1m])) by (result)
```

Worker failure ratio:

```promql
sum(rate(messaging_worker_processed_total{job="worker",result="failure"}[5m]))
/
clamp_min(sum(rate(messaging_worker_processed_total{job="worker"}[5m])), 0.001)
```

Core ingress 결과는 `success`, `rejected`, `dlq`, `failure`로 분리합니다. `notification-worker`는 `job="notification-worker"`로 별도 조회합니다.

### `messaging_worker_last_success_timestamp`

Worker 마지막 event 성공 처리 Unix timestamp.

```promql
time() - max(messaging_worker_last_success_timestamp{job="worker"})
```

해석:

- 값 지속 증가: Worker pod 생존 중 실제 consume / persist 성공 중단 가능성

### `messaging_worker_stage_latency_seconds`

Worker 내부 구간별 latency.

주요 stage:

- `db_persist`: PostgreSQL transaction으로 event 영속화
- `request_status_update`: request status 갱신
- request status DB row: message persistence와 같은 PostgreSQL transaction
- request status compacted topic: DB commit 이후 best-effort publish
- DB read fallback: DB commit 이후 snapshot topic `message-snapshots` / `stream-snapshots` 원본 사용
- message read 응답 `source`, `degraded`, `snapshot_age_seconds`: DB membership/watermark-gated snapshot hit / stale fallback 판단용 API-level signal
- `notification_enqueue`: DB commit 이후 `message-notifications` topic으로 후속 notification 작업 생성
- `notification_db_insert`: 별도 `notification-worker`가 notification attempt record 저장. 외부 채널 실제 발송 결과 제외
- `notification_publish`: DB commit 이후 notification job publish

```promql
histogram_quantile(0.95, sum(rate(messaging_worker_stage_latency_seconds_bucket[1m])) by (le, stage))
```

### `messaging_notification_publish_failures_total`

PostgreSQL commit 이후 `message-notifications` publish 실패 counter입니다.

```promql
sum(increase(messaging_notification_publish_failures_total{job="worker"}[15m]))
```

증가 시 core DB row는 이미 commit됐을 수 있습니다. request/message reconciliation과 수동 복구가 필요하며 현재 transactional outbox는 없습니다.

## 비동기 lag

### `messaging_event_persist_lag_seconds`

Payload `queued_at`부터 Worker의 PostgreSQL `commit()`이 반환된 직후 기록한 `persisted_at`까지의 lag입니다. DB commit 이후 request status/snapshot/notification publish 시간은 제외합니다. API와 Worker clock 차이는 별도 고려가 필요합니다.

```promql
histogram_quantile(0.95, sum(rate(messaging_event_persist_lag_seconds_bucket{job="worker"}[1m])) by (le))
```

PowerShell performance suite의 2026-06 `accepted-to-persisted`는 PostgreSQL row `created_at` / row-visible proxy입니다. 현재 script의 `accepted_to_status_observed_ms`는 API `queued_at`부터 client가 `persisted` status를 관측할 때까지이며 polling interval과 network delay를 포함합니다. 둘 다 이 Worker histogram과 별도 측정입니다.

### `messaging_queue_wait_seconds`

event가 Worker 처리 전까지 대기한 시간 해석 지표.

- Kafka-centered 관점: consumer-side wait / backlog signal

```promql
histogram_quantile(0.95, sum(rate(messaging_queue_wait_seconds_bucket[1m])) by (le))
```

## Read cache operating signals

DB snapshot materialized cache 현재 검증 기준:

- API 응답 메타데이터
- 운영 신호 후보
- Prometheus counter / histogram 승격 후보

| 신호 | 현재 확인 방법 | 운영 해석 |
| --- | --- | --- |
| Read cache hit ratio | `GET /streams/{stream_id}/events` 응답의 `source=cache` 비율 | 낮아지면 pod hydration 지연, cache rebuild, DB fallback 증가 가능성 |
| Snapshot age | 응답의 `snapshot_age_seconds` | warning `> 30s`, critical `> 120s` 후보 |
| Cache rebuild time | API pod restart 후 첫 fresh `source=cache` 응답까지 시간 | API pod 교체 뒤 read cache 복구 속도 |
| Stale response count | `source=cache`, `degraded=true` 응답 수 | DB failure 중 stale cache fallback이 사용자 read를 받치고 있는 정도 |
| Degraded read count | `degraded=true` 응답 수 | read path가 정상 DB/cache 경로를 벗어난 빈도 |
| Per-pod snapshot replay progress | 미구현 custom metric: current position / captured initial end offset / remaining records / hydration duration | pod별 cold-start replay와 cache gate 개방 지연 측정 |

Materialized cache consumer는 `group_id` 없이 각 API pod가 모든 snapshot partition을 직접 assign하고 beginning부터 replay합니다. 따라서 kafka-exporter의 `kafka_consumergroup_lag` series가 존재하지 않으며, Worker/notification consumer group lag와 섞어 해석하지 않습니다. 현재는 readiness payload의 `ready`, `hydrated`, `last_error`, cache item count와 API read 응답을 확인합니다.

`message-request-status`와 `message-snapshots`는 대부분 request/message별 unique key를 사용합니다. Compaction만으로 전체 key 수와 pod cold-start replay가 bounded 되지는 않습니다. Retention 또는 bootstrap/changelog 구조가 적용되기 전에는 cache rebuild time을 장기 SLO로 확정하지 않습니다.

`source=cache` 해석:

- `degraded=false`: fresh cache hit
- `degraded=true`: DB failure 또는 stale fallback을 cache가 받친 상태
- `source=cache`만으로 성공 판단 제외

## DLQ metrics

### `messaging_dlq_events_total`

Worker가 event를 Kafka DLQ topic으로 보낸 누적 counter.

```promql
sum by (reason) (increase(messaging_dlq_events_total[15m]))
```

같은 `reason`이 반복 증가하면 replay보다 원인 수정이 먼저입니다.

### `messaging_dlq_replay_total`

DLQ Replayer의 replay 결과 counter입니다.

```promql
sum by (result) (increase(messaging_dlq_replay_total[15m]))
```

주요 `result`:

- `replayed`: DLQ event를 ingress topic으로 다시 append함
- `skipped_max_replay`: `DLQ_REPLAY_MAX_COUNT`에 도달해 자동 replay에서 제외함

### DLQ summary `oldest_sample_age_seconds`

이 값은 Prometheus metric이 아니라 운영 API가 조회한 append-only log sample의 시간 범위입니다.

```powershell
Invoke-RestMethod -Headers @{ Authorization = "Bearer <token>" } http://localhost/v1/dlq/ingress/summary?limit=200&sample_limit=5
```

Endpoint: `GET /v1/dlq/ingress/summary`

해석 기준:

- 오래된 sample 포함 여부 확인
- unresolved depth / oldest unresolved age에서 제외
- 현재 incident 판단: DLQ counter increase, replay result, Worker lag, accepted/persisted reconciliation 조합

## PostgreSQL metrics

### `messaging_db_pool_in_use`

process별 DB connection checkout 수입니다.

```promql
messaging_db_pool_in_use{job=~"api|worker|notification-worker|dlq-replayer"}
```

API만 높으면 request/read path, Worker면 persistence path, notification-worker면 notification attempt insert, DLQ Replayer면 replay gate를 먼저 확인합니다.

### `messaging_db_failure_total`

DB failure reason별 counter입니다.

```promql
sum by (reason) (increase(messaging_db_failure_total[15m]))
```

### `messaging_postgres_is_primary`

Pgpool 경유 writable primary가 reachable한지 나타냅니다.

- `1`: primary reachable
- `0`: write path unavailable

### `messaging_postgres_standby_count`

Pgpool이 up 상태로 보고하는 standby 수입니다.

- `2+`: 로컬 HA ready 기준
- `1 이하`: degraded 기준

### `messaging_postgres_sync_standby_count`

sync 또는 quorum 기준을 만족하는 standby 수입니다. 로컬 kind 데모에서는 async streaming standby도 정상 운영 상태로 취급합니다.

### `messaging_postgres_replication_state_count`

standby replication state 분포입니다. 정상 기준에서는 standby가 `streaming`으로 관측되는 것이 기대값입니다.

### `messaging_postgres_replication_delay_bytes_max`

가장 큰 replication delay입니다. 임계치를 넘으면 replication lag 상승으로 보고 `degraded`로 해석합니다.

## KEDA / Kubernetes metrics

Worker autoscaling은 KEDA 기반 Kafka lag scaling을 사용합니다.

```promql
kube_deployment_spec_replicas{namespace="messaging-app",deployment="worker"}
kube_deployment_status_replicas_available{namespace="messaging-app",deployment="worker"}
kube_horizontalpodautoscaler_status_desired_replicas{namespace="messaging-app",horizontalpodautoscaler="worker-keda-hpa"}
```

API HPA:

```promql
kube_deployment_spec_replicas{namespace="messaging-app",deployment="api"}
kube_deployment_status_replicas_available{namespace="messaging-app",deployment="api"}
kube_horizontalpodautoscaler_status_desired_replicas{namespace="messaging-app",horizontalpodautoscaler="api-hpa"}
```

리소스 / rollout 신호:

```promql
sum by (pod, container) (increase(kube_pod_container_status_restarts_total{namespace="messaging-app"}[15m]))
kube_deployment_status_replicas_unavailable{namespace="messaging-app"}
```

## 상태 모델

### `ready`

- schema startup 완료
- Kafka bootstrap reachable
- PostgreSQL writable primary reachable
- standby / sync standby minimum 충족
- replication delay threshold 이내
- non-local unsafe auth secret 미사용

### `degraded`

- schema/Kafka/secret hard failure 없음
- PostgreSQL primary unreachable, standby 부족, sync standby 부족, 또는 replication delay 상승

### `not_ready`

- schema startup 미완료
- Kafka bootstrap unreachable
- non-local unsafe auth secret

더 자세한 readiness 정책은 [RELIABILITY_POLICY.md](RELIABILITY_POLICY.md)를 봅니다.

## Kafka exporter metrics

### `kafka_brokers`

kafka-exporter가 broker metadata에서 확인한 broker 수입니다.

```promql
kafka_brokers
```

로컬 HA 기준은 `3`입니다.

### `kafka_consumergroup_lag`

consumer group lag를 topic / partition 단위로 보여줍니다. 현재 적용 대상은 `message-worker`, `notification-worker`, DLQ replayer처럼 group id가 있는 consumer입니다. API materialized cache replay에는 적용되지 않습니다.

```promql
sum by (topic) (clamp_min(kafka_consumergroup_lag{consumergroup="message-worker"}, 0))
```

`message-worker` lag가 높으면 Worker 처리량, DB persist latency, pod scaling을 함께 확인합니다.

Notification path:

```promql
sum by (topic) (clamp_min(kafka_consumergroup_lag{consumergroup="notification-worker"}, 0))
```

kafka-exporter는 아직 committed offset이 없는 빈 partition을 `-1`로 노출할 수 있습니다. 집계할 때 각 partition series에 `clamp_min(..., 0)`을 먼저 적용해 빈 partition이 다른 partition의 실제 양수 backlog를 상쇄하지 않도록 합니다.

### `kafka_topic_partition_current_offset`

topic partition offset 지표입니다. topic별 partition 수와 write 흐름을 확인하는 데 사용합니다.

```promql
count by (topic) (kafka_topic_partition_current_offset{topic=~"message-.*"})
```
