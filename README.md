# 주문 이후 이벤트 처리 시스템 포트폴리오

Post-Order Event Pipeline Portfolio

쇼핑몰에서 결제와 주문 완료 이후 발생하는 이벤트를 Kafka로 받아 저장, 분류, 알림, 장애 격리, 재처리까지 처리하는 event-driven order pipeline입니다.

사용자는 결제 완료와 주문 완료 응답을 빠르게 확인합니다. 이후 주문 이벤트의 영속화, 운영 분류, 알림 발행, 실패 격리, backlog drain은 내부 Kafka / Worker 경로에서 처리합니다.

This project demonstrates a Kafka-centered post-order event pipeline. The customer-facing path returns payment/order completion quickly, while persistence, classification, notification, DLQ isolation, replay, and backlog drain are handled through the internal Kafka / Worker path.

## TL;DR

- API는 주문 이후 이벤트를 DB에 직접 쓰지 않고 Kafka에 append한 뒤 `202 Accepted`를 반환합니다.
- Worker consumer group이 Kafka partition을 consume하고 PostgreSQL HA에 비동기로 persistence합니다.
- 실패 event는 inline retry 후 Kafka DLQ topic으로 격리하고, DLQ Replayer가 복구 가능한 event를 replay합니다.
- KEDA는 CPU가 아니라 Kafka consumer lag 기준으로 Worker를 scale-out합니다.
- API read path는 DB commit 이후 publish된 `message-snapshots`, `stream-snapshots`를 API local materialized cache로 소비해 cache-first read를 제공합니다.
- 로컬에서 검증한 구조를 Terraform 기반 AWS migration blueprint로 정리해 EKS, MSK, RDS PostgreSQL, ALB, ACM, Secrets Manager로 이전 가능한 구조를 보여줍니다.

자세한 서비스 기준은 [SERVICE_REQUIREMENTS.md](docs/SERVICE_REQUIREMENTS.md), 구조는 [ARCHITECTURE.md](docs/ARCHITECTURE.md), 최신 검증 결과는 [TEST_RESULTS.md](docs/TEST_RESULTS.md)에 정리했습니다.

## Full System vs Demo Lite

이 포트폴리오는 본래 검증용 시스템과 저사양 공개 데모를 나누어 설명합니다.

| Mode | Purpose | How to read it |
| --- | --- | --- |
| Full system / 본래 시스템 | Kafka 3 broker, PostgreSQL HA, Pgpool, KEDA scale-out, Grafana, DLQ replay까지 포함한 검증 기준입니다. | HA, ordering, failure recovery, performance baseline은 이 기준으로 설명합니다. |
| Demo lite / 저사양 데모 | 2코어급 서버에서 API -> Kafka -> Worker -> DB 흐름을 직접 보여주는 축소 실행 모드입니다. | 성능 증명보다 실제 서비스 흐름과 운영 증거를 보여주는 데 집중합니다. |

`demo-lite`는 full HA를 흉내 내기 위한 구성이 아니라, 제한된 서버에서도 같은 이벤트 처리 개념을 실행해 볼 수 있게 만든 profile입니다.

`demo-lite` is not the HA or performance proof. It is a constrained runtime that makes the same event-driven flow visible on a small server.

## Local Demo

브라우저 데모는 로컬 Kubernetes 환경에서 실제 Kafka / Worker / PostgreSQL 경로로 처리되는 모습을 보여줍니다.

The browser demo shows real local processing through API intake, Kafka append, Worker persistence, and PostgreSQL storage.

- Demo UI: `http://localhost/demo/order-dashboard.html`
- GitHub: `https://github.com/Jangwanko/Cloud_portfolio`
- API docs: `http://localhost/docs`
- Grafana: `http://localhost/grafana/d/messaging-portfolio-overview/messaging-portfolio-operations-overview?orgId=1&refresh=5s`
- Readiness: `http://localhost/health/ready`

처음 실행할 때는 Docker Desktop을 켠 뒤 아래 명령을 실행합니다. Windows 기준으로는 Docker Desktop만 설치하고 실행되어 있으면 `scripts/bootstrap_tools.ps1`이 `tools/kind.exe`, `tools/kubectl.exe`, `tools/helm/windows-amd64/helm.exe`를 준비합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1
```

2코어 2스레드급 서버에서는 full HA 구성을 그대로 올리기보다 `demo-lite` 프로파일을 사용합니다. 이 모드는 Kafka / PostgreSQL HA를 축소하고 API -> Kafka -> Worker -> DB 흐름 시연에 집중합니다.

On a 2-core demo host, use the `demo-lite` profile instead of the full HA profile. It reduces Kafka and PostgreSQL HA capacity while keeping the visible API -> Kafka -> Worker -> DB flow.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_lite.ps1
```

