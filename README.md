# Kafka 기반 고신뢰 이벤트 처리 시스템

Reliable Event Processing System

producer가 보낸 범용 event를 Kafka로 수락하고, Worker가 PostgreSQL에 저장하며, 실패 격리·재처리·알림 작업 분리·조회용 snapshot까지 이어가는 event-driven system입니다.

This repository implements a reliable event-processing boundary with Kafka append-first intake, asynchronous PostgreSQL persistence, per-stream ordering, bounded retries, DLQ replay, and operational evidence. The order lifecycle shown in the demo is a reference scenario built on the generic event contract.

## TL;DR

- Intake: API가 `message-ingress`에 append한 뒤 `202 Accepted` 반환
- Persistence: `message-worker` consumer group이 PostgreSQL HA에 비동기 저장
- Ordering: 같은 `stream_id`의 partition boundary와 Worker inline retry
- Failure: retry 한도 초과 event를 `message-ingress-dlq`에 격리
- Read model: DB commit 이후 compacted snapshot topic으로 materialized cache 갱신
- Notification: `message-notifications`와 별도 `notification-worker`
- Scaling: Worker consumer lag 기반 KEDA, API CPU 기반 HPA
- AWS: 로컬 검증 구조를 EKS, MSK, RDS PostgreSQL 등으로 옮기기 위한 migration blueprint
- Contract: `/v2/streams/{stream_id}/events`와 versioned `event_type` / `payload` / `metadata` envelope

핵심 구현과 운영 근거는 [Architecture](docs/ARCHITECTURE.md), [Validation Results](docs/TEST_RESULTS.md), [Runbook](docs/RUNBOOK.md)에서 확인할 수 있습니다. 다음 투자 순서와 완료 조건은 [Improvement Roadmap](docs/IMPROVEMENT_ROADMAP.md)에 정리했습니다.

## Problem

업무 시스템은 주문, 결제, 배포, IoT, 알림처럼 서로 다른 domain에서도 지속적으로 event를 만듭니다. PostgreSQL write 지연이나 짧은 장애를 API 응답 경로에 그대로 전파하면 수락 지연과 producer 재시도가 커질 수 있고, 같은 업무 stream의 event가 순서를 잃으면 downstream state가 뒤틀릴 수 있습니다.

이 포트폴리오는 다음 질문을 검증합니다.

- DB write path가 느리거나 잠시 중단돼도 event를 받아둘 수 있는가
- 같은 stream의 event가 실패 record를 추월하지 않는가
- 처리 실패를 격리하고 제한된 replay 경로로 복구할 수 있는가
- accepted, persisted, backlog, DLQ를 서로 다른 신호로 관측할 수 있는가

## Solution

- API: Kafka ingress append 성공 뒤 `202 Accepted`
- Worker: consumer group으로 처리하고 PostgreSQL에 최종 영속화
- Ordering: `stream_id` Kafka key와 partition-local inline retry
- Recovery: retry exhaustion 뒤 DLQ 격리, replay count guard와 수동 replay
- Read path: DB membership과 sequence watermark로 검증한 snapshot read, DB 장애 중 hydrated cache degraded fallback
- Operations: Prometheus, Grafana, readiness, GitOps, runbook

## 서비스 경계 / Service Boundary

이 저장소가 구현하는 경계:

- 범용 event intake와 request status
- `schema_version`, `event_type`, JSON `payload`, JSON `metadata` envelope
- Kafka partition 기반 stream 처리
- PostgreSQL 최종 영속화와 구조화 event envelope
- DB commit 이후 snapshot과 notification job 발행
- retry, DLQ 격리, replay guard
- 운영용 demo, metrics, alerts, runbook

참조 시나리오와 호환 경계:

- 주문·결제 lifecycle은 범용 contract를 설명하는 reference scenario
- `/v1/orders/{order_id}/events`는 기존 order client를 위한 compatibility adapter
- legacy body-only stream route와 `category` / `payment_id`는 과거 client·증거 호환용 alias
- checkout UI, 실제 payment 승인, 주문 transaction은 reference producer의 책임으로 가정

