# 이벤트 처리 워크로드를 위한 Kubernetes·GitOps 운영 플랫폼

Kubernetes Operations Platform for a Reliable Event Processing Workload

직접 만든 **Kafka 기반 고신뢰 이벤트 처리 시스템**을 검증 대상 workload로 사용해 배포, lag 기반 확장, 관측, 장애 격리, 복구, 백업·복원을 검증한 DevOps·플랫폼 엔지니어링 프로젝트입니다. API 기능보다 운영 중 어떤 신호를 보고 어떻게 판단했는지를 중심으로 설명합니다.

This project operates a reliable event-processing workload on Kubernetes and validates GitOps delivery, lag-based scaling, observability, failure recovery, and restore procedures. The order lifecycle shown in the demo is a reference scenario built on the generic event contract.

## 핵심 요약 / Executive Summary

| 관점 | 핵심 내용 |
| --- | --- |
| 운영 문제 | DB 장애가 API로 전파되고, 유입이 처리 용량을 넘으면 Kafka backlog가 증가하며, 잘못된 rollout 순서가 schema 호환성을 깨뜨림 |
| 검증 workload | `API → Kafka → Worker → PostgreSQL`; Kafka append 뒤 `202`, DB persistence는 비동기 처리 |
| 플랫폼 대응 | Kubernetes, API HPA, Kafka lag 기반 Worker KEDA, Prometheus/Grafana, Argo CD staged rollout, DLQ/replay, backup/restore |
| 판단 방식 | API latency와 처리 용량을 분리하고 consumer lag, replica, persistence 관측값, drain time, 최종 저장 결과를 함께 확인 |
| 현재 경계 | local single-node kind에서 검증. AWS는 Terraform migration blueprint이며 실제 배포 증거에서 제외 |

```text
Git push → CI validation → verified image → GitOps tag → Argo CD → Kubernetes
                                                                  │
Producer → API ──202 after Kafka append──> Kafka ──Worker──> PostgreSQL
                                               │             │
                                               └─ lag/KEDA    └─ snapshot/backup
Observability ← API latency · consumer lag · replicas · persistence · drain · restore
```

## 운영 지표를 판단으로 연결한 방법 / Operational Reasoning

| 관측 신호 | 의미 | 이 신호만으로 말할 수 없는 것 | 판단과 조치 |
| --- | --- | --- | --- |
| API `202`·p95 | Kafka까지의 수락 성능 | PostgreSQL 저장 완료와 지속 가능 처리량 | persistence status와 consumer lag를 별도로 확인 |
| message-worker consumer lag | 수락 속도와 DB 처리 속도의 차이 | 원인이 CPU라는 결론 | DB commit, stream lock, partition 분산, connection pool을 함께 점검 |
| Worker replica | KEDA가 확장 결정을 실행한 결과 | 확장으로 전체 성능이 좋아졌다는 증거 | fixed/KEDA의 peak lag와 drain time을 같은 조건에서 비교 |
| status/commit 관측 지연 | client 또는 Worker가 저장을 확인한 시점 | DB commit 자체와 동일한 timestamp | row-visible, status-observed, commit-observed 정의를 분리 |
| backlog drain time | burst 이후 처리 경로가 정상 상태로 복귀하는 데 걸린 시간 | API intake 수치의 대체값 | 최종 message/notification lag `0`과 함께 복구 완료 판정 |
| Argo `Synced / Healthy` | desired state와 controller health | 새 application image가 실제 실행 중이라는 증거 | source commit, registry digest/tag, overlay revision, workload image 확인 |
| backup file·Job 완료 | 복구 지점 생성 시도 | 데이터 복원 성공 | disposable DB restore와 table/version/max sequence 일치 확인 |

### 대표 판단 사례

- **KEDA A/B**: Worker `2→8`, all-pipeline drain `301.42초→261.17초` 확인. 같은 실행에서 event 수 `7.35%` 감소, p95 `25.62%` 증가, notification peak lag `11,536` 발생. 확장 동작과 병목 이동의 증거로 채택하고 안정 성능 개선 판정은 보류
- **DB 장애 복구**: 장애 중 Kafka append와 `202` 수락 확인. pod 복귀나 readiness만 사용하지 않고 PostgreSQL 최종 저장과 consumer lag `0`까지 확인
- **GitOps와 복구**: `Synced / Healthy`와 workload image revision을 분리. backup 생성과 restore 성공도 별도 증거로 관리

### 현재 검증 수치 / Current Evidence

| Evidence | Workload | Event/requests | p95 | Interpretation |
| --- | --- | ---: | ---: | --- |
| Current generic v2 recovery candidate | 100 VU / 30s, single hot stream, 3회 평균 | `29,168` event `202` | `101.27ms` | ordering·hot-partition 경계, stable 미승격 |
| Multi-stream Worker A/B candidate | 100 VU / 30s, 64 streams | fixed `22,125` / KEDA `20,499` event `202` | fixed `169.24ms` / KEDA `212.60ms` | KEDA drain 개선과 intake 악화 동시 확인 |
| Historical Kafka intake baseline | 100 VU / 30s, legacy request contract | `31,676` requests | `80.65ms` | historical evidence, current v2 수치로 재표현 제외 |

Current v2는 첫 v2 후보보다 event 수 `14.93%` 증가, p95 `18.30%` 감소를 3회 반복에서 확인했습니다. Historical baseline보다 event 수 `7.92%` 낮고 p95 `25.57%` 높습니다. 계약과 실행 조건이 달라 직접적인 세대 간 성능 결론에서 제외합니다. 세부 조건과 원본은 [Validation Results](docs/TEST_RESULTS.md)와 [results evidence guide](results/README.md)에 있습니다.