서버에 실제 배포할 때는 k3s 기준 스크립트를 사용합니다.

For a real 2-core Linux server deployment, use the k3s deployment script.

```bash
HOST_NAME=your.domain.example BASE_URL=http://your.domain.example bash scripts/deploy_lite_k3s.sh
```

이미 로컬 `kind` 클러스터가 있고 화면 변경만 반영하려면 아래 순서로 갱신합니다.

```powershell
docker build -t messaging-portfolio:local .
tools\kind.exe load docker-image messaging-portfolio:local --name messaging-ha
kubectl rollout restart deployment/api -n messaging-app
kubectl rollout status deployment/api -n messaging-app --timeout=180s
```

데모 화면에서는 `샘플 10개/100개/1000개 추가`로 예약 큐를 만들고 `결제 완료 / 주문 완료 이벤트 보내기`를 누릅니다. 화면은 예약 건수, Kafka 적재, DB 저장, 총 소요시간, Worker 현재/최대 replica를 분리해서 보여줍니다.

In the demo UI, add 10, 100, or 1000 reserved sample events, then send the post-order event batch. The screen separates reserved events, Kafka appended events, DB persisted events, total elapsed time, and current/max Worker replicas.

`예약 건수`는 전송 시작 후 `남은 예약/전체 예약` 형식으로 보이며, Kafka append가 성공한 시점에 줄어듭니다. `DB 저장`은 Worker가 PostgreSQL에 persistence한 수를 따로 보여줍니다.

`Reserved` is shown as `remaining/total` after the send run starts. It decreases when the API appends an event to Kafka. `DB Persisted` is counted separately after Worker persistence succeeds.

The demo also includes a lightweight rule-based Operations Advisor. It does not call an AI API. Instead, it interprets queue, Kafka append, DB persistence, and DLQ signals with deterministic rules. A future AI worker can consume the same advisor signals and produce richer operator-facing summaries outside the core persistence path.

English demo script:

1. Start Docker Desktop and run `scripts/quick_start_all.ps1`.
2. Open `http://localhost/demo/order-dashboard.html`.
3. Click `EN` if you want to run the demo in English.
4. Click `Add 10 Samples`, `Add 100 Samples`, or `Add 1000 Samples` to reserve post-order events.
5. Click `Send Post-Order Events` and watch the counters move from Reserved to Kafka Appended to DB Persisted.
6. Use Swagger, Grafana, Readiness, DLQ summary, and Reset Demo DB from the operations panel when you want to show supporting evidence.

실행 세부 절차는 [QUICK_START.md](docs/QUICK_START.md), 데모 시연 순서는 [DEMO_GUIDE.md](docs/DEMO_GUIDE.md), 데모 운영 작업은 [OPERATIONS.md](docs/OPERATIONS.md)를 참고합니다.

## What This Proves

이 프로젝트는 주문 완료 이후에도 계속 발생하는 결제 승인, 주문 생성, 배송 시작, 환불 요청, 알림 발행 같은 이벤트를 운영 가능한 pipeline으로 처리하는 것을 목표로 합니다.

- DB 장애가 API intake 실패로 바로 전파되지 않도록 Kafka append-first 경계를 둡니다.
- 같은 `stream_id`는 Kafka partition ordering boundary와 Worker inline retry로 순서를 지킵니다.
- DLQ summary는 `/v1/dlq/ingress/summary`에서 `by_reason`, `replayable`, `blocked`를 기준으로 운영자가 먼저 판단할 수 있게 합니다.
- `check_portfolio_status.ps1`로 readiness, Argo CD `Synced / Healthy`, kafka-exporter lag, KEDA, backup PVC 상태를 한 번에 확인합니다.
- 정상 event 흐름과 장애 / DLQ 흐름의 상세 `sequenceDiagram`은 [ARCHITECTURE.md](docs/ARCHITECTURE.md)에 둡니다.

## Problem

주문 완료 이후 이벤트를 DB 동기 write 중심으로 처리하면 write 병목, timeout, 장애 전파, 실패 event 유실 위험이 커집니다. 사용자에게 보여줄 완료 응답과 내부 운영 처리 상태가 섞이면 서비스 경계도 흐려집니다.

상세한 사용자 관점, 기능 요구, 비기능 요구, SLO guardrail은 [SERVICE_REQUIREMENTS.md](docs/SERVICE_REQUIREMENTS.md)에 정리했습니다.

## Solution

Kafka 기반 event log를 주문 이후 이벤트 intake 경로에 두고, persistence / 분류 / 알림 / 재처리를 Worker 경로로 분리했습니다.

