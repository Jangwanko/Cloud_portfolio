# 관측 지표 안내

이 문서는 Grafana / Prometheus에서 async processing pipeline의 상태를 어떻게 읽는지 정리합니다.

관측 목표는 단순히 Pod가 살아 있는지 확인하는 것이 아니라, 아래 질문에 답하는 것입니다.

- API가 요청을 정상적으로 수락하는가?
- Redis ingress queue backlog가 쌓이는가?
- Worker가 backlog를 따라잡는가?
- `accepted` 된 요청이 PostgreSQL에 늦지 않게 `persisted` 되는가?
- Redis / PostgreSQL HA topology가 degraded 되었는가?
- KEDA가 Worker replica를 실제로 늘렸는가?

## Grafana 패널 기준

`Messaging Portfolio Overview` 대시보드의 패널은 아래 지표를 기준으로 봅니다.

| Panel | PromQL / Metric | 해석 |
| --- | --- | --- |
| `DB Health` | `messaging_health_status{job="api",component="db"}` | API가 보는 PostgreSQL writable primary 상태 |
| `Redis Health` | `messaging_health_status{job="api",component="redis"}` | API가 보는 Redis writable master / Sentinel 상태 |
| `Redis Connected Replicas` | `messaging_redis_connected_replicas{job="api"}` | writable master에 연결된 Redis replica 수 |
| `PostgreSQL Standbys` | `messaging_postgres_standby_count{job="api"}` | pgpool / replication 기준 standby 수 |
| `API Request Rate` | `sum(rate(messaging_api_requests_total[1m])) by (status)` | HTTP status별 API request rate |
| `API Latency` | `messaging_api_request_latency_seconds_bucket` | API 전체 request latency p95 / p99 |
| `Worker Throughput (events only)` | `sum(rate(messaging_worker_processed_total[1m])) by (result)` | Worker event 처리량과 성공 / 실패 비율 |
| `Queue Depth By Queue` | `sum by (queue) (messaging_queue_depth)` | Redis queue별 backlog |
| `Redis Topology Signals` | `messaging_redis_connected_replicas`, `messaging_redis_sentinel_master_ok`, `messaging_redis_sentinel_quorum_ok` | Redis replica / Sentinel 상태 |
| `Redis Replica Link Status` | `messaging_redis_master_link_status{job="api"}` | replica별 master link 상태 |
| `PostgreSQL Replication Capacity` | `messaging_postgres_standby_count`, `messaging_postgres_sync_standby_count`, `messaging_postgres_is_primary` | primary reachability와 standby capacity |
| `PostgreSQL Replication Delay` | `messaging_postgres_replication_delay_bytes_max{job="api"}` | standby replay 지연 최대값 |
| `PostgreSQL Replication States` | `messaging_postgres_replication_state_count{job="api"}` | standby replication state 분포 |
| `DB Failure Reasons (15m)` | `sum by (reason) (increase(messaging_db_failure_total[15m]))` | 최근 15분 DB failure reason별 증가량 |
| `API Stage Latency` | `messaging_api_stage_latency_seconds_bucket` | API hot path 단계별 p95 latency |
| `Worker Stage Latency` | `messaging_worker_stage_latency_seconds_bucket` | Worker 내부 단계별 p95 latency |
| `Accepted To Persisted Lag` | `messaging_event_persist_lag_seconds_bucket` | API accepted부터 PostgreSQL persisted까지의 p95 / p99 lag |
| `Redis Queue Wait Time` | `messaging_queue_wait_seconds_bucket` | Redis enqueue부터 Worker dequeue까지의 p95 / p99 wait time |
| `Worker Replicas` | `kube_deployment_spec_replicas`, `kube_deployment_status_replicas_available`, `kube_horizontalpodautoscaler_status_desired_replicas` | Worker desired / available / KEDA HPA desired replica 비교 |

## Key Interpretation

