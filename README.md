# Kubernetes 기반 이벤트 처리 운영 플랫폼

Kubernetes & GitOps Operations Platform for an Event-Processing Workload

이 프로젝트는 Kubernetes workload의 배포, 확장, 관측, 장애 복구를 검증한 DevOps·플랫폼 엔지니어링 포트폴리오입니다. 직접 만든 **Kafka 기반 고신뢰 이벤트 처리 시스템**은 운영 설계를 시험하는 workload이며 주문 lifecycle은 장애와 복구 흐름을 보여주는 reference scenario입니다.

This portfolio demonstrates Kubernetes architecture, GitOps delivery, metric-driven autoscaling, incident recovery, and restore validation through a working event-processing workload.

## 한눈에 보기 / Executive Summary

| 영역 | 구현·검증 범위 |
| --- | --- |
| Kubernetes | StatefulSet·Deployment·Job, probes, PDB, HPA·KEDA, Argo CD sync wave |
| 처리 경로 | `API → Kafka → Worker → PostgreSQL`, retry·DLQ·replay; 실제 DB entrypoint는 Pgpool |
| 관측 | API latency, consumer lag, Worker replica, commit lag, drain time, PostgreSQL HA |
| 복구 | DB outage, same-stream ordering, PostgreSQL restart, logical backup·restore |
| 대표 트러블슈팅 | CPU HPA→queue backlog KEDA 전환, scale-out 뒤 DB 경합, GitOps namespace prune, 장애 중 retry·log 폭증 |
| Cloud blueprint | EKS·ECR·MSK·RDS·ALB·ACM·Route 53·Secrets Manager Terraform 설계 |

## Kubernetes 설계 / Kubernetes Architecture

```mermaid
flowchart LR
    Git[Git push] --> CI[GitHub Actions<br/>test · render · image]
    CI --> GHCR[GHCR SHA image]
    GHCR --> Argo[Argo CD]

    subgraph K8s[Kubernetes]
      Ingress[Ingress] --> API[API<br/>CPU HPA]
      API --> Kafka[(Kafka<br/>StatefulSet)]
      Kafka --> Worker[Worker<br/>lag KEDA]
      Worker --> Pool[Pgpool] --> DB[(PostgreSQL HA)]
      Worker --> Notify[Notification Worker]
      Kafka --> DLQ[DLQ Replayer]
      Metrics[Prometheus] -. scrape .-> API
      Metrics -. scrape .-> Worker
      Metrics -. lag .-> Kafka
      Metrics --> Grafana[Grafana]
      KEDA[KEDA] -. consumer lag .-> Kafka
      KEDA --> Worker
    end

    Argo --> K8s
```

### 핵심 설계 결정

| 결정 | Kubernetes 구현 | 운영 목적 |
| --- | --- | --- |
| stateless·stateful 분리 | API·Worker는 Deployment, Kafka·PostgreSQL은 StatefulSet·PVC | rollout과 데이터 lifecycle 분리 |
| backlog 기반 확장 | API CPU HPA `6→8`, core Worker KEDA `2→4`, notification `1→2` | 수락 부하와 비동기 처리 수요를 서로 다른 신호로 확장 |
| release ordering | Secret `-3` → migration Job `-2` → Worker `-1` → API `0` | schema·consumer 호환 경계 보호 |
| traffic gate | startup·readiness·liveness probe 분리 | process 생존, dependency 준비, traffic 진입 구분 |
| state 보호 | Namespace `Prune=false`, PDB, PostgreSQL sync standby | GitOps prune·rollout 중 연쇄 장애 방지 |
| artifact 추적 | source commit → CI → immutable image tag → overlay commit → runtime image | README와 실제 배포 revision 연결 |

Full profile의 API 최소 `6`은 로컬 100 VU 측정에서 CPU 포화와 늦은 HPA 반응을 줄이기 위한 실험값입니다. production 권장값이 아니며 demo-lite는 API `1→2`로 낮춥니다. 현재 kind는 single-node이므로 node·AZ 장애 증거는 AWS 전환 과제로 분리합니다.

세부 구조와 ordering·idempotency 경계: [Architecture](docs/ARCHITECTURE.md)