- API는 Kafka ingress topic에 append하고 빠르게 응답합니다.
- Worker는 PostgreSQL HA에 최종 영속화하고 request status를 갱신합니다.
- DB commit 이후 snapshot을 Kafka compacted topic에 publish해 cache-first read 원본으로 사용합니다.
- 알림은 `message-notifications` topic과 별도 `notification-worker`로 분리합니다.
- 실패 event는 retry / DLQ / replay guard를 거쳐 복구 가능한 상태로 남깁니다.

구현 세부와 처리 흐름은 [ARCHITECTURE.md](docs/ARCHITECTURE.md), 장애 대응은 [RUNBOOK.md](docs/RUNBOOK.md)에 정리했습니다.

## Architecture Boundary

이 프로젝트는 Kafka-only 구조가 아니라 Kafka-centered 구조입니다.

Kafka 중심:
- event intake
- stream ordering boundary
- Worker consumer group processing
- retry / DLQ / replay
- lag based autoscaling
- DB snapshot compacted topics / local materialized cache

PostgreSQL state path:
- auth / membership
- final message persistence
- stream sequence
- read model

`X-Idempotency-Key`는 Kafka payload에 포함되고, 최종 deduplication은 Worker persistence 단계에서 처리합니다. API event intake 기준선은 Kafka append 전에 PostgreSQL claim을 만들지 않는 경로입니다. DB read fallback은 Kafka ingress event가 아니라 Worker가 PostgreSQL commit 이후 publish한 DB snapshot을 기준으로 합니다.

## Intake Boundary: Idempotency State Path

현재 event intake 기준선은 PostgreSQL 선조회 없이 Kafka append를 먼저 수행하는 경로입니다. `X-Idempotency-Key`는 Worker persistence 단계의 최종 deduplication에 사용하고, 남은 개선 과제는 idempotency state까지 Kafka compacted topic 또는 별도 state backend로 분리하는 것입니다.

## Validation Summary

대표 Kafka baseline:

| 항목 | 결과 |
| --- | ---: |
| Kafka intake load | 100 VU / 30s |
| Requests | `31,676` |
| Error rate | `0.00%` |
| Avg latency | `44.13ms` |
| p95 latency | `80.65ms` |
| p99 latency | `103.57ms` |
| Same-stream ordering | `100/100 pass` |
| Accepted-to-persisted latest p95 | `7.67ms` |

최근 운영 경계 검증:
- 2026-06-09 재실행: `34,284` requests, error `0.00%`, Worker lag `36394 -> 33274 -> 23563 -> 11971 -> 0`
- Post-tuning suite: accepted-to-persisted p95 `8.08ms`, Worker lag `29204 -> 23597 -> 15111 -> 6893 -> 0`
- Notification path split suite: notification-worker lag `0`, 운영 경계 개선으로 해석
- Ordering / failure injection: single/multi stream, Pgpool outage, missing `0`, duplicate `0`, mixed payload `0`, DLQ `0`

측정 환경은 AMD Ryzen 5 5600, Docker Desktop 12 CPU, 약 15.6GiB memory, kind single-node 기준입니다. 최신 수치와 측정 조건은 [TEST_RESULTS.md](docs/TEST_RESULTS.md)에 둡니다.

Cache-first read 검증에서는 fresh cache read `source=cache`, DB down stale fallback `degraded=true`, `snapshot_age_seconds` 응답 메타데이터를 확인했습니다.

## Trade-off

| 선택 | 얻은 것 | 포기한 것 |
| --- | --- | --- |
| API -> Kafka append | DB 장애 전파 감소 | 즉시 persistence 보장 약화 |
| Worker async persistence | 처리량 / 복구성 향상 | eventual consistency 발생 |
| inline retry | stream 순서 보존 | 뒤 event backpressure |
| DLQ replay | 실패 event 복구 | 운영 판단 필요 |
| PostgreSQL read model 유지 | 조회 / 영속성 단순화 | state path 일부 DB 의존 |

Kafka Worker KEDA 효과는 API throughput 증가로 단정하지 않습니다. Worker scaling 효과는 consumer lag, accepted-to-persisted lag, backlog drain time으로 봅니다.

## Ordering Guarantee

같은 `stream_id`는 같은 Kafka partition으로 라우팅되고, Worker는 transient failure에서 tail 재발행이 아니라 inline retry를 사용합니다. 따라서 같은 stream의 event가 실패 event를 추월하지 않도록 설계했습니다. multi-partition 전체 global ordering은 보장하지 않습니다.

## Current Bottleneck

현재 병목 후보는 Worker replica 수보다 다음 구간에 가깝습니다.

- Worker DB write throughput
- PostgreSQL insert / commit latency
- `room_sequences` lock wait
- record-by-record Worker loop
- Pgpool / DB connection pool
- consumer group rebalance / partition imbalance

