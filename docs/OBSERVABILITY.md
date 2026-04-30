# 관측성

이 문서는 Kafka 기반 event stream pipeline을 Grafana / Prometheus에서 어떻게 읽는지 정리합니다.

관측의 목적은 단순히 Pod 생존 여부를 보는 것이 아니라, 아래 질문에 답하는 것입니다.

- API가 요청을 빠르게 `accepted` 하는가?
- Kafka ingress topic에 쌓인 event를 Worker consumer group이 따라잡는가?
- DB commit 이후 snapshot compacted topic 기반 local materialized cache가 DB failover 중 degraded read를 보조하는가?
- message read 응답의 `source`, `degraded`, `snapshot_age_seconds`로 cache-first read가 정상 동작하는가?
- read cache hit ratio, snapshot age, degraded read count, snapshot consumer lag로 read path가 DB failover를 얼마나 흡수하는가?
- `accepted` 된 요청이 PostgreSQL에 언제 `persisted` 되는가?
- 병목이 API intake, Kafka lag, Worker 처리량, PostgreSQL persistence 중 어디에 있는가?
- KEDA가 Kafka consumer lag를 기준으로 Worker replica를 늘리는가?

## Grafana 패널

| Panel | PromQL / Metric | Interpretation |
| --- | --- | --- |
| `API Request Rate` | `sum(rate(messaging_api_requests_total[1m])) by (status)` | HTTP status별 request rate |
| `API Latency` | `messaging_api_request_latency_seconds_bucket` | API intake 요청이 응답을 받기까지의 p95 / p99 |
| `API Stage Latency` | `messaging_api_stage_latency_seconds_bucket` | membership, Kafka publish 등 hot path 구간 |
| `Worker Throughput By Result` | `sum(rate(messaging_worker_processed_total[1m])) by (result)` | Worker 처리량과 성공 / 실패 비율 |
| `Worker Failure Ratio` | `messaging_worker_processed_total{result="failure"}` 비율 | Worker 처리 실패가 retry / DLQ로 이어지는지 확인 |
| `Worker Last Success Age` | `time() - max(messaging_worker_last_success_timestamp{job="worker"})` | Worker pod는 살아 있지만 실제 처리가 멈춘 상태 감지 |
| `Worker Stage Latency` | `messaging_worker_stage_latency_seconds_bucket` | DB persist, status update, notification 처리 구간 |
| `Accepted To Persisted Lag` | `messaging_event_persist_lag_seconds_bucket` | API accepted부터 PostgreSQL persisted까지의 async lag |
| `Queue Wait Time` | `messaging_queue_wait_seconds_bucket` | Kafka consume 전까지 대기한 시간에 가까운 Worker-side wait 지표 |
| `DB Pool In Use` | `messaging_db_pool_in_use` | API / Worker / DLQ Replayer의 DB connection checkout 압력 |
| `DLQ Events And Replay` | `messaging_dlq_events_total`, `messaging_dlq_replay_total` | DLQ 유입, replay, replay guard skip 흐름 |
| `Worker Replicas` | `kube_deployment_spec_replicas`, `kube_deployment_status_replicas_available`, `kube_horizontalpodautoscaler_status_desired_replicas` | Worker desired / available / KEDA HPA desired replica 비교 |
| `API Scaling` | API deployment / HPA replica 지표 | API HPA와 실제 available replica 비교 |
| `Pod Restarts` | `kube_pod_container_status_restarts_total` | CrashLoopBackOff, OOMKilled, 낮은 사양 신호 |
| `Unavailable Replicas` | `kube_deployment_status_replicas_unavailable` | rollout, scheduling, readiness 문제 |
| `DB Health` | `messaging_health_status{job="api",component="db"}` | API가 보는 PostgreSQL writable path |
| `PostgreSQL Standbys` | `messaging_postgres_standby_count{job="api"}` | pgpool / replication 기준 standby 수 |
| `PostgreSQL Replication Delay` | `messaging_postgres_replication_delay_bytes_max{job="api"}` | standby replay delay |

현재 dashboard는 kafka-exporter가 제공하는 `kafka_consumergroup_lag`, `kafka_brokers`, `kafka_topic_partition_current_offset`를 직접 보고, application-side 보조 신호로 `Queue Wait Time`, Worker throughput, KEDA desired replica를 함께 해석합니다.

## 핵심 해석

- Kafka consumer lag 증가: ingress rate가 Worker 처리량보다 빠르거나 downstream persistence path가 막힌 상태입니다.
- Queue wait 증가: Worker가 backlog를 충분히 빨리 소비하지 못하고 있거나 DB write path가 느린 상태입니다.
- Accepted-to-persisted lag 증가: API는 요청을 수락하지만 PostgreSQL 영속화가 늦어지는 상태입니다.
- API latency 증가: Kafka publish 또는 인증 토큰 처리 등 request intake path 병목입니다. Event write path에서 PostgreSQL membership / idempotency 선조회가 보이면 설계 회귀로 봅니다.
- Worker `db_persist` stage 증가: PostgreSQL / Pgpool / row lock / disk I/O 병목 가능성이 큽니다.
- Worker replica 증가 후에도 lag가 줄지 않음: 단순 Worker 수 부족보다 PostgreSQL persistence path 병목일 가능성이 높습니다.
- Worker last success age 증가: Worker pod 상태보다 실제 consume / persist 성공 여부를 우선 확인합니다.
- DB pool in use 증가: API / Worker / DLQ Replayer 중 어느 process가 DB connection을 오래 붙잡는지 분리해서 봅니다.
- Pod restart 증가: 낮은 사양, OOMKilled, CrashLoopBackOff, image / readiness 문제를 먼저 확인합니다.

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
- `replay_count`가 `max_replay_count` 이상이면 자동 replay 대상에서 제외된 것으로 봅니다.