The public contract starts at event acceptance. `Kafka Appended` and `DB Persisted` remain separate so that asynchronous completion is visible. Order and payment fields in the reference demo do not constrain the generic processing model.

### Generic Event Contract

```http
POST /v2/streams/42/events
Authorization: Bearer <token>
X-Idempotency-Key: producer-event-42-7
Content-Type: application/json

{
  "event_type": "deployment.completed",
  "payload": {
    "service": "catalog-api",
    "revision": "a1b2c3d"
  },
  "metadata": {
    "environment": "staging",
    "producer": "release-controller"
  }
}
```

Kafka append 성공 응답은 `202 Accepted`이며 DB persistence 완료를 의미하지 않습니다. `GET /v2/event-requests/{request_id}`와 `GET /v2/streams/{stream_id}/events`에서 후속 상태와 저장 결과를 확인합니다. 인증·stream 생성은 공유 resource API인 `/v1/auth/login`, `/v1/streams`를 사용합니다.

Client request body는 `event_type`, `payload`, `metadata`로 구성되고 API가 accepted/Kafka envelope에 `schema_version=2`를 부여합니다. Append 직후 Worker가 request status row를 만들기 전에는 status GET이 잠시 `404`일 수 있으므로 동일 `request_id`로 retry합니다.

HTTP request body는 transport 단계에서 기본 `1 MiB`로 제한합니다. 그 안에서 generic `payload`는 최대 `65,536` UTF-8 JSON bytes, `metadata`는 최대 `16,384` bytes로 별도 검증합니다.

새 Worker는 legacy와 generic envelope를 모두 읽지만 구 Worker는 v2의 legacy body preview만 저장하므로 `payload`와 `metadata`를 보존하지 못합니다. 이 전환은 대칭 rolling compatibility가 아닙니다. GitOps는 gate `false`인 `messaging-env` Secret wave `-3` → 일반 Sync migration Job wave `-2` → Worker wave `-1` → API wave `0` 순서로 직렬화하며, `local-ha` overlay가 API container에만 gate `true`를 명시합니다. 수동 local manifest도 gate 기본값을 `false`로 두어 API startup migration 동안 v2 intake를 막고, quick start가 Worker rollout 뒤 API env를 `true`로 설정해 재기동합니다.

## Architecture Boundary

```text
Producer / reference scenario adapter
        |
        | POST event, 202 after Kafka append
        v
  message-ingress  -- message-worker --> PostgreSQL HA
        ^                 |                |
        | replay          | retry exhausted| after commit, best effort
        |                 v                v
  DLQ replayer <-- message-ingress-dlq   status / snapshots / notification job
```

이 프로젝트는 Kafka-centered 구조이며 PostgreSQL state/read model을 유지합니다.

Kafka append-first path는 request intake를 PostgreSQL write latency와 분리합니다. PostgreSQL state path는 Worker의 최종 persistence/idempotency와 API read model에 남아 있습니다.

- Kafka: intake, partition ordering, consumer processing, retry/DLQ/replay, lag-based scaling
- PostgreSQL: final durable state, stream sequence, idempotency/deduplication, query model
- API local materialized cache: Worker가 DB commit 뒤 발행한 snapshot만 원본으로 사용
- Compacted topics: `message-request-status`, `message-snapshots`, `stream-snapshots`
- Notification: core persistence transaction 뒤 Kafka job 발행과 `notification_attempts` 기록; 외부 채널 실제 발송은 현재 범위 제외, transactional outbox 미적용

각 API pod는 consumer group offset을 공유하지 않고 세 compacted topic을 beginning부터 독립적으로 replay합니다. Startup 시점에 캡처한 end offset까지 따라잡아 `hydrated`가 된 뒤에만 stream read cache를 사용합니다. PostgreSQL이 정상일 때는 DB membership authorization을 통과하고 cached page가 DB의 latest sequence watermark와 연속으로 일치해야 fresh snapshot을 반환합니다. DB 장애 중에는 이미 hydrated된 message/membership cache만 degraded fallback에 사용합니다.