## Pod 구성 / Workload Inventory

| Workload | Full profile | 책임·운영 신호 |
| --- | ---: | --- |
| `api` Deployment | `6→8` | Kafka append와 PostgreSQL read; CPU, p95, readiness |
| `worker` Deployment | `2→4` | DB persistence·retry·DLQ; consumer lag, commit lag, drain |
| `notification-worker` | `1→2` | notification attempt 기록; 전용 lag와 처리량 |
| `dlq-replayer` | `1` | replay guard 확인과 ingress 재주입 |
| `kafka` StatefulSet | `3` | 8 partitions, RF `3`, `min.insync.replicas=2`, PVC |
| PostgreSQL StatefulSet | `3` | durable source of truth, `synchronous_commit=on`, `ANY 1` |
| Pgpool Deployment | `2` | writable primary routing, PDB `minAvailable=1` |
| Prometheus·Grafana | 각 `1` | metric 수집, alert, 운영 dashboard |
| migration·bootstrap Job | release·설치 시 | schema와 topic을 workload보다 먼저 준비 |
| backup CronJob | 주 1회 | atomic dump, 7일 retention, restore 검증용 PVC |

Argo CD, KEDA, metrics-server, ingress controller는 platform controller 영역에 둡니다. application resource는 `messaging-app` namespace에서 관리합니다.

실행 방법과 manifest 경계: [Quick Start](docs/QUICK_START.md) · [GitOps](docs/GITOPS.md)

## AWS Migration Blueprint

| Local design | AWS 대응 | 이전 시 검증할 기능 |
| --- | --- | --- |
| kind node | EKS managed node group | multi-node·multi-AZ, topology spread, IRSA |
| GHCR SHA image | ECR immutable image | scan-on-push와 배포 provenance |
| ingress-nginx | AWS Load Balancer Controller·ALB | public entrypoint와 target health |
| Kafka StatefulSet | MSK | broker replication, private networking, KEDA lag |
| PostgreSQL·Pgpool | RDS Multi-AZ / Aurora PostgreSQL | managed failover, backup, PITR |
| Kubernetes Secret | Secrets Manager + ESO/CSI | rotation과 least-privilege 전달 |
| local Prometheus·Grafana | AMP·AMG 또는 EKS 내부 운영 | 장기 보존과 alert delivery |
| backup PVC | RDS backup·snapshot·PITR + S3 logical copy | cluster-loss restore와 RPO/RTO |

Terraform은 EKS·ECR·MSK·RDS·ACM·Route 53·Secrets Manager skeleton을 제공하며 `fmt`, offline `init`, `validate`를 통과했습니다. 현재 AWS에 배포된 Terraform stack은 없습니다. AWS credential과 비용이 필요한 `plan` / `apply`는 실행하지 않았습니다.

English: The Terraform source is a migration blueprint. It maps validated local responsibilities to managed AWS services; it does not claim a deployed AWS environment.

상세 구현 범위와 보안 경계: [AWS IaC Plan](docs/AWS_IAC_PLAN.md)

## 관측 설계 / Observability Map

| 구간 | 보는 지표 | 판단 질문 |
| --- | --- | --- |
| API | request rate, p95/p99, Kafka publish stage, HPA replica | 요청 수락 경로가 느린가 |
| Kafka | topic offset, `message-worker` lag | ingress가 persistence capacity를 넘었는가 |
| Worker | result throughput, queue wait, accepted-to-commit lag, DB stage | DB·lock·pool 중 어디서 지연되는가 |
| KEDA | desired·available replica, scale transition, drain time | replica 증가가 backlog 회복으로 이어졌는가 |
| downstream | notification lag와 attempt throughput | 병목이 다음 consumer로 이동했는가 |
| PostgreSQL | primary, standby·sync standby, replication delay, pool usage | writable path와 HA guardrail이 유지되는가 |
| delivery | Argo revision, runtime image, unavailable replica, restart | 검증한 artifact가 실제 실행 중인가 |
| recovery | backup Job, dump size, schema·row·sequence comparison | 파일 생성 뒤 실제 restore도 성공했는가 |

