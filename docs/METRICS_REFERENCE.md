# Metrics 기준표

이 문서는 Kafka 기반 포트폴리오의 Prometheus / Grafana 지표를 빠르게 찾기 위한 참고 문서입니다. 장애별 확인 순서와 해석은 [OBSERVABILITY.md](OBSERVABILITY.md)를 먼저 봅니다.

## Dashboard 그룹

운영 dashboard는 아래 흐름을 기준으로 봅니다.

- API request rate / latency / 5xx ratio
- API hot path stage latency
- Worker throughput / failure ratio / last success age
- Accepted-to-persisted async lag
- Kafka topic wait time
- DB pool pressure / DB failure reason
- DLQ event / replay result
- PostgreSQL primary / standby / replication state
- Worker / API replica count and HPA desired replicas
- Pod restart / unavailable replica signal

Kafka broker에서 직접 산출한 partition별 consumer lag는 아직 별도 exporter를 붙이지 않았습니다. 현재 dashboard에서는 `messaging_queue_wait_seconds`, Worker throughput, KEDA desired replica를 함께 보며 application-side backlog를 해석합니다.

## Health

### `messaging_health_status`

component별 health 신호입니다.

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

HTTP status별 API request counter입니다.

```promql
sum(rate(messaging_api_requests_total[1m])) by (status)
```

5xx ratio:

```promql
sum(rate(messaging_api_requests_total{status=~"5.."}[5m]))
/
clamp_min(sum(rate(messaging_api_requests_total[5m])), 1)
```

### `messaging_api_request_latency_seconds`

API가 request를 받고 response를 반환하기까지의 시간입니다. Worker가 PostgreSQL에 persisted 완료할 때까지의 시간은 포함하지 않습니다.

```promql
histogram_quantile(0.95, sum(rate(messaging_api_request_latency_seconds_bucket[1m])) by (le))
```

### `messaging_api_stage_latency_seconds`

API hot path 내부 구간별 latency입니다.

주요 stage:

- `membership_check`: stream membership 확인
- `postgres_idempotency`: idempotency state path
- `kafka_publish`: Kafka ingress topic publish

```promql
histogram_quantile(0.95, sum(rate(messaging_api_stage_latency_seconds_bucket[1m])) by (le, stage))
```

## Worker metrics

### `messaging_worker_processed_total`

Worker가 event를 처리한 누적 건수입니다.

```promql
sum(rate(messaging_worker_processed_total[1m])) by (result)
```

Worker failure ratio:

```promql
sum(rate(messaging_worker_processed_total{result="failure"}[5m]))
/
clamp_min(sum(rate(messaging_worker_processed_total[5m])), 1)
```

### `messaging_worker_last_success_timestamp`

Worker가 마지막으로 event를 성공 처리한 Unix timestamp입니다.

```promql
time() - max(messaging_worker_last_success_timestamp{job="worker"})
```

값이 계속 증가하면 Worker pod가 살아 있어도 실제 consume / persist 성공이 멈춘 상태일 수 있습니다.

### `messaging_worker_stage_latency_seconds`

Worker 내부 구간별 latency입니다.

주요 stage:

- `db_persist`: PostgreSQL transaction으로 event 영속화
- `request_status_update`: request status 갱신
- `notification_enqueue`: 후속 notification 작업 생성
- `notification_db_insert`: notification 처리 결과 기록

```promql
histogram_quantile(0.95, sum(rate(messaging_worker_stage_latency_seconds_bucket[1m])) by (le, stage))
```

## 비동기 lag

### `messaging_event_persist_lag_seconds`

API가 request를 `accepted` 한 시점부터 Worker가 PostgreSQL에 `persisted` 할 때까지 걸린 end-to-end async lag입니다.

```promql
histogram_quantile(0.95, sum(rate(messaging_event_persist_lag_seconds_bucket[1m])) by (le))
```

### `messaging_queue_wait_seconds`

event가 Worker 처리 전까지 대기한 시간을 해석하는 지표입니다. Kafka-native 관점에서는 consumer-side wait / backlog signal로 봅니다.

```promql
histogram_quantile(0.95, sum(rate(messaging_queue_wait_seconds_bucket[1m])) by (le))
```

## DLQ metrics

### `messaging_dlq_events_total`

Worker가 event를 Kafka DLQ topic으로 보낸 누적 counter입니다.

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

## PostgreSQL metrics

### `messaging_db_pool_in_use`

process별 DB connection checkout 수입니다.

```promql
messaging_db_pool_in_use{job=~"api|worker|dlq-replayer"}
```

API만 높으면 request hot path, Worker만 높으면 persistence path, DLQ Replayer가 높으면 replay path를 먼저 확인합니다.

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

- Kafka bootstrap reachable
- PostgreSQL writable primary reachable
- standby / replication 상태 정상
- Worker consume path 정상

### `degraded`

- PostgreSQL standby 부족 또는 replication lag 상승
- Kafka append는 가능하지만 Worker lag 증가
- DLQ replay 증가
- persistence path 지연

### `not_ready`

- Kafka bootstrap unreachable
- Kafka publish 실패
- API intake state path 장애로 request 수락 불가

더 자세한 readiness 정책은 [RELIABILITY_POLICY.md](RELIABILITY_POLICY.md)를 봅니다.