Each API pod independently replays all snapshot partitions from the beginning and opens the cache read gate only after reaching its captured startup end offsets. Kafka exporter consumer-group lag does not describe this path; per-pod replay progress is a planned custom metric.

기존 배포와 저장 상태를 안전하게 이어가기 위해 `message-*` topic, `message-worker` consumer group, `messaging-app` namespace, `rooms`/`messages` table 같은 물리 식별자는 유지합니다. 범용 정체성은 공개 contract와 의미 모델에서 드러내며, 이름 변경만을 위한 migration은 수행하지 않습니다.

DB commit과 후속 Kafka publish 사이의 crash gap은 남아 있는 신뢰성 과제입니다. 재현 조건과 완료 기준은 [Improvement Roadmap](docs/IMPROVEMENT_ROADMAP.md)에 포함했습니다.

정상 event 흐름과 장애 / DLQ 흐름의 `sequenceDiagram`은 [Architecture](docs/ARCHITECTURE.md)에 있습니다. Inline retry와 DLQ terminal 처리가 같은 partition의 뒤 event에 미치는 영향까지 함께 설명합니다.

### Ordering Guarantee

- 같은 `stream_id`: 같은 Kafka partition으로 routing
- Worker: record key와 envelope의 `stream_id`가 다르거나 key가 유효한 UTF-8 정수가 아니면 non-replayable invalid DLQ로 격리
- transient failure: 해당 record를 같은 partition에서 inline retry
- terminal DLQ 처리 또는 성공 뒤에만 다음 offset 진행
- 서로 다른 partition 전체의 global ordering: 보장 범위 제외

### Idempotency Boundary

`X-Idempotency-Key`는 Kafka payload에 포함됩니다. API는 Kafka append 전에 PostgreSQL claim을 만들지 않으며, Worker persistence transaction의 actor-scoped PostgreSQL state가 최종 deduplication을 담당합니다. 이전 plain-route record는 같은 actor와 stream의 완전한 persisted response일 때만 scoped key로 승계합니다. 이 경계는 append-first intake를 지키지만 동일 key에 대한 즉시 동기 응답 재사용은 제공하지 않습니다.

## Demo

| Target | Observed / expected version | Contract state |
| --- | --- | --- |
| `master` worktree source | UI `2.0.0`, API `2.0.0` | generic v2 + `202` source contract |
| local `dev-kafka` live cluster, 2026-07-21 | API `2.0.0`, image `d31ac14` | generic v2 + `202`, Argo `Synced / Healthy` |
| public demo-lite deployment | UI `1.4.1`, API image `e481a21` | branch/deployment-specific, event response `200` |

문서에 등록된 외부 URL은 `demo-lite` deployment입니다. Generic v2는 local `dev-kafka` cluster에서 검증됐으며 public demo-lite에는 아직 반영되지 않았습니다. 접속 시 UI badge, readiness의 API version, event response status를 함께 확인합니다.