API acceptance latency는 Kafka append까지의 수락 경로를 나타냅니다. KEDA 효과는 consumer lag, commit lag, backlog drain time으로 판정합니다. Argo `Synced / Healthy`는 runtime image 확인과 함께 사용합니다.

지표 정의·dashboard·대응 연결: [Observability](docs/OBSERVABILITY.md) · [Metrics Reference](docs/METRICS_REFERENCE.md) · [Runbook](docs/RUNBOOK.md)

## STAR 운영 문제 해결 경험 / Operational STAR Cases

### 사례 1 — Worker scaling 기준을 CPU에서 queue backlog로 전환

- **S**: Worker가 DB connection·lock·commit을 기다리는 동안 CPU는 낮게 유지됐지만 Redis queue backlog와 API latency는 계속 증가
- **T**: Worker의 실제 처리 대기량을 반영하는 scaling signal 선정과 DB connection pressure 축소
- **A**: Pgpool·DB pool을 먼저 조정한 뒤 Worker CPU HPA를 KEDA queue-depth scaler로 교체
- **R**: 초기 `5,434 requests·p95 8,175ms`에서 pool 조정 후 `11,314·3,333ms`, KEDA 적용 후 `19,528·1,954ms`로 개선. 현재 Kafka 구조에서도 같은 판단을 적용해 consumer lag를 Worker scaling signal로 사용

### 사례 2 — Worker 확장 뒤 DB 경합

- **S**: Worker replica 증가 뒤 DB stage latency와 notification backlog 증가, KEDA drain 이점 불안정
- **T**: record 단위 offset 안전성을 유지하며 PostgreSQL roundtrip과 downstream commit 경합 축소
- **A**: authorization read 통합, sequence atomic upsert, notification 최대 20건 batch transaction, 성공 event INFO log 제거; clean fixed/KEDA 각 3회
- **R**: KEDA backlog 처리율 `13.38%` 증가, drain `222.49→194.05초`로 `12.78%` 감소; 오류 `0%`, ordering `100/100`, final lag `0/0`. API p95는 `6.49%` 증가해 trade-off로 기록

### 사례 3 — GitOps namespace prune 사고 복구

- **S**: Namespace desired-state 전환 중 prune으로 PostgreSQL·Pgpool·PVC와 in-cluster backup 소실
- **T**: workload와 동기 복제 guardrail 복구, 같은 유형의 연쇄 삭제 차단
- **A**: DB stack 재설치, `synchronous_commit=on`·`ANY 1` 지속 적용, Namespace `Prune=false`, disposable DB restore 비교
- **R**: PostgreSQL `3/3`, sync/quorum standby `2`; 10개 table의 schema·row count·max id·sequence 일치

### 사례 4 — DB 장애 중 API pod 재시도·로그 폭증

- **S**: PostgreSQL 미연결 시 API 6개 pod가 2초마다 연결을 재시도하고 warning을 기록해 분당 최대 180회 연결 시도·로그 발생
- **T**: readiness 차단과 자동 복구를 유지하면서 장애가 길어질 때 control plane·DB·node disk 부하 제한
- **A**: retry를 `2→4→8→16→30초` exponential backoff로 변경, 반복 warning을 60초당 1회로 제한, Uvicorn lifecycle log와 Prometheus request metric 분리
- **R**: DB 미연결 smoke에서 12초간 initial·retry warning 각 1건; full cluster에서 API 6개 lifecycle log 유지, health access log `0`, readiness 복구 확인

### 추가 트러블슈팅 기록

| 문제 | 조치 | 결과·해석 |
| --- | --- | --- |
| Kafka Worker tail retry의 ordering 위험 | Pgpool `1→2`, PDB, same-offset inline retry | p95 `80.65ms`, ordering `1..100` 유지 |
| API pod별 cache replay가 scale-out 때 DB·memory 경합 생성 | cache·snapshot topic 제거, PostgreSQL read model 단일화 | pod별 replay 제거, p95·drain 개선, 운영 경계 축소 |

실험 조건·원본·stable 채택 여부: [Validation Results](docs/TEST_RESULTS.md) · [Evidence Guide](results/README.md)

