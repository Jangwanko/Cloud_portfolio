# 관측성

이 문서는 Kafka 기반 event stream pipeline을 Grafana / Prometheus에서 어떻게 읽는지 정리합니다.

관측의 목적은 단순히 Pod 생존 여부를 보는 것이 아니라, 아래 질문에 답하는 것입니다.

- API가 요청을 빠르게 `accepted` 하는가?
- Kafka ingress topic에 쌓인 event를 Worker consumer group이 따라잡는가?
- `accepted` 된 요청이 PostgreSQL에 언제 `persisted` 되는가?
- 병목이 API intake, Kafka lag, Worker 처리량, PostgreSQL persistence 중 어디에 있는가?
- KEDA가 Kafka consumer lag를 기준으로 Worker replica를 늘리는가?

## Grafana 패널

| Panel | PromQL / Metric | Interpretation |
| --- | --- | --- |
| `API Request Rate` | `sum(rate(messaging_api_requests_total[1m])) by (status)` | HTTP status별 request rate |
| `API Latency` | `messaging_api_request_latency_seconds_bucket` | API intake 요청이 응답을 받기까지의 p95 / p99 |
| `API Stage Latency` | `messaging_api_stage_latency_seconds_bucket` | membership, Kafka publish 등 hot path 구간 |
| `Worker Throughput` | `sum(rate(messaging_worker_processed_total[1m])) by (result)` | Worker 처리량과 성공 / 실패 비율 |
| `Worker Stage Latency` | `messaging_worker_stage_latency_seconds_bucket` | DB persist, status update, notification 처리 구간 |
| `Accepted To Persisted Lag` | `messaging_event_persist_lag_seconds_bucket` | API accepted부터 PostgreSQL persisted까지의 async lag |
| `Queue Wait Time` | `messaging_queue_wait_seconds_bucket` | Kafka consume 전까지 대기한 시간에 가까운 Worker-side wait 지표 |
| `Worker Replicas` | `kube_deployment_spec_replicas`, `kube_deployment_status_replicas_available`, `kube_horizontalpodautoscaler_status_desired_replicas` | Worker desired / available / KEDA HPA desired replica 비교 |
| `DB Health` | `messaging_health_status{job="api",component="db"}` | API가 보는 PostgreSQL writable path |
| `PostgreSQL Standbys` | `messaging_postgres_standby_count{job="api"}` | pgpool / replication 기준 standby 수 |
| `PostgreSQL Replication Delay` | `messaging_postgres_replication_delay_bytes_max{job="api"}` | standby replay delay |

Kafka consumer lag는 KEDA Kafka scaler의 external metric과 consumer group 상태로 확인합니다.

## 핵심 해석

- Kafka consumer lag 증가: ingress rate가 Worker 처리량보다 빠르거나 downstream persistence path가 막힌 상태입니다.
- Queue wait 증가: Worker가 backlog를 충분히 빨리 소비하지 못하고 있거나 DB write path가 느린 상태입니다.
- Accepted-to-persisted lag 증가: API는 요청을 수락하지만 PostgreSQL 영속화가 늦어지는 상태입니다.
- API latency 증가: Kafka publish, membership check 등 request intake path 병목입니다.
- Worker `db_persist` stage 증가: PostgreSQL / Pgpool / row lock / disk I/O 병목 가능성이 큽니다.
- Worker replica 증가 후에도 lag가 줄지 않음: 단순 Worker 수 부족보다 PostgreSQL persistence path 병목일 가능성이 높습니다.

## 문제 해결 흐름

### API latency 증가

확인 순서:

1. `API Latency`
2. `API Stage Latency`
3. Kafka publish stage 또는 state stage latency
4. API HPA / replica 상태

해석:

- API stage 중 Kafka publish가 느리면 Kafka broker / network / metadata lookup을 봅니다.
- state 관련 stage가 느리면 API hot path가 다시 DB에 묶이는지 확인합니다.
- API latency는 높지만 accepted-to-persisted lag가 낮으면 persistence보다 intake path 문제입니다.

