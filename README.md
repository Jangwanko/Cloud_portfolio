# 주문 이후 이벤트 처리 시스템 포트폴리오

Post-Order Event Pipeline Portfolio

- 서비스 배경: 쇼핑몰에서 결제와 주문 완료 이후 발생하는 이벤트 처리
- 핵심 경로: API -> Kafka -> Worker -> PostgreSQL
- 운영 범위: 저장, 분류, 알림, 장애 격리, 재처리
- 사용자 화면: 결제 완료 / 주문 완료 응답 중심
- 내부 처리: Kafka / Worker / DLQ / materialized cache / observability

This project is a Kafka-centered post-order event pipeline. The customer-facing path returns payment/order completion quickly. Persistence, classification, notification, DLQ isolation, replay, and backlog drain run through the internal Kafka / Worker path.

## TL;DR

- API: Kafka append 후 `202 Accepted` 반환
- Worker: Kafka partition consume, PostgreSQL HA persistence
- Failure: inline retry 후 Kafka DLQ topic 격리
- Replay: DLQ Replayer로 복구 가능 event 재주입
- Scaling: Kafka consumer lag 기준 Worker scaling
- Read path: `message-snapshots`, `stream-snapshots` 기반 cache-first read
- AWS path: EKS, MSK, RDS PostgreSQL, ALB, ACM, Secrets Manager 대응 구조

상세 문서:

- 서비스 기준: [SERVICE_REQUIREMENTS.md](docs/SERVICE_REQUIREMENTS.md)
- 구조: [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 최신 검증 결과: [TEST_RESULTS.md](docs/TEST_RESULTS.md)

## What To Look For

- 결제/주문 완료 응답 후 내부 이벤트 처리
- `예약 건수`, `Kafka 적재`, `DB 저장` 분리 확인
- Worker DB 저장 전까지 `받았다`와 `저장됐다` 구분
- Operations Advisor: 규칙 기반 위험 / 해결 알림, AI API 미사용
- `demo-lite`: 별도 브랜치의 저사양 데모 profile
- AWS 문서: managed service 이전 설계도

## Full System vs Demo Lite

| Mode | Purpose | Read it as |
| --- | --- | --- |
| Full system / 본래 시스템 | Kafka 3 broker, PostgreSQL HA, Pgpool, KEDA scale-out, Grafana, DLQ replay 포함 | HA, ordering, recovery, performance baseline 기준 |
| Demo lite / 저사양 데모 | `demo-lite` 브랜치에서 2코어급 서버용 profile 제공 | 작은 서버에서 같은 흐름 확인 |

- `demo-lite`: 포트폴리오의 이벤트 처리 개념을 볼 수 있게 만든 데모 사이트
- `demo-lite` is a demo site built to show the event-processing concept of this portfolio.
- master 기준 즉시 실행 경로: 아래 Local Demo
- demo-lite 기준과 제약: [DEMO_LITE.md](docs/DEMO_LITE.md)

## Local Demo

URLs:

- Demo UI: `http://localhost/demo/order-dashboard.html`
- GitHub: `https://github.com/Jangwanko/Cloud_portfolio`
- Swagger / API docs: `http://localhost/docs`
- Grafana: `http://localhost/grafana/d/messaging-portfolio-overview/messaging-portfolio-operations-overview?orgId=1&refresh=5s`
- Readiness: `http://localhost/health/ready`

처음 실행:

- Windows 기준: Docker Desktop만 설치하고 실행
- helper: `scripts/bootstrap_tools.ps1`가 `tools/kind.exe`, `tools/kubectl.exe`, `tools/helm/windows-amd64/helm.exe` 준비

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1
```

화면 변경만 반영:

```powershell
docker build -t messaging-portfolio:local .
tools\kind.exe load docker-image messaging-portfolio:local --name messaging-ha
kubectl rollout restart deployment/api -n messaging-app
kubectl rollout status deployment/api -n messaging-app --timeout=180s
```

Demo steps:

- `샘플 10개/100개/1000개 추가`
- `결제 완료 / 주문 완료 이벤트 보내기`
- `예약 건수 -> Kafka 적재 -> DB 저장 -> 총 소요시간` 확인
- Worker 현재/최대 replica 확인
- Operations Advisor / Readiness / DLQ summary 확인

English demo script:

1. Start Docker Desktop and run `scripts/quick_start_all.ps1`.
2. Open `http://localhost/demo/order-dashboard.html`.
3. Click `EN`.
4. Click `Add 10 Samples`, `Add 100 Samples`, or `Add 1000 Samples`.
5. Click `Send Post-Order Events`.
6. Watch `Reserved -> Kafka Appended -> DB Persisted`.

관련 문서:

- 실행 세부: [QUICK_START.md](docs/QUICK_START.md)
- 데모 시연: [DEMO_GUIDE.md](docs/DEMO_GUIDE.md)
- 운영 작업: [OPERATIONS.md](docs/OPERATIONS.md)

## What This Proves

- DB 장애 전파 완화: Kafka append-first 경계
- 같은 `stream_id`: Kafka partition boundary와 Worker inline retry로 순서 유지
- 정상 event 흐름 / 장애 / DLQ 흐름: [ARCHITECTURE.md](docs/ARCHITECTURE.md)의 `sequenceDiagram`
- DLQ 판단: `/v1/dlq/ingress/summary`의 `by_reason`, `replayable`, `blocked`
- 운영 점검: `check_portfolio_status.ps1`로 readiness, Argo CD, kafka-exporter lag, KEDA, backup PVC 확인

## Problem

- 주문 완료 이후에도 계속 발생하는 event:
  - 결제 승인
  - 주문 생성
  - 배송 시작
  - 환불 요청
  - 알림 발행
- DB 동기 write 중심 처리의 위험:
  - write 병목
  - timeout
  - 장애 전파
  - 실패 event 유실
- 목표:
  - 빠른 event 수락
  - stream 단위 순서 유지
  - 실패 event 격리
  - 복구 가능한 replay 경로
  - 운영자가 볼 수 있는 증거

## Solution

- API: Kafka ingress topic append, 빠른 응답
- Worker: PostgreSQL HA 최종 영속화, request status 갱신
- Snapshot: DB commit 이후 `message-snapshots`, `stream-snapshots` compacted topic publish
- Read model: `API local materialized cache`의 cache-first read 원본
- Notification: `message-notifications` topic과 `notification-worker` 분리
- Failure: retry / DLQ / replay guard로 복구 가능 상태 유지

구현 세부:

- 구조: [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 장애 대응: [RUNBOOK.md](docs/RUNBOOK.md)

## Architecture Boundary

Kafka-only 구조가 아니라 Kafka-centered 구조.

Kafka-centered:

- event intake
- stream ordering boundary
- Worker consumer group processing
- retry / DLQ / replay
- lag based autoscaling
- DB snapshot compacted topics

PostgreSQL state path:

- auth / membership
- final message persistence
- stream sequence
- read model

## Intake Boundary: Idempotency State Path

- `X-Idempotency-Key`: Kafka payload 포함
- 최종 deduplication: Worker persistence 단계
- API event intake 기준선: Kafka append 전에 PostgreSQL claim 생성 제외
- DB read fallback: Kafka ingress event 미사용
- cache source: DB commit 이후 snapshot topic
- stale fallback: `degraded=true`, `snapshot_age_seconds`

## Validation Summary

대표 Kafka baseline:

| Item | Result |
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

- 2026-06-09 rerun: `34,284` requests, error `0.00%`
- Worker lag: `36394 -> 33274 -> 23563 -> 11971 -> 0`
- Post-tuning suite: accepted-to-persisted p95 `8.08ms`
- Notification path split: notification-worker lag `0`
- Ordering / failure injection: missing `0`, duplicate `0`, mixed payload `0`, DLQ `0`
- Cache-first read: `source=cache`
- DB down stale fallback: `degraded=true`, `snapshot_age_seconds`

측정 조건:

- AMD Ryzen 5 5600
- Docker Desktop 12 CPU
- 약 15.6GiB memory
- kind single-node
- 상세 수치: [TEST_RESULTS.md](docs/TEST_RESULTS.md)

## Trade-off

| Choice | Gain | Cost |
| --- | --- | --- |
| API -> Kafka append | DB 장애 전파 감소 | 즉시 persistence 보장 약화 |
| Worker async persistence | 처리량 / 복구성 향상 | eventual consistency |
| inline retry | stream 순서 보존 | 뒤 event backpressure |
| DLQ replay | 실패 event 복구 | 운영 판단 필요 |
| PostgreSQL read model 유지 | 조회 / 영속성 단순화 | state path 일부 DB 의존 |

Worker scaling 해석:

- API throughput 증가로 단정 금지
- 확인 지표:
  - consumer lag
  - accepted-to-persisted lag
  - backlog drain time

## Ordering Guarantee

- 같은 `stream_id`: 같은 Kafka partition
- Worker transient failure: inline retry
- 같은 stream event: 실패 event 추월 방지
- multi-partition 전체 global ordering은 보장하지 않습니다

## Current Bottleneck

- Worker DB write throughput
- PostgreSQL insert / commit latency
- `room_sequences` lock wait
- record-by-record Worker loop
- Pgpool / DB connection pool
- consumer group rebalance / partition imbalance

## What I Learned

- Kafka: DB 대체 저장소보다 event transport / ordering / replay 경계
- 장애 대응: 실패 제거보다 격리와 복구 가능성 확보
- Worker scaling: replica 수보다 DB write throughput과 backlog drain time 확인

## Next Improvements

1. Worker DB write throughput / commit latency / lock wait 분리 측정
2. Kafka consumer batch 처리와 offset commit 전략 실험
3. `room_sequences` lock 완화 또는 stream sequence allocation 방식 재검토
4. idempotency state를 Kafka compacted topic 또는 별도 state backend로 분리
5. consumer group rebalance / partition imbalance 시나리오 추가

## AWS Managed Service Mapping

| Local | AWS migration blueprint |
| --- | --- |
| `kind` cluster | Amazon EKS |
| `ingress-nginx` | AWS Load Balancer Controller + ALB |
| local self-signed TLS | ACM + Route 53 |
| Kafka 3-broker KRaft | Amazon MSK |
| PostgreSQL HA + Pgpool | RDS PostgreSQL Multi-AZ / Aurora PostgreSQL |
| runtime secret | AWS Secrets Manager |
| local image build/load | Amazon ECR push + EKS deploy |
| Prometheus / Grafana | EKS 유지 또는 AMP / AMG |

Terraform 위치:

- 현재 AWS 미배포
- 로컬 검증 구조를 AWS managed architecture로 이전하기 위한 migration blueprint
- 상세: [AWS_IAC_PLAN.md](docs/AWS_IAC_PLAN.md)
- Terraform skeleton: [infra/terraform/README.md](infra/terraform/README.md)

## Operations

상태 확인:

- Readiness: `http://localhost/health/ready`
- Swagger / OpenAPI: `http://localhost/docs`, `/openapi.json`
- Grafana: `http://localhost/grafana/d/messaging-portfolio-overview/messaging-portfolio-operations-overview?orgId=1&refresh=5s`
- Prometheus: `http://localhost/prometheus/`
- DLQ summary: `GET /v1/dlq/ingress/summary`

운영 상태 점검:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_portfolio_status.ps1
```

관련 문서:

- 전체 점검: [SERVICE_PROCESS_CHECKLIST.md](docs/SERVICE_PROCESS_CHECKLIST.md)
- 관측 지표: [OBSERVABILITY.md](docs/OBSERVABILITY.md)
- 사고 대응: [RUNBOOK.md](docs/RUNBOOK.md)
- GitOps 흐름: [GITOPS.md](docs/GITOPS.md)

## Documentation Map

- [QUICK_START.md](docs/QUICK_START.md): 실행 가이드
- [DEMO_GUIDE.md](docs/DEMO_GUIDE.md): 데모 화면 사용법과 시연 스크립트
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
