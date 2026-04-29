# 테스트 결과

Kafka Event Stream Systems 포트폴리오의 로컬 Kubernetes/kind 검증 결과입니다.

테스트 결과는 [SERVICE_REQUIREMENTS.md](SERVICE_REQUIREMENTS.md)의 서비스 요구와 SLO guardrail을 기준으로 해석합니다. 즉 단순히 Pod가 떠 있는지가 아니라, request intake, stream ordering, Worker persistence, DLQ 격리, replay guard, GitOps 일관성, 운영 관측 신호가 실제 서비스 흐름을 설명할 수 있는지를 확인합니다.

## 전체 검증 요약

| 시나리오 | 결과 | 비고 |
| --- | --- | --- |
| 전체 quick start | 통과 | `scripts/quick_start_all.ps1` 정상 완료 |
| Kafka broker rollout | 통과 | `apache/kafka:3.7.0` 3-broker KRaft StatefulSet, `3/3` ready |
| Kafka topic bootstrap | 통과 | `message-ingress`, `message-ingress-dlq`, `message-request-status`, `message-snapshots`, `stream-snapshots`, partitions `8`, replication factor `3`, `min.insync.replicas=2`, snapshot topics `cleanup.policy=compact` |
| API Kafka intake | 통과 | API가 Kafka-only ingress path로 event를 수락 |
| Worker consumer group | 통과 | `message-worker`가 `message-ingress` partition을 소비하고 PostgreSQL에 영속화 |
| Smoke test | 통과 | event accepted -> Kafka ingress topic -> Worker -> PostgreSQL persisted |
| API contract test | 통과 | auth, stream membership, request status, unread count, DLQ summary 응답 계약 검증 |
| PostgreSQL 장애 테스트 | 통과 | DB primary 중단 중에도 API가 degraded mode로 ready를 유지하고 cached-stream event를 수락 |
| Kafka DLQ flow | 통과 | poison event가 `message-ingress-dlq`에 도달하고 DLQ API로 조회됨 |
| DB snapshot materialized cache | 통과 | `message-snapshots` / `stream-snapshots` compacted topic으로 DB commit 이후 snapshot을 local cache 원본으로 사용 |
| Cache-first message read | 통과 | `GET /streams/{stream_id}/events`가 `source`, `degraded`, `snapshot_age_seconds`, `items` 응답 계약을 사용. smoke test에서 `event_source=cache` 확인 |
| DLQ replay guard | 통과 | `replay_count >= max_replay_count` event는 `replayable=false`로 표시되고 자동 재주입 대상에서 제외 |
| HPA metrics test | 통과 | API HPA metric 유효 |
| Unit tests | 통과 | `.venv\Scripts\python.exe -m pytest -q`, `44 passed` |
| 같은 stream 순차 보증 | 통과 | 100개 순차 이벤트가 `stream_seq 1..100`으로 영속화 |
| Kafka 성능 suite | 통과 | 100 VU / 30s Kafka intake 기준선, `0.00%` error |

## 측정 / 재현 환경

이번 성능 기준선은 아래 로컬 환경에서 측정했습니다.

| 항목 | 값 |
| --- | --- |
| Host CPU | AMD Ryzen 5 5600, 6 cores / 12 threads, max 3.5GHz |
| Host memory | 약 32GiB |
| Docker Desktop 노출 사양 | 12 CPU, 약 15.6GiB memory |
| Docker Desktop runtime | Docker Desktop, `x86_64` |
| Kubernetes cluster | kind single-node |
| Kubernetes node | `messaging-ha-control-plane` |
| Kubernetes version | `v1.32.2` |
| Node OS / kernel | Debian 12, WSL2 kernel `6.6.87.2-microsoft-standard-WSL2` |
| Container runtime | `containerd://2.0.2` |
| Kubernetes allocatable | 12 CPU, `16338128Ki` memory |
| Pod resource requests | 5.1 CPU, `6768Mi` memory |
| Pod resource limits | 13.725 CPU, `14782Mi` memory |

재현 기준:

- 현재 100 VU / 30s 기준선은 12 threads / 16GiB 이상을 권장합니다.
- 권장 사양보다 낮은 환경에서는 전체 HA stack과 성능 기준선을 안정적으로 재현하기 어려울 수 있습니다.
- 낮은 사양의 환경에서는 Python unit test 또는 축소된 Kubernetes profile만 권장합니다.

낮은 사양에서의 실패 해석:

| 구간 | 흔한 실패 형태 | 해석 |
| --- | --- | --- |
| 설치 / rollout | `timed out waiting for the condition`, `CrashLoopBackOff`, `OOMKilled` | pod가 필요한 CPU/RAM을 제때 확보하지 못함 |
| readiness | `/health/ready` timeout, `degraded`, `not_ready` | Kafka / PostgreSQL / Pgpool이 준비되기 전에 timeout 도달 |
| Kafka intake | `503`, produce timeout | Kafka broker 응답 또는 ack 지연 |
| Worker 처리 | persisted timeout, consumer lag 증가 | Worker 처리량 또는 PostgreSQL persistence path 지연 |
| DLQ 검증 | `Poison event did not reach Kafka DLQ in time` | Worker가 제한 시간 안에 실패 event를 DLQ로 보내지 못함 |
| 성능 테스트 | error rate 증가, p95/p99 threshold 실패 | 처리량 한계 또는 resource contention |

따라서 낮은 사양에서 timeout / latency / restart 형태로 실패하는 경우, 기능 오류보다 리소스 부족 가능성을 먼저 확인합니다.

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
- idempotency-enabled write도 API가 Kafka append 전에 PostgreSQL state-store 작업을 수행하지 않습니다. Idempotency key는 event payload에 포함되고, 최종 deduplication은 Worker persistence 단계에서 수행합니다.

## 검증 명령

- `.venv\Scripts\python.exe -m pytest -q`
- `.venv\Scripts\python.exe -m compileall portfolio worker observer alembic`
- `kubectl apply --dry-run=client -f k8s\gitops\base\kafka-ha.yaml -o yaml`
- `kubectl kustomize k8s\gitops\overlays\local-ha`
- `powershell -ExecutionPolicy Bypass -File scripts\quick_start_all.ps1`
- `powershell -ExecutionPolicy Bypass -File scripts\test_api_contracts.ps1 -SkipReset`
- `powershell -ExecutionPolicy Bypass -File scripts\test_db_down.ps1 -SkipReset`
- `powershell -ExecutionPolicy Bypass -File scripts\test_dlq_flow.ps1 -SkipReset`
- `powershell -ExecutionPolicy Bypass -File scripts\test_dlq_replay_guard.ps1 -SkipReset`
- `powershell -ExecutionPolicy Bypass -File scripts\test_stream_ordering.ps1 -EventCount 100`
- `powershell -ExecutionPolicy Bypass -File scripts\test_hpa_scaling.ps1 -TimeoutSec 60`
- `powershell -ExecutionPolicy Bypass -File scripts\run_kafka_performance_suite.ps1`
- `git diff --check`

## 운영 메모

- 최신 출력은 `results/kafka-performance/latest.txt`에 저장됩니다.
- 초기 진단 실행에서는 `X-Idempotency-Key`를 켠 경우 API가 Kafka append 전에 PostgreSQL state-store 작업을 수행했고, 이 DB state path가 병목이 되어 `503` 응답과 Pgpool 재시작 압력이 함께 나타났습니다.
- 이후 event intake 본질을 지키기 위해 idempotency claim을 API hot path에서 제거했습니다. 현재 `X-Idempotency-Key`는 Kafka payload에 포함되고, Worker persistence 단계에서 최종 deduplication을 처리합니다.
- DB read fallback은 Kafka ingress event가 아니라 DB commit 이후 발행된 `message-snapshots` / `stream-snapshots` compacted topic만 사용합니다. 남은 보강 과제는 idempotency state를 Kafka compacted topic 또는 별도 state backend로 분리하는 것입니다.
- 2026-04-29 성능 튜닝 실험에서 producer linger / batch size와 Worker batch commit을 조정했지만, 100 VU / 30초 intake 기준선은 기존보다 낮아지고 p95/p99가 악화되어 채택하지 않았습니다. 대신 실험 중 발견한 stream 생성 직후 event append의 read-after-write 문제는 Pgpool primary routing hint로 보강했습니다.
- 장시간 500+ VU 테스트는 별도 capacity profile로 다룹니다.
## 운영 메트릭 변화 확인

Grafana / Prometheus 대시보드가 실제 운영 신호를 받는지 확인하기 위해 2026-04-29 로컬 kind 클러스터에서 smoke, ordering, DLQ flow를 실행했습니다.