## 트러블슈팅 검증 요약 / Troubleshooting Evidence

| 검증 | 결과 | 증명 범위 |
| --- | --- | --- |
| 64-stream fixed/KEDA 각 3회 | KEDA 처리율 `137.67 events/s`, drain `194.05초`; fixed `121.42 events/s`, `222.49초` | lag 기반 scale-out과 DB 경합 trade-off |
| same-stream ordering | `stream_seq 1..100`, missing·duplicate `0` | stream partition 경계의 순서 보존 |
| DB outage | 장애 중 요청 수락 유지, 복구 뒤 persistence와 lag `0` | intake와 durable persistence 분리 |
| PostgreSQL recovery | `3/3 ready`, sync/quorum standby `2` | local process restart와 동기 복제 guardrail |
| logical restore | schema, 10개 table, row count, max id·sequence 일치 | 같은 cluster의 disposable restore |

## Demo

- Public demo-lite: [Demo UI](https://vm118.js-banjiha.cloud/demo/order-dashboard.html) · [Swagger](https://vm118.js-banjiha.cloud/docs) · [Readiness](https://vm118.js-banjiha.cloud/health/ready) · [Grafana](https://vm118.js-banjiha.cloud/grafana/d/messaging-portfolio-overview/reliable-event-processing-operations-overview?orgId=1&refresh=5s)
- Local full profile: `powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1`; Windows에서는 Docker Desktop만 설치하고 실행하면 `bootstrap_tools.ps1`이 pinned kind·kubectl·Helm을 `tools/`에 준비
- Local links: [Demo UI](http://localhost/demo/order-dashboard.html) · [Swagger](http://localhost/docs) · [Grafana](http://localhost/grafana/d/messaging-portfolio-overview/reliable-event-processing-operations-overview?orgId=1&refresh=5s)

Demo는 `Reserved → Kafka Appended → DB Persisted`를 서로 독립적으로 갱신하고 Worker 현재/최대 replica를 표시합니다. DLQ summary는 최근 append-only 표본의 `by_reason`·`replayable`·`blocked`를 보여줍니다. demo-lite는 Kafka `1`, PostgreSQL `1`, API·core Worker `1→2`, notification Worker `1`의 저사양 profile입니다.

시연 순서와 화면 의미: [Demo Guide](docs/DEMO_GUIDE.md)

## 운영 경계와 다음 단계

| 현재 경계 | 다음 검증·구현 |
| --- | --- |
| single-node local cluster | EKS multi-node·multi-AZ와 disruption drill |
| record 단위 explicit offset commit | Worker crash·consumer group rebalance·offset recovery 장애 주입 |
| node local container log | 중앙 log pipeline, sampling, retention, disk-pressure alert |
| 같은 host의 backup PVC·logical restore | S3 사본, RDS PITR, cluster-loss restore와 RPO/RTO |
| local secret | IRSA·Secrets Manager·ESO/CSI와 rotation |

현재 검증은 single-node local cluster와 workload 수준 장애에 한정됩니다. node·AZ 장애와 production SLA는 증명 범위가 아닙니다. 상세 완료 기준은 [Improvement Roadmap](docs/IMPROVEMENT_ROADMAP.md)에 있습니다.

## 운영·문서 지도 / Operations & Docs

상태 점검: `powershell -ExecutionPolicy Bypass -File scripts/check_portfolio_status.ps1 -SkipArgoCd`

- 설계·배포: [Architecture](docs/ARCHITECTURE.md) · [GitOps](docs/GITOPS.md) · [AWS IaC Plan](docs/AWS_IAC_PLAN.md)
- 관측·대응: [Observability](docs/OBSERVABILITY.md) · [Runbook](docs/RUNBOOK.md) · [Reliability Policy](docs/RELIABILITY_POLICY.md)
- 실행·검증: [Quick Start](docs/QUICK_START.md) · [Service Checklist](docs/SERVICE_PROCESS_CHECKLIST.md) · [Test Results](docs/TEST_RESULTS.md)
- 요구·개선: [Service Requirements](docs/SERVICE_REQUIREMENTS.md) · [Improvement Roadmap](docs/IMPROVEMENT_ROADMAP.md)