- `queue_depth` 증가: ingress rate가 Worker 처리량보다 높거나 downstream persistence path가 막힌 상태입니다.
- `queue_wait` 증가: job이 Redis queue에서 오래 대기하는 상태로, Worker 처리량 부족 또는 DB persistence 병목을 의심합니다.
- `accepted_to_persisted_lag` 증가: API는 요청을 수락하지만 PostgreSQL 영속화가 늦어지는 상태입니다. `Queue Wait`, `Worker Stage Latency`, `DB Failure Reasons`를 함께 봅니다.
- `api_latency` 증가: request intake path 병목입니다. `API Stage Latency`에서 membership check, Redis sequence, Redis enqueue 중 어느 단계가 느린지 확인합니다.
- `worker_stage_latency{stage="db_persist"}` 증가: PostgreSQL / pgpool / room sequence lock / disk I/O 병목 가능성이 큽니다.
- Worker replica 증가 후에도 queue가 줄지 않음: 단순 Worker 수 부족보다 DB persistence path 병목일 가능성이 큽니다.

## 장애별 확인 순서

### API latency 상승

확인 순서:
1. `API Latency`
2. `API Stage Latency`
3. `Queue Depth By Queue`
4. API HPA / replica 상태

해석:
- `API Stage Latency`의 `membership_check`, `redis_sequence`, `redis_enqueue` 중 어느 stage가 느린지 확인합니다.
- queue depth가 낮은데 API latency만 높으면 API hot path, Redis round-trip, API CPU 쪽을 먼저 봅니다.
- queue depth도 함께 증가하면 Worker 또는 PostgreSQL persistence path 병목일 수 있습니다.

### Queue backlog 증가

확인 순서:
1. `Queue Depth By Queue`
2. `Worker Throughput (events only)`
3. `Worker Replicas`
4. `Redis Queue Wait Time`
5. `Worker Stage Latency`

해석:
- `message_ingress:p.*` queue가 증가하면 API intake 속도가 Worker 처리 속도보다 빠른 상태입니다.
- Worker replica가 증가하지 않으면 KEDA trigger, Prometheus scrape, `worker-keda-hpa` 상태를 확인합니다.
- Worker replica가 증가했는데도 backlog가 줄지 않으면 PostgreSQL write path나 Worker `db_persist` 병목을 봅니다.

### Accepted to persisted lag 증가

확인 순서:
1. `Accepted To Persisted Lag`
2. `Redis Queue Wait Time`
3. `Worker Stage Latency`
4. `PostgreSQL Replication Capacity`
5. `DB Failure Reasons (15m)`

해석:
- `queue_wait`가 함께 증가하면 Worker가 queue를 따라잡지 못하는 상태입니다.
- `queue_wait`는 낮고 `db_persist`가 높으면 PostgreSQL / pgpool / row lock / disk I/O 쪽 병목일 가능성이 큽니다.
- API latency가 낮아도 이 lag가 증가하면 사용자에게는 요청이 수락됐지만 실제 영속화가 늦어지는 상태입니다.

### DB 장애 또는 degraded

확인 순서:
1. `DB Health`
2. `DB Failure Reasons (15m)`
3. `PostgreSQL Replication Capacity`
4. `PostgreSQL Replication Delay`
5. `PostgreSQL Replication States`

해석:
- PostgreSQL primary가 write 불가여도 Redis enqueue가 가능하면 API readiness는 `degraded`로 볼 수 있습니다.
- 이 경우 API는 요청을 `accepted` 할 수 있고, Worker는 DB 복구 후 persistence를 재개합니다.
- DB failure reason이 recovery 후에도 증가하면 pgpool endpoint, credential, pool, PostgreSQL pod 상태를 확인합니다.

### Redis 장애 또는 degraded

확인 순서:
1. `Redis Health`
2. `Redis Topology Signals`
3. `Redis Replica Link Status`
4. `Redis Connected Replicas`
5. `Queue Depth By Queue`

해석:
- Redis writable master 또는 Sentinel master resolution이 깨지면 intake write path가 막힐 수 있습니다.
- replica count 부족, replica link 이상, Sentinel quorum 저하는 degraded로 해석합니다.
- Redis complete outage와 single-node failover는 별도 시나리오로 봅니다.

### Worker scaling 확인

확인 순서:
1. `Queue Depth By Queue`
2. `Worker Replicas`
3. `Worker Throughput (events only)`
4. `Redis Queue Wait Time`