| 측정 항목 | 결과 | 해석 |
| --- | ---: | --- |
| Prometheus scrape `api:8000` | `up = 1` | API metrics scrape 정상 |
| Prometheus scrape `worker:9101` | `up = 1` | Worker metrics scrape 정상 |
| Prometheus scrape `dlq-replayer:9102` | `up = 1` | DLQ replay metrics scrape 정상 |
| Prometheus scrape `kube-state-metrics:8080` | `up = 1` | restart / unavailable replica 지표 수집 정상 |
| API request counter | `3974 -> 4008` | 테스트 요청이 API counter에 반영됨 |
| Ordering test | 20 events, `stream_seq 1..20` | 같은 stream ordering boundary 검증 |
| DLQ metric | `messaging_dlq_events_total` 증가 | poison / gap event가 DLQ 지표에 반영됨 |
| Pod restart increase | `0` | 테스트 동안 workload restart 없음 |
| Unavailable replicas | `0` | 테스트 종료 시점 availability 정상 |

운영 알림 기준값도 같은 측정 체계에 연결했습니다. `MessagingApi5xxRateWarning`은 API 5xx ratio `> 1%`, `MessagingApiHigh5xxRate`는 `> 5%`, `MessagingEventPersistLagHigh`는 accepted-to-persisted p95 `> 5s`, `MessagingEventPersistLagCritical`은 `> 15s`, `MessagingQueueWaitHigh`는 Kafka topic wait p95 `> 10s`, `MessagingQueueWaitCritical`은 `> 30s`, `MessagingDlqReplayBlocked`는 `skipped_max_replay` 누적값 `> 0`을 기준으로 합니다. DLQ age는 Prometheus counter가 아니라 DLQ summary API의 `oldest_age_seconds`로 확인하며 warning `> 600`, critical `> 1800`을 운영 판단 기준으로 둡니다.
## 운영 Alert Probe 결과

`test_operational_alerts.ps1`는 Prometheus rule 로딩과 실제 alert firing을 검증하기 위해 추가했습니다. 2026-04-29 실행 결과 DLQ 증가와 replay blocked는 `firing`, unavailable replica는 `pending` 상태까지 확인했습니다.

| Scenario | Expected alert | 측정 방식 |
| --- | --- | --- |
| DLQ event 증가 | `MessagingDlqEventsIncreasing` | `test_dlq_flow.ps1` 실행 후 Prometheus `/api/v1/rules`에서 firing 확인 |
| DLQ replay guard 차단 | `MessagingDlqReplayBlocked` | `test_dlq_replay_guard.ps1` 실행 후 `skipped_max_replay` 누적값 기반 alert firing 확인 |
| unavailable replica | `MessagingDeploymentUnavailableReplicas` | `dlq-replayer`에 잘못된 image rollout을 만들고 kube-state-metrics unavailable replica 기반 alert 상태 확인 |

실행 명령:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_operational_alerts.ps1 -SkipReset
```
## DLQ Summary API 계약

API contract test는 `GET /v1/dlq/ingress/summary` 응답 계약도 확인합니다.

| Field | 검증 |
| --- | --- |
| `queue_backend` | `kafka` |
| `topic` | Kafka DLQ topic 이름 존재 |
| `total` | 0 이상 숫자 |
| `replayable` | 0 이상 숫자 |
| `blocked` | 0 이상 숫자 |
| `oldest_age_seconds` | 필드 존재 |
| `by_reason` | reason별 count map 존재 |
| `by_stream` | stream별 count list 존재 |
| `recent_samples` | 최근 DLQ event 샘플 list 존재 |
## Response Model / Incident Signal 계약

핵심 운영 API는 FastAPI `response_model`로 응답 형태를 고정했습니다.

| API | Response model |
| --- | --- |
| `GET /health/ready` | `ReadinessResponse` |
| `GET /v1/event-requests/{request_id}` | `EventRequestStatusResponse` |
| `GET /v1/dlq/ingress` | `DlqListResponse` |
| `GET /v1/dlq/ingress/summary` | `DlqSummaryResponse` |

장애 신호 wrapper는 아래 명령으로 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_incident_signals.ps1 -SkipDbOutage
```

이 suite는 DB outage, DLQ alert probe, Worker bad rollout의 운영 신호를 묶어서 확인하도록 구성했습니다.
## OpenAPI 계약 검증

OpenAPI는 ChatGPT API가 아니라 이 서비스의 공용 API 사용 설명서입니다. FastAPI가 route와 `response_model`을 읽어 `/openapi.json`을 생성하고, `/docs`에서 사람이 확인할 수 있게 보여줍니다.

테스트는 아래 항목을 검증합니다.

| Endpoint | OpenAPI response schema |
| --- | --- |
| `GET /health/ready` | `ReadinessResponse` |
| `GET /v1/event-requests/{request_id}` | `EventRequestStatusResponse` |
| `GET /v1/dlq/ingress` | `DlqListResponse` |
| `GET /v1/dlq/ingress/summary` | `DlqSummaryResponse` |

