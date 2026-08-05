# 관측성

Reliable Event Processing System 관측 범위:

- intake
- persistence
- lag
- DLQ
- replica 상태
- PostgreSQL read availability

Prometheus의 `messaging_*` metric prefix와 기존 Grafana dashboard UID/title은 시계열·링크 호환을 위한 물리 식별자로 유지합니다. 포트폴리오의 현재 의미 모델은 generic event/stream 기준입니다.

관측 기준:

- API가 요청을 빠르게 `accepted` 하는가?
- Kafka ingress topic에 쌓인 event를 Worker consumer group이 따라잡는가?
- readiness와 Worker 운영 조회가 서로 다른 endpoint와 판정 기준을 유지하는가?
- request status와 event list 조회가 PostgreSQL 정상 시 완료되고, DB read 장애 시 `503`으로 명확히 실패하는가?
- Worker replica 정보가 readiness 판정과 분리된 `/ops/summary`와 Grafana에서 일치하는가?
- `accepted` 된 요청이 PostgreSQL에 언제 `persisted` 되는가?
- 병목이 API intake, Kafka lag, Worker 처리량, PostgreSQL persistence 중 어디에 있는가?
- KEDA가 Kafka consumer lag를 기준으로 Worker replica를 늘리는가?

## Grafana 패널

| Panel | PromQL / Metric | Interpretation |
| --- | --- | --- |
| `Kafka Intake Status` | `min(messaging_health_status{job="api",component="kafka"})` instant query | `Healthy`/`Unavailable`로 현재 API replica 전체의 Kafka append 경로 상태 표시 |
| `PostgreSQL Primary` | `min(messaging_postgres_is_primary{job="api"})` instant query | `Active`/`Unavailable`로 Pgpool writable primary 도달 가능 여부 표시 |
| `Worker Status` | Worker Deployment `available replicas / spec replicas` instant query | `Available`/`Unavailable`로 Kubernetes가 요구한 Worker replica와 실제 available replica 일치 여부 표시. 실제 수는 `Worker Replicas`에서 확인 |
| `PostgreSQL Standbys` | `min(messaging_postgres_standby_count{job="api"})` instant query | API pod별 중복을 제거한 현재 standby 수를 `N Ready`로 표시 |
| `API Request Rate` | `sum(rate(messaging_api_requests_total[1m])) by (status)` | HTTP status별 request rate |
| `API 5xx Ratio` | 전체 request 대비 `5xx`의 5분 비율, 표시 범위 `0~100%` | user-visible server error ratio |
| `API Latency` | `messaging_api_request_latency_seconds_bucket[5m]` | API intake 요청이 응답을 받기까지의 p95 / p99 |
| `API Stage Latency` | `messaging_api_stage_latency_seconds_bucket[5m]` | membership, Kafka publish 등 hot path 구간 |
| `Worker Throughput By Result` | `sum(rate(messaging_worker_processed_total{job="worker"}[1m])) by (result)` | core Worker의 `success` / `rejected` / `dlq` / failure 처리량 |
| `Worker Failure Ratio` | `messaging_worker_processed_total{job="worker",result="failure"}` 비율, 표시 범위 `0~100%` | core Worker unexpected failure와 seek-back 확인 |
| `Worker Last Success Age` | `time() - max(messaging_worker_last_success_timestamp{job="worker"})` | Worker pod는 살아 있지만 실제 처리가 멈춘 상태 감지 |
| `Worker Stage Latency` | `messaging_worker_stage_latency_seconds_bucket{job="worker"}` | core Worker DB persist / status update 구간 |
| `API Queue To DB Commit` | `messaging_event_persist_lag_seconds_bucket{job="worker"}` | Kafka append 전에 기록한 API `queued_at`부터 Worker PostgreSQL `commit()` 반환 직후까지. queue wait·Worker 처리·DB 작업·inline retry 포함 |
| `API Queue To Worker Start` | `messaging_queue_wait_seconds_bucket{job="worker"}` | Kafka append 전에 기록한 API `queued_at`부터 Worker handler 시작까지. Kafka publish 시간을 포함하는 backlog 보조 지표 |
| `DB Pool In Use` | `sum by (job) (messaging_db_pool_in_use{job=~"api|worker|notification-worker|dlq-replayer"})` | replica 전체의 DB connection checkout을 workload별 합계로 표시 |
| `DLQ Events And Replay` | `messaging_dlq_events_total`, `messaging_dlq_replay_total` | DLQ 유입, replay, replay guard skip 흐름 |
| `Worker Scaling` | `kube_deployment_spec_replicas`, `kube_deployment_status_replicas_available`, `kube_horizontalpodautoscaler_status_desired_replicas` | Worker desired / available / KEDA HPA desired replica 비교 |
| `API Scaling` | API deployment / HPA replica 지표 | API HPA와 실제 available replica 비교 |
| `Pod Restarts` | `kube_pod_container_status_restarts_total` | CrashLoopBackOff, OOMKilled, 낮은 사양 신호 |
| `Unavailable Replicas` | `kube_deployment_status_replicas_unavailable` | rollout, scheduling, readiness 문제 |
| `DB Health` | `messaging_health_status{job="api",component="db"}` | API가 보는 PostgreSQL writable path |
| `PostgreSQL Standbys` | `messaging_postgres_standby_count{job="api"}` | pgpool / replication 기준 standby 수 |
| `PostgreSQL Replication Delay` | `messaging_postgres_replication_delay_bytes_max{job="api"}` | standby replay delay |