## Metric 메모

### API

- `messaging_api_requests_total`: HTTP status별 API request counter입니다.
- `messaging_api_request_latency_seconds`: API가 요청을 받아 응답하기까지의 latency입니다. Worker persistence 완료까지의 시간은 포함하지 않습니다.
- `messaging_api_stage_latency_seconds`: API hot path를 stage별로 나눠 봅니다.

### Worker

- `messaging_worker_processed_total`: Worker가 event를 처리한 누적 건수입니다.
- `messaging_worker_last_success_timestamp`: Worker가 마지막으로 event를 성공 처리한 Unix timestamp입니다.
- `messaging_worker_failures_total`: Worker loop failure 누적 건수입니다.
- `messaging_worker_stage_latency_seconds`: Worker 내부 병목을 stage별로 봅니다.
- `messaging_event_persist_lag_seconds`: API accepted부터 PostgreSQL persisted까지의 end-to-end async lag입니다.
- `messaging_queue_wait_seconds`: event가 Worker에 의해 처리되기 전까지 대기한 시간을 해석하는 지표입니다.
- `messaging_dlq_events_total`: Worker가 Kafka DLQ로 보낸 event 수입니다.
- `messaging_dlq_replay_total`: DLQ Replayer의 replay / max replay skip 결과입니다.

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

자세한 metric 설명은 [METRICS_REFERENCE.md](METRICS_REFERENCE.md), readiness 상태 모델은 [RELIABILITY_POLICY.md](RELIABILITY_POLICY.md), 장애 대응 절차는 [RUNBOOK.md](RUNBOOK.md), 검증 결과는 [TEST_RESULTS.md](TEST_RESULTS.md)에 정리되어 있습니다.
## Alert Probe

Prometheus alert rule과 Grafana 운영 패널이 실제 metric 변화에 연결되어 있는지는 아래 스크립트로 확인합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_operational_alerts.ps1 -SkipReset
```

이 검증은 `MessagingDlqEventsIncreasing`, `MessagingDlqReplayBlocked`를 실제 `firing` 상태까지 관찰하고, `MessagingDeploymentUnavailableReplicas`가 `pending` 또는 `firing`으로 전환되는지 확인합니다. 성능 수치를 측정하는 suite가 아니라 metric scrape, alert evaluation, Kubernetes 상태 지표 배선을 확인하는 운영성 테스트입니다.
## DLQ Summary 해석

DLQ 패널에서 증가 신호가 보이면 `GET /v1/dlq/ingress/summary`로 reason과 replay 가능 상태를 먼저 나눕니다.

- `by_reason`: 실패 원인별 분포입니다.
- `replayable`: replay guard에 걸리지 않은 event 수입니다.
- `blocked`: `DLQ_REPLAY_MAX_COUNT`에 도달해 자동 replay에서 제외된 event 수입니다.
- `oldest_age_seconds`: 오래 남아있는 DLQ가 있는지 확인하는 age 신호입니다.
- `by_stream`: 특정 stream에 DLQ가 몰리는지 확인합니다.

이 API는 Prometheus counter보다 payload에 가까운 운영 조회입니다. 알림은 “증가했다”를 알려주고, summary API는 “무엇이 왜 쌓였는가”를 확인합니다.
## Dashboard Operator Links

Grafana dashboard에는 `DLQ Operator Links` 패널을 둡니다. `DLQ Events And Replay` 패널에서 변화가 보이면 해당 링크 패널의 summary endpoint와 Runbook을 따라갑니다.

- Summary: `GET /v1/dlq/ingress/summary?limit=200&sample_limit=5`
- Samples: `GET /v1/dlq/ingress?limit=20`
- Runbook: `DLQ Summary Triage`
## Kafka Exporter Panels

Kafka 자체 상태는 kafka-exporter를 통해 직접 봅니다.

| Panel | Metric | Interpretation |
| --- | --- | --- |
| `Kafka Broker Count` | `kafka_brokers` | exporter가 보는 broker 수. 로컬 HA 기준은 `3`입니다. |
| `Kafka Consumer Group Lag` | `kafka_consumergroup_lag{consumergroup="message-worker"}` | Worker consumer group이 topic별로 따라잡지 못한 message 수입니다. |
| `Kafka Topic Partitions` | `kafka_topic_partition_current_offset` | topic별 partition 구성을 확인합니다. |

`Kafka Consumer Group Lag`가 증가하면서 `Worker Throughput`이 낮으면 Worker 처리 병목을 먼저 봅니다. lag가 증가하면서 `db_persist` stage도 증가하면 PostgreSQL / Pgpool persistence path를 먼저 봅니다.