## Demo

| Target | Observed / expected version | Contract state |
| --- | --- | --- |
| `master` worktree source | UI `2.0.0`, API `2.0.0` | generic v2 + `202` source contract |
| local `dev-kafka` live cluster, 2026-07-21 | API `2.0.0`, image `9349ba9`, image-tag revision `b84c379` | generic v2 + `202`, cache `ready=true` / `hydrated=true`, Argo `Synced / Healthy` |
| public demo-lite deployment | `Post-Order Event Console`, UI `1.4.1`, API `1.0.0`, image `e481a21` | branch/deployment-specific, generic v2 없음, event response `200` |

현재 generic v2 시연 경로는 아래 Local Quick Start입니다. Public URL은 아직 legacy `demo-lite` deployment이며 current v2 데모 링크로 사용하지 않습니다.

Master source `2.0.0` 시연 흐름(fresh install 또는 staged rollout 뒤):

1. Open the Demo UI and select `EN` if needed.
2. Add `10`, `100`, or `1000` sample events.
3. Send the order-lifecycle reference events through the generic stream API.
4. Watch `Reserved → Kafka Appended → DB Persisted`.
5. Compare API acceptance, DB persistence, Worker replica, DLQ, and Operations Advisor signals.

화면은 Kafka append와 DB persistence를 별도 counter로 표시하고, partially unconfirmed 상태를 완료로 처리하지 않습니다. `DLQ summary`의 `by_reason`, `replayable`, `blocked`, `oldest_sample_age_seconds`는 append-only log의 최근 표본이며 unresolved depth나 미해결 event SLO가 아닙니다. 세부 화면 동작은 [Demo Guide](docs/DEMO_GUIDE.md)에서 설명합니다.

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

### Public legacy demo-lite

아래 링크는 2코어급 서버의 legacy compatibility deployment입니다. UI `1.4.1`, API `1.0.0`, event `200` 상태이며 README의 generic v2 source와 다릅니다.

- [Demo UI](https://vm118.js-banjiha.cloud/demo/order-dashboard.html)
- [Swagger](https://vm118.js-banjiha.cloud/docs)
- [Readiness](https://vm118.js-banjiha.cloud/health/ready)
- [Grafana](https://vm118.js-banjiha.cloud/grafana/d/messaging-portfolio-overview/reliable-event-processing-operations-overview?orgId=1&refresh=5s)

제약과 배포 경계는 [Demo Lite](docs/DEMO_LITE.md)에 있습니다. Public v2 동기화 전까지 이 링크는 현재 기능 검증의 대표 진입점에서 제외합니다.

## Validation Summary

- generic v2 hot-stream clean run 3회: event `202` 평균 `29,168`, error `0.00%`, p95 `101.27ms`, p99 `140.59ms`
- same-stream ordering: `100/100`; failure injection 네 시나리오 missing/duplicate/mixed payload/DLQ `0`
- DB 장애 중 `202` 수락, 복구 뒤 persistence와 consumer lag `0` 확인
- cache fallback: DB membership/watermark 검증 뒤 fresh cache, DB 장애 중 hydrated degraded cache 확인
- 테스트: local unit/contract/infrastructure `363 passed`

hot single-stream은 ordering·lock·partition 병목 증거로 사용합니다. 64-stream fixed/KEDA A/B에서 KEDA `2→8`, 전체 pipeline drain `301.42초→261.17초`를 확인했습니다. KEDA arm은 event 수 `7.35%` 감소와 p95 `25.62%` 증가가 함께 발생해 stable 성능 개선 판정에서 제외합니다. 측정 조건, historical 결과, proxy 정의는 [Validation Results](docs/TEST_RESULTS.md)에 있습니다.

## Generic Event Contract

플랫폼 검증에 사용한 workload는 versioned generic event를 Kafka에 append하고 Worker가 PostgreSQL에 저장합니다.

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

Kafka append 성공 응답은 `202 Accepted`이며 DB persistence 완료를 의미하지 않습니다. 후속 상태와 저장 결과는 `GET /v2/event-requests/{request_id}`와 `GET /v2/streams/{stream_id}/events`로 확인합니다. API가 `schema_version=2`를 부여하며 ordering, idempotency, migration, cache 세부 계약은 [Architecture](docs/ARCHITECTURE.md)와 [Service Requirements](docs/SERVICE_REQUIREMENTS.md)에 유지합니다.

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
- Worker scale-out 때 단일 notification-worker로 이동하는 backlog
- 최신 hot-stream 30초 burst의 main lag drain 평균 `508.58초`; historical suite 범위 약 `10~16분`

병목 판정은 API p95 하나로 하지 않습니다. Worker commit-observed lag, consumer lag peak, drain time, DB stage latency를 함께 봅니다.

## Next Improvements

우선순위는 [Improvement Roadmap](docs/IMPROVEMENT_ROADMAP.md)의 완료 기준으로 관리합니다.

- 64-stream fixed/KEDA A/B 3회 반복, notification-worker capacity와 single-node resource contention 분리
- CI-validated image promotion, rollback, public demo staged rollout의 실제 배포 증거 확보
- Worker crash/rebalance와 partition offset recovery 장애 주입
- object storage backup, cluster-loss restore drill, RPO/RTO 기록
- AWS network·IAM·secret·managed database를 포함한 실제 Terraform plan/apply 경로 검증

Transactional outbox, accepted-state read model, unresolved DLQ state는 [Improvement Roadmap](docs/IMPROVEMENT_ROADMAP.md)에 workload 신뢰성 gap으로 유지합니다. 새로운 API·domain 기능 확장보다 배포, 운영, 복구, cloud evidence를 우선합니다.

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