현재 dashboard 기준:

- kafka-exporter 직접 지표: `kafka_consumergroup_lag`, `kafka_brokers`, `kafka_topic_partition_current_offset`
- application-side 보조 신호: `API Queue To Worker Start`, Worker throughput, KEDA desired replica
- 해석 방식: broker / lag / Worker 처리량 동시 확인
- 첫 화면 순서: 현재 상태 → `Worker Scaling`·`Kafka Consumer Group Lag` → API request rate·latency
- 상태 색상: HTTP `2xx` 초록, `4xx` 노랑, `5xx` 빨강; 5xx·Worker failure ratio `1%` 노랑, `5%` 빨강
- 상태 metric 부재: 빨간 `No data` 표시

Prometheus scrape topology:

- API, Worker, notification-worker, DLQ Replayer: headless Service의 DNS A record discovery로 ready replica별 scrape
- kafka-exporter와 Kubernetes metrics: 별도 scrape job
- 여러 replica의 counter/rate: `sum`으로 집계, pod별 이상은 `instance`/`pod` label로 분리
- 상단 상태 패널: 현재 시점 instant query와 `min` 집계로 과거 종료 pod 및 replica별 중복값 제외
- `MessagingMetricsTargetMissing`: API, core Worker, DLQ Replayer, kafka-exporter의 `up=0` 또는 series 부재 감지
- `MessagingNotificationConsumerLagHigh`: notification-worker consumer group lag이 5분 동안 `100` 초과 시 warning

## 핵심 해석

- Kafka consumer lag 증가: ingress rate가 Worker 처리량보다 빠르거나 downstream persistence path 병목
- Queue wait 증가: Worker backlog 소비 지연 또는 DB write path 지연
- API queued-at-to-DB-commit lag 증가: Kafka append 전 `queued_at` 기록 이후 PostgreSQL commit 반환까지 지연
- PowerShell historical accepted-to-persisted: PostgreSQL row `created_at` / row-visible proxy, Grafana histogram과 별도
- PowerShell current `accepted_to_status_observed_ms`: client의 `persisted` status 관측까지이며 polling/network 포함, Grafana histogram과 별도
- API latency 증가: Kafka publish, 인증 토큰 처리 등 request intake path 병목
- PostgreSQL membership / idempotency 선조회 노출: event write path 설계 회귀 후보
- Worker `db_persist` stage 증가: PostgreSQL / Pgpool / row lock / disk I/O 병목 후보
- Worker replica 증가 후 lag 유지: PostgreSQL persistence path 병목 우선 확인
- Worker last success age 증가: pod 상태보다 consume / persist 성공 여부 우선 확인
- DB pool in use 증가: API / Worker / notification-worker / DLQ Replayer DB connection 점유 분리 확인
- Pod restart 증가: 낮은 사양, OOMKilled, CrashLoopBackOff, image / readiness 문제 확인

## 문제 해결 흐름

### API latency 증가

확인 순서:

1. `API Latency`
2. `API Stage Latency`
3. Kafka publish stage 또는 state stage latency
4. API HPA / replica 상태

해석:

- Kafka publish stage 지연: Kafka broker / network / metadata lookup 확인
- state stage 지연: API hot path DB 결합 여부 확인
- API latency 높음 + API queued-at-to-DB-commit lag 낮음: persistence보다 intake path 문제

### Kafka lag 또는 backlog 증가

확인 순서:

1. KEDA Kafka scaler external metric
2. Worker Throughput
3. Worker Replicas
4. API Queue To Worker Start
5. Worker Stage Latency

해석:

- Worker replica 미증가: KEDA ScaledObject, Kafka trigger, consumer group, HPA 상태 확인
- Worker replica 증가 + lag 유지: `db_persist` stage와 PostgreSQL 상태 우선 확인
- Worker failure 동반 증가: DLQ topic과 retry reason 확인

### API queued-at-to-DB-commit lag 증가

확인 순서:

1. `API Queue To DB Commit`
2. `API Queue To Worker Start`
3. `Worker Stage Latency`
4. `DB Failure Reasons`
5. PostgreSQL replication / Pgpool 상태

해석:

- queue wait 동반 증가: Worker 소비 지연 또는 backlog 상태
- queue wait 낮음 + `db_persist` 높음: PostgreSQL write path 지연
- API latency 낮음 + lag 증가: 사용자 응답은 빠르지만 실제 영속화 지연

### DLQ 증가

확인 순서:

1. `GET /v1/dlq/ingress?limit=5`
2. Worker failure logs
3. `messaging_worker_processed_total{job="worker",result="failure"}`
4. DLQ Replayer logs

해석:

- retry 한도 초과 event: Kafka DLQ topic 이동
- DLQ payload 확인: `failed_reason`, `retry_count`, `replay_count`
- replay 후 같은 이유 반복: 데이터 조건 또는 persistence logic 문제 후보
- `replay_count >= max_replay_count`: 자동 replay 대상 제외

## Metric 메모

### API

- `messaging_api_requests_total`: HTTP status별 API request counter
- `messaging_api_request_latency_seconds`: API 요청 수신부터 응답까지의 latency, Worker persistence 완료 시간 제외
- `messaging_api_stage_latency_seconds`: API hot path를 stage별로 나눠 봅니다.

### Worker

- `messaging_worker_processed_total`: Worker event 처리 누적 건수
- `messaging_worker_last_success_timestamp`: Worker 마지막 event 성공 처리 Unix timestamp
- `messaging_worker_failures_total`: Worker loop failure 누적 건수
- `messaging_worker_stage_latency_seconds`: Worker 내부 병목을 stage별로 봅니다.
- `messaging_event_persist_lag_seconds`: payload `queued_at`부터 Worker의 PostgreSQL `commit()` 반환 직후 `persisted_at`까지의 lag. post-commit publish 시간 제외
- `messaging_queue_wait_seconds`: Kafka append 전 API `queued_at`부터 Worker handler 시작까지. Kafka publish 시간 포함
- `messaging_dlq_events_total`: Worker가 Kafka DLQ로 보낸 event 수
- `messaging_dlq_replay_total`: DLQ Replayer replay / max replay skip 결과
- `messaging_notification_publish_failures_total`: core DB commit 이후 notification job publish 실패 수. persistence rollback을 뜻하지 않으며 notification 누락 조사 신호

### PostgreSQL

- `messaging_postgres_is_primary`: pgpool 경유 writable primary reachability
- `messaging_postgres_standby_count`: standby 수
- `messaging_postgres_sync_standby_count`: sync 또는 quorum standby 수
- `messaging_postgres_replication_delay_bytes_max`: 가장 큰 replication delay
- `messaging_db_failure_total`: DB failure reason별 counter

### Kubernetes / KEDA

- `kube_deployment_spec_replicas`: Deployment desired Worker replica 수
- `kube_deployment_status_replicas_available`: 실제 available Worker replica 수
- `kube_horizontalpodautoscaler_status_desired_replicas`: KEDA 생성 HPA desired replica 수

자세한 metric 설명은 [METRICS_REFERENCE.md](METRICS_REFERENCE.md), readiness 상태 모델은 [RELIABILITY_POLICY.md](RELIABILITY_POLICY.md), 장애 대응 절차는 [RUNBOOK.md](RUNBOOK.md), 검증 결과는 [TEST_RESULTS.md](TEST_RESULTS.md)에 정리되어 있습니다.

## Alert Probe