특히 `DlqSummaryResponse`는 `total`, `replayable`, `blocked`, `oldest_age_seconds`, `by_reason`, `by_stream`, `recent_samples`를 OpenAPI schema에 노출하는지 확인합니다.

`oldest_age_seconds`는 DLQ event가 오래 방치되는지를 보는 운영 신호입니다. 10분 초과는 warning, 30분 초과는 critical 기준으로 보고, `blocked`, `by_reason`, `recent_samples`를 함께 확인합니다.
## Kafka Exporter 관측성

Kafka broker/topic/consumer group 상태를 직접 보기 위해 kafka-exporter를 추가했습니다.

| 항목 | 검증 |
| --- | --- |
| exporter deployment | `kafka-exporter` Deployment / Service 추가 |
| Prometheus scrape | `kafka-exporter:9308` scrape target 추가 |
| broker count | `kafka_brokers` 패널 추가 |
| consumer lag | `kafka_consumergroup_lag{consumergroup="message-worker"}` 패널 추가 |
| topic partitions | `kafka_topic_partition_current_offset` 기반 partition 패널 추가 |
| alert | `MessagingKafkaExporterDown`, `MessagingKafkaConsumerLagHigh` 추가 |
실제 로컬 클러스터 확인값:

| Query | Result |
| --- | --- |
| `up{job="kafka-exporter"}` | `1` |
| `kafka_brokers` | `3` |
| `sum(kafka_consumergroup_lag{consumergroup="message-worker"})` | `0` |
| Grafana panels | `Kafka Broker Count`, `Kafka Consumer Group Lag`, `Kafka Topic Partitions` 반영 확인 |

## GitOps / Argo CD 확인

2026-04-29 로컬 `kind` 클러스터에서 Argo CD 설치와 GitOps sync를 다시 확인했습니다.

| 항목 | 결과 |
| --- | --- |
| Argo CD 설치 | `argocd` namespace 생성, controller / server / repo-server / dex / notifications / applicationset 모두 `1/1 Running` |
| Application | `messaging-portfolio-local-ha` 생성 |
| Source | `https://github.com/Jangwanko/Cloud_portfolio.git`, revision `dev-kafka`, path `k8s/gitops/overlays/local-ha` |
| Sync / Health | `Synced / Healthy` |
| GitOps render | `kubectl kustomize k8s\gitops\overlays\local-ha` 통과 |
| Workload readiness | `api`, `worker`, `kafka`, PostgreSQL, Pgpool, `kafka-exporter`, Prometheus, Grafana ready |
| 보강한 drift 처리 | HPA가 관리하는 Deployment `/spec/replicas` 차이는 `ignoreDifferences`로 제외 |
| 보강한 health 처리 | `postgres-backups` PVC의 `WaitForFirstConsumer` 대기는 local-path backup PVC의 정상 상태로 해석 |

설치 스크립트는 로컬 proxy 환경값이 원격 Argo CD manifest 다운로드를 방해하지 않도록 proxy 값을 비우고, CRD annotation 크기 제한을 피하기 위해 server-side apply를 사용합니다.

## Portfolio Status Check

운영형 포트폴리오 데모 직전에 현재 클러스터 상태를 한 번에 확인하기 위해 `scripts/check_portfolio_status.ps1`를 추가했습니다.

| 확인 항목 | 2026-04-29 결과 |
| --- | --- |
| API readiness | `ready` |
| Argo CD Application | `Synced / Healthy`, revision `ecf8f2f70cfc3778ff56d2e4957f3395f04c76ee` |
| Core workloads | API `3/3`, Worker `2/2`, Kafka `3/3`, PostgreSQL `3/3`, Pgpool `2/2` |
| KEDA | `worker-keda` Ready |
| Prometheus scrape | `api`, `worker`, `dlq-replayer`, `kafka-exporter`, `kube-state-metrics` 모두 `up=1` |
| Kafka exporter | `kafka_brokers=3`, `message-worker consumer_lag=0` |
| Backup PVC | `postgres-backups`는 첫 backup CronJob consumer 전까지 `Pending`; local-path `WaitForFirstConsumer`의 정상 warning으로 처리 |

문서화:
- 처음 실행자는 [QUICK_START.md](QUICK_START.md)로 클러스터를 먼저 구성합니다.
- 전체 서비스 프로세스 점검은 [SERVICE_PROCESS_CHECKLIST.md](SERVICE_PROCESS_CHECKLIST.md)의 `처음 실행하는 경우`, `정상 출력 예시`, `이상 신호를 읽는 법`을 따라갑니다.
