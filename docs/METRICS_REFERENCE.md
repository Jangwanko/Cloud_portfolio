# Metrics Reference

이 문서는 Kafka-native 포트폴리오의 Prometheus / Grafana 지표를 빠르게 찾기 위한 참고 문서입니다.
장애별 확인 순서와 해석은 [OBSERVABILITY.md](OBSERVABILITY.md)를 먼저 봅니다.

## Dashboard Groups

- API request rate / latency
- API hot path stage latency
- Worker throughput / failure rate
- Worker stage latency
- Accepted-to-persisted async lag
- Queue wait / backlog interpretation
- PostgreSQL primary / standby / replication state
- Worker replica count and KEDA desired replicas
- DB failure reason

Kafka consumer lag는 KEDA Kafka scaler와 consumer group 상태로 확인합니다.

## Health

### `messaging_health_status`

component별 health 신호입니다.

- `component="kafka"`: Kafka bootstrap reachable
- `component="db"`: PostgreSQL writable primary 확인 기준
- `component="worker"`: Worker 처리 루프 상태

대표 쿼리:

```promql
messaging_health_status{job="api",component="kafka"}
messaging_health_status{job="api",component="db"}
```

## API Metrics

### `messaging_api_requests_total`

HTTP status별 API request counter입니다.

```promql
sum(rate(messaging_api_requests_total[1m])) by (status)
```

5xx가 증가하면 Kafka publish 실패, DB/state path 실패, API 내부 오류를 확인합니다.

### `messaging_api_request_latency_seconds`

API가 request를 받고 response를 반환하기까지의 시간입니다.

중요: 이 값은 Worker가 PostgreSQL에 persisted 완료할 때까지의 시간을 포함하지 않습니다. persisted 완료까지의 async lag는 `messaging_event_persist_lag_seconds`로 봅니다.

```promql
histogram_quantile(
  0.95,
  sum(rate(messaging_api_request_latency_seconds_bucket[1m])) by (le)
)
```

### `messaging_api_stage_latency_seconds`

API hot path 내부 구간별 latency입니다.

주요 stage:

- `membership_check`: stream membership 확인
- `kafka_publish`: Kafka ingress topic publish

대표 쿼리:

```promql
histogram_quantile(
  0.95,
  sum(rate(messaging_api_stage_latency_seconds_bucket[1m])) by (le, stage)
)
```

해석:

- `kafka_publish` 증가: Kafka broker / network / producer metadata path 확인
- state stage가 증가하면 API hot path가 다시 DB에 묶이는지 확인
- API latency 증가 + event persist lag 정상: intake path 병목 가능성

## Worker Metrics

### `messaging_worker_processed_total`

Worker가 event를 처리한 누적 건수입니다.

```promql
sum(rate(messaging_worker_processed_total[1m])) by (result)
```

해석:

- `success` 증가: Kafka consume -> PostgreSQL persist path 정상
- `failure` 증가: retry / DLQ 이동 가능성 확인
- idle 상태에서는 `No data`가 정상일 수 있음

### `messaging_worker_stage_latency_seconds`

Worker 내부 구간별 latency입니다.

주요 stage:

- `db_persist`: PostgreSQL transaction으로 event 영속화
- `request_status_update`: request status 갱신
- `notification_enqueue`: 후속 notification 작업 생성
- `notification_db_insert`: notification 처리 결과 기록

```promql
histogram_quantile(
  0.95,
  sum(rate(messaging_worker_stage_latency_seconds_bucket[1m])) by (le, stage)
)
```

해석:

- `db_persist` 증가: PostgreSQL / Pgpool / row lock / disk I/O 병목
- status update 증가: request state path 병목
- notification stage 증가: event persist 이후 후속 처리 병목

## Async Lag

### `messaging_event_persist_lag_seconds`

API가 request를 `accepted` 한 시점부터 Worker가 PostgreSQL에 `persisted` 할 때까지 걸린 end-to-end async lag입니다.

```promql
histogram_quantile(
  0.95,
  sum(rate(messaging_event_persist_lag_seconds_bucket[1m])) by (le)
)
```

해석:

- API latency는 낮고 persist lag만 높음: 사용자는 빠르게 응답을 받지만 persistence가 밀림
- persist lag + queue wait 상승: Worker backlog 또는 consumer lag 가능성
- persist lag + `db_persist` 상승: PostgreSQL persistence 병목 가능성

### `messaging_queue_wait_seconds`

event가 Worker 처리 전까지 대기한 시간을 해석하는 지표입니다. Kafka-native 관점에서는 consumer-side wait / backlog signal로 봅니다.

```promql
histogram_quantile(
  0.95,
  sum(rate(messaging_queue_wait_seconds_bucket[1m])) by (le)
)
```

해석:

- queue wait 증가: Worker 처리량이 ingress rate를 따라가지 못함
- queue wait 낮음 + `db_persist` 높음: queue보다 DB write path가 직접 병목
- queue wait 증가 + Worker replica 미증가: KEDA trigger / HPA / consumer group 상태 확인

## PostgreSQL Metrics

### `messaging_postgres_is_primary`

pgpool 경유로 writable primary가 reachable한지 나타냅니다.

- `1`: primary reachable
- `0`: write path unavailable

### `messaging_postgres_standby_count`

pgpool이 up 상태로 보고하는 standby 수입니다.

- `2+`: 로컬 HA ready 기준
- `1 이하`: degraded 기준

### `messaging_postgres_sync_standby_count`

sync 또는 quorum 기준을 만족하는 standby 수입니다.

로컬 kind 데모에서는 async streaming standby도 정상 운영 상태로 취급합니다. 이 값은 현재 복제가 sync인지 async인지 설명하는 보조 지표입니다.

### `messaging_postgres_replication_state_count`

standby replication state 분포입니다. 정상 기준에서는 standby가 `streaming`으로 관측되는 것이 기대값입니다.

### `messaging_postgres_replication_delay_bytes_max`

가장 큰 replication delay입니다. 임계치를 넘으면 replication lag 상승으로 보고 `degraded`로 해석합니다.

### `messaging_db_failure_total`

DB failure reason별 counter입니다.

```promql
sum by (reason) (increase(messaging_db_failure_total[15m]))
```

## KEDA / Kubernetes Metrics

Worker autoscaling은 CPU 기반 HPA가 아니라 KEDA 기반 Kafka consumer lag scaling을 사용합니다.

- scale target: `Deployment/worker`
- min replica: `2`
- max replica: `8`
- trigger: Kafka topic lag
- topic: `message-ingress`
- consumer group: `message-worker`
- lag threshold: `400`

Replica 확인:

```promql
kube_deployment_spec_replicas{namespace="messaging-app",deployment="worker"}
kube_deployment_status_replicas_available{namespace="messaging-app",deployment="worker"}
kube_horizontalpodautoscaler_status_desired_replicas{namespace="messaging-app",horizontalpodautoscaler="worker-keda-hpa"}
```

해석:

- desired replica 증가 + available replica 미증가: scheduling, image, readiness, resource 문제
- desired replica 미증가 + lag 증가: KEDA Kafka trigger / ScaledObject / Prometheus external metric 확인
- replica 증가 후 lag 유지: Worker 수보다 DB persistence 병목 가능성

## State Model

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