Prometheus alert rule과 Grafana 운영 패널이 실제 metric 변화에 연결되어 있는지는 아래 스크립트로 확인합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_operational_alerts.ps1 -SkipReset
```

이 검증은 `MessagingDlqEventsIncreasing`, `MessagingDlqReplayBlocked`를 실제 `firing` 상태까지 관찰하고, `MessagingDeploymentUnavailableReplicas`가 `pending` 또는 `firing`으로 전환되는지 확인합니다. 검증 대상은 metric scrape, alert evaluation, Kubernetes 상태 지표 배선입니다. 성능 수치 측정은 범위에서 제외합니다. `MessagingMetricsTargetMissing`과 `MessagingNotificationConsumerLagHigh`는 현재 rule source에 포함되어 있으나 같은 장애 주입 script의 실행 증거는 아직 없습니다.

## DLQ Summary 해석

DLQ 패널에서 증가 신호가 보이면 `GET /v1/dlq/ingress/summary`의 recent log sample로 reason과 replay guard 상태를 먼저 나눕니다. Summary count는 unresolved backlog가 아닙니다.

- `by_reason`: 실패 원인별 분포
- `replayable`: replay guard에 걸리지 않은 event 수
- `blocked`: 조회 sample에서 재주입할 수 없는 event 수. malformed payload, identity/counter 위반, alias 충돌, 필수 식별자 누락, `DLQ_REPLAY_MAX_COUNT` 도달을 포함
- `oldest_sample_age_seconds`: 조회 sample에서 가장 오래된 append-only log record age. unresolved age 제외
- `by_stream`: 특정 stream DLQ 집중 여부 확인

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
| `Kafka Consumer Group Lag` | `sum by (consumergroup) (clamp_min(kafka_consumergroup_lag{consumergroup=~"message-worker|notification-worker"}, 0))` | persistence·notification consumer group별 전체 lag입니다. topic별 선과 total 선을 겹치지 않습니다. |
| `Kafka Topic Partitions` | `kafka_topic_partition_current_offset` | topic별 partition 구성을 확인합니다. |

`Kafka Consumer Group Lag`가 증가하면서 core `Worker Throughput`이 낮으면 Worker 처리 병목을 먼저 봅니다. lag가 증가하면서 `db_persist` stage도 증가하면 PostgreSQL / Pgpool persistence path를 먼저 봅니다. persistence와 notification consumer group은 legend로 분리합니다.

### Readiness와 Operations Summary 경계

`/health/ready`:

- schema startup
- Kafka bootstrap/append 연결
- PostgreSQL primary·standby·sync·replication guardrail
- non-local auth secret 안전성

`/ops/summary`:

- Worker desired replica
- Worker available replica
- KEDA HPA desired replica
- Worker max replica
- Prometheus 단일 query 결과를 15초 재사용

Worker 조회 실패는 readiness state를 바꾸지 않습니다. 데모와 운영 화면은 `source=unavailable`과 `error`를 표시하고 Grafana 시계열을 함께 확인합니다.

## Ordering / Failure Injection 관측

`scripts/ordering_failure_injection.py`는 pass/fail 판정을 새 Prometheus metric으로 만들지 않습니다. 실험 결과는 PostgreSQL `messages` table 조회 결과로 판정하고 `results/ordering-failure/latest.json`에 저장합니다.

Grafana는 실험 중 시스템 반응을 보는 용도로 사용합니다.

| 관측점 | PromQL | 해석 |
| --- | --- | --- |
| Kafka consumer lag | `sum(clamp_min(kafka_consumergroup_lag{consumergroup="message-worker"}, 0))` | DB write path가 막히는 동안 backlog가 쌓이고 복구 후 0으로 drain되는지 확인 |
| API queued-at-to-DB-commit p95 | `histogram_quantile(0.95, sum(rate(messaging_event_persist_lag_seconds_bucket{job="worker"}[1m])) by (le))` | Kafka append 전 API queued 시각부터 Worker DB commit 반환 직후까지 |
| PostgreSQL primary reachability | `messaging_postgres_is_primary{job="api"}` | 장애 주입이 DB path에 실제로 반영됐는지 확인 |
| DLQ events | `sum(increase(messaging_dlq_events_total[5m]))` | 짧은 장애 흡수 실험에서 DLQ가 0인지 확인 |
| Worker throughput | `sum(rate(messaging_worker_processed_total{job="worker"}[1m]))` | 복구 후 core backlog 처리 흐름 |

새 consumer group의 빈 partition은 kafka-exporter에서 `-1` lag로 보일 수 있습니다. partition별 값을 `0` 이상으로 정규화한 뒤 합산하며, 합계를 먼저 낸 뒤 한 번만 clamp하지 않습니다.

실험 결과 자체는 `ordering`, `no_loss`, `no_duplicate`, `no_mixed_payload`, `dlq_empty` checks로 판단합니다.
