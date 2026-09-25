# Demo Lite

## Terraform 실환경 검증 — OpenStack, 2026-09-25

OpenStack에서 Terraform으로 VM·네트워크 등 11개 리소스를 생성·삭제하고,
빈 환경에서 데모라이트 서비스를 자동 재구축했습니다. 이후 기존 VM의 CPU를
2 → 3 vCPU로 변경하고 데이터 보존과 서비스 복구까지 검증했습니다.

| 검증 | 실제 결과 |
| --- | --- |
| 인프라 수명주기 | 11개 생성 → 11개 삭제 → 빈 state → 11개 재생성 |
| 자동 설치 | 단일 실행 명령으로 k3s → DB·Kafka → migration → Worker → API → 이벤트 검사 |
| 기존 VM 사양 변경 | 플레이버 1개 생성, VM 1개 in-place 수정, 삭제 0개 |
| 데이터 보존 | 변경 전 이벤트를 동일 request ID로 조회; type·payload·metadata 일치 |
| 변경 후 처리 | 새 이벤트 HTTP 202 → persisted → 조회 성공 |
| 상태 일치 | 재구축 후와 resize 후 모두 terraform plan 종료 코드 0 |
| 최종 사양 | 3 vCPU / RAM 4 GiB / 디스크 40 GiB; VM ID와 Floating IP 유지 |

검증 대상은 기존 OpenStack 위에 생성한 단일 VM의 core demo-lite 구성입니다.
사양 변경 중 서비스 재시작이 있었고 정상 복구됐습니다. 무중단·HA·Git 기반 CI/CD
검증으로 해석하지 않습니다. 초기 실행기의 PowerShell·SSH 대기 문제를 수정한 뒤,
두 번째 빈 환경 구축은 수동 보정 없이 통과했습니다.

[실행 구성](../infra/terraform/envs/openstack-demo-lite/README.md) · [검증 기록](../infra/terraform/envs/openstack-demo-lite/VALIDATION.md)

## Current State — 2026-09-14

공개 UI `2.5.0`, API `2.1.0`, readiness `ready`, Worker `1/1`을 endpoint에서 확인했습니다. Release·desired image와 서버 imageID는 구분합니다. 최신 관측과 미확인 범위의 단일 근거는 [Deployment Status](DEPLOYMENT_STATUS.md)입니다.

저사양 demo-lite는 full master의 핵심 흐름을 시연합니다. Outbox와 full HA 검증은 포함하지 않습니다.

## Purpose

Demo Lite는 제한된 서버에서 핵심 event-processing 경계를 공개 시연합니다.

- API → Kafka → Worker → PostgreSQL 처리 흐름
- Kafka append와 PostgreSQL persistence의 분리된 진행 상태
- lag 기반 core Worker 확장
- DLQ와 Operations Advisor
- actual verified incident의 static AI Investigation replay

3-broker Kafka HA, PostgreSQL standby failover와 full-profile 성능 baseline은 local-ha에서
검증합니다. Demo Lite 수치로 대체하지 않습니다.

## Profile Boundary

| Component | Full local-ha | Demo Lite |
| --- | ---: | ---: |
| Kafka | `3` brokers | `1` broker |
| PostgreSQL | primary `1` + standby `2` | single instance |
| Pgpool | `2` | `1` |
| API | `6→8` | `1→2` |
| Core Worker | `2→4` | `1→2` |
| Notification Worker | `1→2` | fixed `1` |
| Main purpose | HA, recovery, performance experiments | public replay and low-resource operation |

## AI Replay Boundary

Public UI의 기본 Investigation은 actual Phase 5.1 incident에서 생성한 sanitized
`demo.verified-incident-replay.v1` artifact를 재생합니다. OpenAI API를 다시 호출하지
않고 현재 demo-lite runtime을 재진단하지 않습니다.

공개 UI `2.5.0`은 네 controlled Scenario Lab 결과를
`demo.verified-scenario-replays.v1`로 투영합니다. 같은 deterministic activation에서
observation에 따라 다음 read-only tool 선택이 달라지는 기록을 비교합니다.

- Worker DB-path pressure
- Worker replica shortfall
- PostgreSQL path degradation
- Telemetry unavailable

Worker shortfall scenario의 Deployment `current=4`, `ready/available=2/2`와 KEDA
`current=4`는 같은 `2026-08-23T15:32:08Z` capture입니다. KEDA current는 scaler가
관측한 replica 수입니다. available Worker 수나 scale-out 완료 시점을 뜻하지 않습니다.

Normalizer의 `WORKER_CAPACITY_SHORTFALL`은 deterministic observation classification입니다.
Agent의 `WORKER_CAPACITY_SHORTFALL_SUSPECTED=SUPPORTED`는 evidence-grounded hypothesis입니다.
Grounding Validator는 output schema와 evidence citation을 검사하며 root cause를 확정하지
않습니다.

## Release Boundary

1. `demo-dev` source와 tests 확정
2. CI validation 통과
3. 검증된 commit으로 image build
4. 동일 source commit과 image tag를 `demo-lite` release에 기록
5. Argo CD sync
6. UI badge, readiness, replay route, Worker/KEDA 상태 확인

Schema rollout은 migration → Worker → API gate 순서를 유지합니다. UI candidate를
기록했다는 이유만으로 public runtime version을 올리지 않습니다.

## Public Endpoints

- Demo UI: `https://vm118.js-banjiha.cloud/demo/order-dashboard.html`
- Swagger: `https://vm118.js-banjiha.cloud/docs`
- Readiness: `https://vm118.js-banjiha.cloud/health/ready`
- Grafana: `https://vm118.js-banjiha.cloud/grafana/d/messaging-portfolio-overview/reliable-event-processing-operations-overview?orgId=1&refresh=5s`

`master`는 full local-ha 실행 경로의 canonical source입니다. `demo-dev`는 저사양 배포
후보를 검증하고, `demo-lite`는 승인된 public release와 CI image-tag commit을 보존합니다.

## 장애 해석 경계

- `demo-lite` PostgreSQL은 단일 primary 기준입니다. primary 연결 실패는 standby failover를 의미하지 않으며, 단일 primary 복구 대기와 Kafka backlog / Worker retry 관점으로 설명합니다.
- full HA topology와 성능 baseline은 `local-ha` / full-ha 문서와 테스트 결과에서 설명합니다. 기존 DB outage/recovery 결과를 primary promotion/failover 성공 증거로 재표현하지 않습니다.