해석:
- Worker는 CPU HPA가 아니라 KEDA + Prometheus queue depth 기준으로 scale-out합니다.
- KEDA trigger query는 전체 ingress partition backlog를 합산합니다.
- desired replica가 증가했는데 available replica가 따라오지 않으면 Pod scheduling, image, resource, readiness 문제를 봅니다.

## 핵심 Metric Notes

### API

- `messaging_api_requests_total`: HTTP status별 API request counter입니다. 5xx 증가 시 Redis enqueue 실패, DB / Redis health check 실패, API 내부 오류를 확인합니다.
- `messaging_api_request_latency_seconds`: API 전체 request latency입니다. Worker가 PostgreSQL에 persisted할 때까지의 비동기 lag는 포함하지 않습니다.
- `messaging_api_stage_latency_seconds`: API hot path를 stage별로 나눠 봅니다. 주요 stage는 `membership_check`, `redis_idempotency`, `redis_sequence`, `redis_enqueue`입니다.

### Queue / Worker

- `messaging_queue_depth`: Redis queue별 backlog입니다. KEDA Worker scaling의 핵심 입력입니다.
- `messaging_queue_wait_seconds`: Redis queue에 들어간 뒤 Worker가 꺼내기까지의 대기 시간입니다.
- `messaging_worker_processed_total`: Worker가 처리한 event 수입니다. idle 상태에서는 `No data`로 보일 수 있습니다.
- `messaging_worker_stage_latency_seconds`: Worker 내부 병목을 봅니다. 주요 stage는 `db_persist`, `request_status_update`, `notification_enqueue`, `notification_db_insert`입니다.
- `messaging_event_persist_lag_seconds`: API accepted부터 PostgreSQL persisted까지의 end-to-end async lag입니다.

### Redis

- `messaging_redis_connected_replicas`: writable master에 붙은 replica 수입니다. 로컬 ready 기준은 `2+`입니다.
- `messaging_redis_master_link_status`: replica별 master link 상태입니다.
- `messaging_redis_sentinel_master_ok`: Sentinel이 writable master를 식별하는지 봅니다.
- `messaging_redis_sentinel_quorum_ok`: Sentinel quorum이 유지되는지 봅니다.

### PostgreSQL

- `messaging_postgres_is_primary`: pgpool 경유 writable primary reachability입니다.
- `messaging_postgres_standby_count`: standby 수입니다. 로컬 ready 기준은 `2+`입니다.
- `messaging_postgres_sync_standby_count`: sync 또는 quorum standby 수입니다. 로컬 데모에서는 async streaming standby도 ready로 해석합니다.
- `messaging_postgres_replication_state_count`: standby replication state 분포입니다.
- `messaging_postgres_replication_delay_bytes_max`: 가장 큰 replication delay입니다.
- `messaging_db_failure_total`: DB failure reason별 counter입니다. DB health가 내려갔을 때 원인을 좁히는 데 사용합니다.

### Kubernetes / KEDA

- `kube_deployment_spec_replicas`: Deployment가 원하는 Worker replica 수입니다.
- `kube_deployment_status_replicas_available`: 실제 available Worker replica 수입니다.
- `kube_horizontalpodautoscaler_status_desired_replicas`: KEDA가 생성한 HPA의 desired replica 수입니다.

## Prometheus Query 기준

Redis topology와 PostgreSQL replication 지표는 현재 API job이 채웁니다. 따라서 Redis / PostgreSQL 운영 해석에서는 `job="api"` 시계열을 기준으로 봅니다.

Worker도 같은 metric name을 노출할 수 있지만 topology 판정은 API가 수행합니다. Worker 자체 이상 여부는 `component="worker"`, Worker throughput, Worker stage latency, Worker replica 지표로 봅니다.

자세한 metric 설명은 [METRICS_REFERENCE.md](METRICS_REFERENCE.md)에 보존했습니다. readiness 상태 모델은 [RELIABILITY_POLICY.md](RELIABILITY_POLICY.md), 검증 결과는 [TEST_RESULTS.md](TEST_RESULTS.md)에 정리했습니다.