다음 개선 후보는 Kafka consumer batch 처리, offset commit 전략, `room_sequences` lock 완화, idempotency state의 Kafka compacted topic 또는 별도 state backend 분리입니다.

## What I Learned

Kafka는 DB를 대체하는 저장소가 아니라 event transport / ordering / replay 경계로 쓰는 것이 적합했습니다. 장애 대응에서 중요한 것은 실패를 없애는 것이 아니라 실패를 격리하고 복구 가능하게 만드는 것입니다.

## Next Improvements

1. Worker DB write throughput / commit latency / lock wait 분리 측정
2. Kafka consumer batch 처리와 offset commit 전략 실험
3. `room_sequences` lock 완화 또는 stream sequence allocation 방식 재검토
4. idempotency state를 Kafka compacted topic 또는 별도 state backend로 분리
5. consumer group rebalance / partition imbalance 시나리오 추가

## AWS Managed Service Mapping

로컬에서 검증한 구조를 AWS로 옮기면 아래 책임으로 대응됩니다.

| 로컬 구성 | AWS migration blueprint |
| --- | --- |
| `kind` cluster | Amazon EKS |
| `ingress-nginx` | AWS Load Balancer Controller + ALB |
| local self-signed TLS | ACM + Route 53 |
| Kafka 3-broker KRaft | Amazon MSK |
| PostgreSQL HA + Pgpool | RDS PostgreSQL Multi-AZ / Aurora PostgreSQL |
| runtime secret | AWS Secrets Manager |
| local image build/load | Amazon ECR push + EKS deploy |
| Prometheus / Grafana | EKS 유지 또는 AMP / AMG |

이 Terraform 경로는 AWS에 이미 배포했다는 의미가 아니라, 로컬 검증 구조를 AWS managed architecture로 이전할 수 있게 설계한 migration blueprint입니다. 자세한 내용은 [AWS_IAC_PLAN.md](docs/AWS_IAC_PLAN.md)와 [infra/terraform/README.md](infra/terraform/README.md)에 있습니다.

## Operations

운영자는 아래 경로로 상태를 확인합니다.

- Readiness: `http://localhost/health/ready`
- Swagger / OpenAPI: `http://localhost/docs`, `/openapi.json`
- Grafana: `http://localhost/grafana/d/messaging-portfolio-overview/messaging-portfolio-operations-overview?orgId=1&refresh=5s`
- Prometheus: `http://localhost/prometheus/`
- DLQ summary: `GET /v1/dlq/ingress/summary`

운영 상태 점검:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_portfolio_status.ps1
```

전체 점검 순서는 [SERVICE_PROCESS_CHECKLIST.md](docs/SERVICE_PROCESS_CHECKLIST.md), 관측 지표는 [OBSERVABILITY.md](docs/OBSERVABILITY.md), 사고 대응은 [RUNBOOK.md](docs/RUNBOOK.md), GitOps 흐름은 [GITOPS.md](docs/GITOPS.md)에 정리했습니다.

## Documentation Map

- [QUICK_START.md](docs/QUICK_START.md): 실행 가이드
- [DEMO_GUIDE.md](docs/DEMO_GUIDE.md): 데모 화면 사용법과 시연 스크립트
- [DEMO_LITE.md](docs/DEMO_LITE.md): 2코어 저사양 서버용 demo-lite 프로파일
- [SERVICE_REQUIREMENTS.md](docs/SERVICE_REQUIREMENTS.md): 사용자 / 기능 요구 / SLO guardrail
- [ARCHITECTURE.md](docs/ARCHITECTURE.md): Kafka-centered 구조와 sequenceDiagram
- [TEST_RESULTS.md](docs/TEST_RESULTS.md): 최신 검증 결과와 과거 baseline
- [OPERATIONS.md](docs/OPERATIONS.md): 운영 지침, secret, backup, 데모 운영 작업
- [RUNBOOK.md](docs/RUNBOOK.md): Kafka Intake, PostgreSQL / Pgpool, Worker Consumer Lag, DLQ, API Contract, Resource Contention 대응
- [OBSERVABILITY.md](docs/OBSERVABILITY.md): Prometheus / Grafana / kafka-exporter 지표
- [RELIABILITY_POLICY.md](docs/RELIABILITY_POLICY.md): readiness / degraded / not_ready 정책
- [GITOPS.md](docs/GITOPS.md): Argo CD GitOps
- [AWS_IAC_PLAN.md](docs/AWS_IAC_PLAN.md): AWS migration blueprint
- [PATCH_NOTES.md](docs/PATCH_NOTES.md): 최신 변경 이력
- [REPOSITORY_STRUCTURE.md](docs/REPOSITORY_STRUCTURE.md): 저장소 구조