### Kafka lag 또는 backlog 증가

확인 순서:

1. KEDA Kafka scaler external metric
2. Worker Throughput
3. Worker Replicas
4. Queue Wait Time
5. Worker Stage Latency

해석:

- Worker replica가 늘지 않으면 KEDA ScaledObject, Kafka trigger, consumer group, HPA 상태를 확인합니다.
- Worker replica가 늘었는데 lag가 줄지 않으면 `db_persist` stage와 PostgreSQL 상태를 먼저 봅니다.
- Worker failure가 함께 증가하면 DLQ topic과 retry reason을 확인합니다.

### Accepted-to-persisted lag 증가

확인 순서:

1. `Accepted To Persisted Lag`
2. `Queue Wait Time`
3. `Worker Stage Latency`
4. `DB Failure Reasons`
5. PostgreSQL replication / Pgpool 상태

해석:

- queue wait도 함께 증가하면 Worker 소비 지연 또는 backlog 상태입니다.
- queue wait은 낮고 `db_persist`만 높으면 PostgreSQL write path 자체가 느린 상태입니다.
- API latency는 낮은데 lag만 증가하면 사용자는 빠르게 응답을 받지만 실제 영속화가 늦어지는 상태입니다.

### DLQ 증가

확인 순서:

1. `GET /v1/dlq/ingress?limit=5`
2. Worker failure logs
3. `messaging_worker_processed_total{result="failure"}`
4. DLQ Replayer logs

해석:

- retry 한도를 넘긴 event는 Kafka DLQ topic으로 이동합니다.
- DLQ payload의 `failed_reason`, `retry_count`, `replay_count`를 보고 재처리 가능 여부를 판단합니다.
- replay 후 같은 이유로 다시 DLQ에 쌓이면 일시 장애가 아니라 데이터 조건 또는 persistence logic 문제일 수 있습니다.

## Metric 메모

### API

- `messaging_api_requests_total`: HTTP status별 API request counter입니다.
- `messaging_api_request_latency_seconds`: API가 요청을 받아 응답하기까지의 latency입니다. Worker persistence 완료까지의 시간은 포함하지 않습니다.
- `messaging_api_stage_latency_seconds`: API hot path를 stage별로 나눠 봅니다.

### Worker

- `messaging_worker_processed_total`: Worker가 event를 처리한 누적 건수입니다.
- `messaging_worker_stage_latency_seconds`: Worker 내부 병목을 stage별로 봅니다.
- `messaging_event_persist_lag_seconds`: API accepted부터 PostgreSQL persisted까지의 end-to-end async lag입니다.
- `messaging_queue_wait_seconds`: event가 Worker에 의해 처리되기 전까지 대기한 시간을 해석하는 지표입니다.

### PostgreSQL

- `messaging_postgres_is_primary`: pgpool 경유 writable primary reachability입니다.
- `messaging_postgres_standby_count`: standby 수입니다.
- `messaging_postgres_sync_standby_count`: sync 또는 quorum standby 수입니다.
- `messaging_postgres_replication_delay_bytes_max`: 가장 큰 replication delay입니다.
- `messaging_db_failure_total`: DB failure reason별 counter입니다.

### Kubernetes / KEDA

- `kube_deployment_spec_replicas`: Deployment가 원하는 Worker replica 수입니다.
- `kube_deployment_status_replicas_available`: 실제 available Worker replica 수입니다.
- `kube_horizontalpodautoscaler_status_desired_replicas`: KEDA가 생성한 HPA의 desired replica 수입니다.

자세한 metric 설명은 [METRICS_REFERENCE.md](METRICS_REFERENCE.md), readiness 상태 모델은 [RELIABILITY_POLICY.md](RELIABILITY_POLICY.md), 검증 결과는 [TEST_RESULTS.md](TEST_RESULTS.md)에 정리되어 있습니다.
