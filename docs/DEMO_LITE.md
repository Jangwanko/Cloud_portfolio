# Demo Lite

## Current State - 2026-08-29

| Boundary | Version and evidence |
| --- | --- |
| Master / dev-kafka source | UI `2.4.1`, API `2.1.0`, full local-ha profile |
| Demo-dev candidate | UI `2.5.0`, API `2.1.0`, commit `a67f40e`, `363 passed` |
| Public demo-lite runtime | UI `2.4.1`, API `2.1.0`, release `2fc8649`, image `ece446d47370` |
| Public runtime check | replay `200` / `VALID`, readiness `ready`, Worker `1/1`, KEDA max `2` |

Branch HEAD나 source version만으로 배포 완료를 판단하지 않습니다. Public 상태는 release,
image, UI badge, readiness와 runtime route를 함께 확인한 경우에만 기록합니다. 따라서
`demo-dev` UI `2.5.0`은 검증된 source candidate이며 아직 public deployment가 아닙니다.

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

Public UI `2.4.1`은 actual Phase 5.1 incident에서 생성한 sanitized
`demo.verified-incident-replay.v1` artifact를 재생합니다. OpenAI API를 다시 호출하지
않고 현재 demo-lite runtime을 재진단하지 않습니다.

`demo-dev` UI `2.5.0` 후보는 네 controlled Scenario Lab 결과를
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