- Demo UI: [https://vm118.js-banjiha.cloud/demo/order-dashboard.html](https://vm118.js-banjiha.cloud/demo/order-dashboard.html)
- Swagger: [https://vm118.js-banjiha.cloud/docs](https://vm118.js-banjiha.cloud/docs)
- Readiness: [https://vm118.js-banjiha.cloud/health/ready](https://vm118.js-banjiha.cloud/health/ready)
- Grafana: [operations overview](https://vm118.js-banjiha.cloud/grafana/d/messaging-portfolio-overview/reliable-event-processing-operations-overview?orgId=1&refresh=5s)
- DLQ log sample: [summary endpoint](https://vm118.js-banjiha.cloud/v1/dlq/ingress/summary?limit=200&sample_limit=5)

`demo-lite`는 2코어급 서버에서 API → Kafka → Worker → DB 흐름을 보여주는 축소 profile입니다. HA, failover, full-system 성능 baseline의 증거로 사용하지 않습니다. 세부 제약은 [Demo Lite](docs/DEMO_LITE.md)에서 확인할 수 있습니다.

Master source `2.0.0` demo script (fresh install 또는 staged rollout 뒤):

1. Open the Demo UI and select `EN` if needed.
2. Add `10`, `100`, or `1000` sample events.
3. Send the order-lifecycle reference events through the generic stream API.
4. Watch `Reserved → Kafka Appended → DB Persisted`.
5. Check the Worker replica signal and Operations Advisor.

Master source version `2.0.0` behavior:

- generic `/v2/streams/{stream_id}/events`; client가 `event_type`/`payload`/`metadata`를 보내고 API가 `schema_version=2` 부여
- order lifecycle labeled as a reference scenario instead of the system identity
- operations auto-refresh: 30 seconds by default, optional 60 seconds
- in-memory auth token reuse
- one stream-level persistence summary poll every 3 seconds
- explicit `send_failed` and partially unconfirmed completion states
- stored envelope evidence for `schema_version`, `event_type`, `payload`, and `metadata`
- authenticated user-filtered DLQ recent log details and manual replay

`DLQ summary`는 append-only Kafka DLQ log의 최근 표본입니다. 현재 unresolved depth나 미해결 event의 SLO age를 뜻하지 않습니다.

- `by_reason`: 조회 sample의 실패 원인 분포
- `replayable` / `blocked`: sample 안의 재주입 가능 여부. `blocked`는 malformed/identity·counter 위반 또는 max replay 도달을 포함
- `oldest_sample_age_seconds`: sample에서 가장 오래된 record age

## Local Quick Start

Windows에서는 Docker Desktop만 설치하고 실행한 뒤 시작할 수 있습니다. `scripts/bootstrap_tools.ps1`가 저장소의 `tools/` 아래에 kind, kubectl, Helm helper를 준비합니다. Generic `2.0.0` quick start는 v2 gate가 닫힌 manifest를 적용하고 Worker 준비 뒤 API gate를 여는 순서를 자동으로 수행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1
```

Local endpoints:

- Demo UI: `http://localhost/demo/order-dashboard.html`
- Swagger: `http://localhost/docs`
- Readiness: `http://localhost/health/ready`
- Grafana: `http://localhost/grafana/d/messaging-portfolio-overview/reliable-event-processing-operations-overview?orgId=1&refresh=5s`
- Prometheus: `http://localhost/prometheus/`

현재 image 준비:

```powershell
docker build -t messaging-portfolio:local .
tools\kind.exe load docker-image messaging-portfolio:local --name messaging-ha
```

기존 cluster에 `2.0.0`을 반영할 때 API만 먼저 restart하지 않습니다. 수동 local 경로는 `k8s/app/manifests-ha.yaml`의 gate `false`와 quick-start Worker-first enable 절차를 사용합니다. GitOps 경로는 migration Job/Worker/API sync wave를 사용합니다. 각 경로의 canary 확인은 [Operations](docs/OPERATIONS.md)의 rollout boundary를 따릅니다.

상세 절차와 profile별 차이는 [Quick Start](docs/QUICK_START.md)와 [Demo Guide](docs/DEMO_GUIDE.md)에 있습니다.

## Validation Summary

현재 generic v2 후보 — 2026-07-21, stable 미승격:

| Item | Result |
| --- | ---: |
| Workload | 100 VU / 30s, 한 hot stream |
| Total HTTP / event `202` | `25,382` / `25,378` |
| Error rate | `0.00%` |
| Average | `67.83ms` |
| p95 / p99 | `123.96ms` / `153.10ms` |
| Same-stream ordering | `100/100 pass`, `7.93s` |
| Peak Worker lag / main drain | `24,504` / `751.76s` |

역사적 안정 Kafka intake 기준선:

| Item | Result |
| --- | ---: |
| Workload | 100 VU / 30s |
| Requests | `31,676` |
| Error rate | `0.00%` |
| Average | `44.13ms` |
| p95 / p99 | `80.65ms` / `103.57ms` |
| Same-stream ordering | `100/100 pass` |
| Row-visible latency proxy p95 | `7.67ms` |

해석 범위:

- generic v2: local `dev-kafka` image `d31ac14`에서 OpenAPI/API `2.0.0`, event `202`와 첫 성능 후보 확인
- stable 승격: 제외; fresh cluster clean state의 단일 hot-stream 실행이며 반복·multi-stream 검증 필요
- stable legacy 대비: total requests `19.87%` 감소, avg/p95 `53.70%`, p99 `47.82%` 증가
- 마지막 legacy raw 대비: total requests `8.68%` 감소, avg `17.68%`, p95 `3.92%`, p99 `1.66%` 증가
- 2026-06 성능 원본의 event status `200`: route에 HTTP `202` 계약을 명시하기 전 수집한 역사적 결과
- 현재 build의 `202 Accepted`: contract test와 2026-07-21 성능 실행에서 재확인
- `7.67ms`: API accepted 시각과 PostgreSQL row의 `created_at`/조회 가능 시점을 비교한 proxy
- 실제 DB commit timestamp: 위 수치에서 직접 측정하지 않음
- v2 status-observed sample: 50/50 persisted, avg `79.96ms`, p95 `81.28ms`, max `2384.10ms`; polling/network 포함, row-visible proxy와 비교 제외
- v2 Worker histogram query `60s`: 최대 finite bucket 경계 포화로 exact p95 해석 제외
- stable baseline: notification 분리 전 2차 Kafka intake 결과 유지
- last legacy raw suite: 2026-06-18 notification-path split 결과, `27,795` requests, p95 `119.28ms`, row-visible proxy p95 `22.13ms`, message-worker lag 약 16분 뒤 `0`

추가 검증:

- local unit / contract / infrastructure suite: `359 passed` (2026-07-21 현재 변경)
- ordering / failure injection 네 시나리오: missing `0`, duplicate `0`, mixed payload `0`, DLQ `0`
- hydrated cache fresh read: DB membership/watermark 확인 뒤 `source=cache`
- DB down stale fallback: initial hydration 완료 상태에서 `source=cache`, `degraded=true`, `snapshot_age_seconds`
- Worker KEDA 관찰: consumer lag peak, persistence proxy, backlog drain time
- fixed replica 대 KEDA: 동일 조건 직접 비교 결과 없음

Redis queue-first 수치는 과거 Redis scaling/tuning 문맥에만 사용합니다. Kafka baseline과 합치거나 같은 단계의 개선 수치로 표현하지 않습니다. 모든 조건과 역사 결과는 [Test Results](docs/TEST_RESULTS.md), 원본 관리 규칙은 [results/README.md](results/README.md)에 있습니다.

재현 환경은 AMD Ryzen 5 5600, Docker Desktop 12 CPU, 약 15.6GiB memory, kind single-node였습니다. 이보다 낮은 사양에서는 기능 결함 판정 전 resource contention과 restart를 먼저 확인합니다.

## Reliability Semantics

이 문서의 **고신뢰**는 현재 구현하고 검증한 경계를 가리킵니다.

- `stream_id` 단위 partition ordering과 inline retry
- record 처리 성공 또는 terminal DLQ 뒤 explicit offset commit
- PostgreSQL transaction과 idempotency state를 통한 중복 persistence 방어
- retry, DLQ 격리, replay guard, cache fallback
- accepted/persisted/lag/DLQ를 분리한 관측과 장애 주입 검증

exactly-once delivery, partition 간 global ordering, 모든 장애에서의 무손실, production SLA는 증명 범위에 포함하지 않습니다. DB commit 이후 best-effort Kafka publish gap과 unresolved DLQ state model은 남은 개선 과제입니다.

로컬 profile의 Kafka는 애플리케이션별 producer ACL을 증명하지 않습니다. Worker는 compacted topic의 key/payload identity와 owner/schema를 검증하지만, self-consistent forged snapshot을 막는 production 경계는 Kafka authentication과 topic별 least-privilege producer ACL입니다.

`GET /health/ready`의 상태 범위:

- `ready`: schema startup 완료, Kafka 연결, PostgreSQL primary와 HA guardrail 충족, non-local secret 안전
- `degraded`: Kafka intake 가능, PostgreSQL primary/standby/replication guardrail 일부 이탈
- `not_ready`: schema 미준비, Kafka 연결 불가, non-local 환경의 unsafe auth secret

PostgreSQL standby 수와 replication byte lag는 degraded reason에 반영됩니다. Broker replica 수, Worker replica/consumer lag, materialized cache 정보는 상태 결정과 분리해 Prometheus, alerts, status script에서 확인합니다. 자세한 의미는 [Reliability Policy](docs/RELIABILITY_POLICY.md)에 있습니다.

Readiness response의 `app_version`은 실행 중인 API build version을 제공합니다. 이번 `master` source의 예상 조합은 Demo UI `ver. 2.0.0 / api 2.0.0`입니다. 공개 demo-lite의 `ver. 1.4.1`은 별도 branch/image이므로 이 source 조합으로 해석하지 않습니다.

## Trade-offs

| Choice | Benefit | Cost / remaining risk |
| --- | --- | --- |
| Kafka append-first intake | DB 장애의 API 전파 완화 | accepted와 persisted 사이 eventual consistency |
| Inline retry | same-stream 순서 보존 | 뒤 event backpressure |
| PostgreSQL final state | durability와 query model 명확화 | DB write/lock capacity가 Worker 처리량 제한 |
| Post-commit best-effort publish | core transaction과 알림 장애 범위 분리 | commit 뒤 publish 누락 gap |
| DLQ append-only log | 실패 event 보존과 replay input | unresolved 상태는 별도 모델 필요 |
| Per-pod snapshot replay | pod마다 독립된 local read cache와 offset 소유권 충돌 제거 | unique request/message key 증가에 따라 cold-start replay가 계속 길어질 수 있음 |

Worker scaling 효과는 API request count보다 consumer lag, persistence latency, backlog drain time으로 평가합니다. 현재 병목 후보는 DB insert/commit, stream sequence lock, record processing, connection pool, partition imbalance입니다.

## What I Learned

- 빠른 `202`와 실제 처리 용량은 다른 문제이며 burst throughput만으로 지속 가능 용량을 설명할 수 없음
- Kafka ordering은 global guarantee가 아니라 key/partition과 consumer retry 경계의 조합
- DB commit 이후 Kafka publish도 별도 실패 경계를 가지며 transactional outbox가 없으면 누락 가능
- append-only DLQ log 표본과 현재 unresolved incident state는 다른 운영 모델
- 배포 증거에는 source commit, registry image, manifest tag, Argo CD revision이 함께 필요

## Current Bottleneck

- Worker DB write throughput과 PostgreSQL insert/commit latency
- `room_sequences` row lock과 hot stream contention
- record processing 및 DB connection pool
- partition 분산도와 consumer group rebalance
- 30초 burst 뒤 consumer lag drain에 약 10~16분이 걸린 최근 실험

병목 판정은 API p95 하나로 하지 않습니다. Worker commit-observed lag, consumer lag peak, drain time, DB stage latency를 함께 봅니다.

## Next Improvements

우선순위는 [Improvement Roadmap](docs/IMPROVEMENT_ROADMAP.md)의 완료 기준으로 관리합니다.

- generic v2 동일 조건 3회 반복과 multi-stream/partition 분산 부하로 sustainable capacity 확정
- object storage backup 자동화와 정기 restore drill; 2026-07-21 host logical dump의 disposable DB 복원은 통과
- Worker offset crash/rebalance 장애 주입 검증
- transactional outbox 또는 동등한 post-commit publish recovery
- v1 compatibility route 사용량 계측과 deprecation 종료 기준 정의
- 주문과 무관한 두 번째 reference producer로 generic envelope 재사용성 검증
- fixed Worker와 KEDA의 동일 조건 A/B 실험
- unresolved DLQ state model과 audit trail
- Worker commit-observed / client status-observed latency 재측정
- AWS MSK TLS/auth, RDS deletion/final snapshot, secret delivery hardening

## AWS Migration Blueprint

현재 AWS에 배포된 Terraform stack은 없습니다. `infra/terraform`은 로컬 구조를 AWS managed architecture로 이전하기 위한 skeleton입니다. SHA256을 검증한 Terraform `1.15.8`로 `fmt -recursive`, `init -backend=false`, `validate`까지 통과했으며, AWS credential과 비용이 필요한 `plan` / `apply`는 실행하지 않았습니다.

| Local | AWS target |
| --- | --- |
| kind | Amazon EKS |
| Kafka KRaft | Amazon MSK |
| PostgreSQL HA + Pgpool | RDS PostgreSQL Multi-AZ / Aurora PostgreSQL |
| local image | Amazon ECR image |
| ingress-nginx | AWS Load Balancer Controller + ALB |
| local TLS | ACM + Route 53 |
| runtime secret | AWS Secrets Manager |

The Terraform directory is a migration blueprint, not evidence of an AWS deployment. Validation and production-hardening gaps are documented explicitly in [AWS IaC Plan](docs/AWS_IAC_PLAN.md).

## Operations

로컬 상태 점검:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_portfolio_status.ps1 -SkipArgoCd
```

Argo CD bootstrap을 완료한 GitOps cluster에서는 `-SkipArgoCd`를 제거합니다. 장애 신호와 복구 순서는 [Observability](docs/OBSERVABILITY.md), [Metrics Reference](docs/METRICS_REFERENCE.md), [Runbook](docs/RUNBOOK.md), [Operations](docs/OPERATIONS.md)에 있습니다.

## Documentation Map

- [SERVICE_REQUIREMENTS.md](docs/SERVICE_REQUIREMENTS.md): 서비스 경계, 기능 요구, SLO guardrail
- [SERVICE_PROCESS_CHECKLIST.md](docs/SERVICE_PROCESS_CHECKLIST.md): 처음 실행부터 performance baseline까지의 전체 점검 순서
- [ARCHITECTURE.md](docs/ARCHITECTURE.md): Kafka-centered 구조, ordering, autoscaling
- [TEST_RESULTS.md](docs/TEST_RESULTS.md): 최신 검증 상태, 안정 baseline, historical evidence
- [QUICK_START.md](docs/QUICK_START.md): local / GitOps 실행 경로
- [DEMO_GUIDE.md](docs/DEMO_GUIDE.md): 화면 사용법과 시연 흐름
- [OPERATIONS.md](docs/OPERATIONS.md): secret, backup, 운영 작업
- [RUNBOOK.md](docs/RUNBOOK.md): incident response
- [OBSERVABILITY.md](docs/OBSERVABILITY.md): dashboard와 alert signal
- [RELIABILITY_POLICY.md](docs/RELIABILITY_POLICY.md): readiness 상태 의미
- [GITOPS.md](docs/GITOPS.md): image build부터 Argo CD sync까지의 배포 경계
- [AWS_IAC_PLAN.md](docs/AWS_IAC_PLAN.md): AWS migration blueprint와 현재 gap
- [IMPROVEMENT_ROADMAP.md](docs/IMPROVEMENT_ROADMAP.md): 우선순위와 측정 가능한 완료 기준
- [PATCH_NOTES.md](docs/PATCH_NOTES.md): 최신 변경 이력
- [REPOSITORY_STRUCTURE.md](docs/REPOSITORY_STRUCTURE.md): 저장소 지도
