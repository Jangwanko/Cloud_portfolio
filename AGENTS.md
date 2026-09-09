# Project Context for Codex

작업 시작 시 이 파일과 현재 branch / git status를 확인합니다.
관련 작업에 필요한 문서만 Context Routing에서 선택하고, 저장소 전체를 다시 분석하지 않습니다.

## Project Identity

- **이벤트 처리 워크로드를 위한 Kubernetes·GitOps 운영 플랫폼**
- 직접 만든 **Kafka 기반 고신뢰 이벤트 처리 시스템**(`Reliable Event Processing System`)을 배포·확장·관측·장애 복구·backup/restore 검증 workload로 사용
- 공개 핵심 계약: `POST /v2/streams/{stream_id}/events`
- Client 입력: `event_type`, JSON `payload`, JSON `metadata`; API가 accepted/Kafka envelope에 `schema_version=2` 부여
- v2 request status/event list GET alias 제공; 인증과 stream 생성은 공유 `/v1` resource API 사용
- 주문·결제 lifecycle은 reference scenario; 기존 order route와 body/category/payment_id는 compatibility adapter/alias 유지
- Kafka / Worker / DLQ / PostgreSQL read model / observability가 수락 이후 처리와 장애 대응 담당

## Human / Codex Roles

- 사람: 문제·요구사항·범위·architecture boundary·변경 금지 영역·acceptance criteria 결정, 운영/성능 기준과 검증 해석, 최종 설계 선택과 위험 작업 승인
- Codex: 필요한 repository 탐색, 코드·설정 구현, 반복 수정, 테스트 작성·실행, diff·로그 정리, 구현 대안과 실패 원인 후보 제안
- 작업 흐름: Problem → Context → Constraints → Implementation → Validation → Evidence → Decision
- 상세 역할·실패 피드백·측정 구조·문서 작성·rollback 규칙: [AI Engineering Workflow](docs/AI_ENGINEERING_WORKFLOW.md)

## Core Invariants

- API는 Kafka `message-ingress` append 뒤 `202 Accepted` 반환; PostgreSQL 선행 쓰기 없음
- `message-worker`가 Kafka partition을 consume하고 PostgreSQL에 비동기 persistence
- PostgreSQL은 request status/event read의 최종 durable source of truth; DB read 장애는 stale data 대신 `503`
- `schema_version`, `event_type`, `payload`, `metadata`는 Kafka envelope와 DB persistence 경계에서 구조화된 필드로 유지
- 같은 stream ordering은 `stream_id` 기준 Kafka partition boundary와 Worker inline retry 범위; global ordering 아님
- 성공/terminal record 단위 explicit offset commit 유지; 최종 idempotency/deduplication은 Worker의 PostgreSQL persistence에서 처리
- `X-Idempotency-Key`는 Kafka payload에 포함; API hot path에서 PostgreSQL claim 생성 없음
- 실패 event는 retry 후 `message-ingress-dlq` 격리; replay guard와 DLQ API 유지
- DLQ list/summary는 append-only log의 최근 표본; unresolved depth·현재 backlog·미해결 event SLO로 해석 금지
- Worker autoscaling은 KEDA Kafka consumer lag, API autoscaling은 CPU HPA 사용
- `/health/ready`는 schema·Kafka·PostgreSQL HA·auth secret 판정; Worker replica는 `/ops/summary`와 Grafana에서 확인
- Notification 경계는 `notification_attempts` 기록까지; 외부 이메일/SMS/push 발송 성공으로 해석 금지
- Kafka append와 DB persistence는 별도 운영 증거; Worker scaling 효과는 lag·persistence latency·drain time으로 평가
- Redis queue-first와 Kafka event stream 결과 분리; historical legacy/order intake baseline을 generic v2 성능으로 재표현 금지
- `demo-lite`는 저사양 시연 profile; HA/failover/full-profile 성능 baseline 증거로 사용 금지
- 고신뢰는 ordering boundary·idempotent persistence·retry/DLQ/replay·관측/복구 검증 범위; exactly-once·무손실·production SLA 주장 금지
- `message-*` topic, `message-worker`, `messaging-app`, `rooms`/`messages` 등 배포·저장 상태의 물리 식별자 유지
- Schema/consumer/API 변경은 migration → 호환 Worker → API gate 순서 확인; 구 Worker에 v2 traffic을 먼저 열면 구조화 payload/metadata 유실
- Source candidate, image publication, runtime rollout을 구분; 상세 상태와 검증 조건은 대상 문서에서 확인

## Work / Branch Rules

- `dev-kafka`: 일반 개발·문서 개편 기본 브랜치, 실제 개발/검증용 Argo CD revision
- `master`: 최종 병합·보관 및 GitOps 기본 배포 기준
- `demo-dev`: 저사양 데모 개발 브랜치
- `demo-lite`: 2코어 k3s 축소 데모 배포 브랜치; 일반 개발 브랜치로 사용 금지
- 기존 미커밋 사용자 변경 보존; 작업 대상 역할에 맞는 브랜치에서 진행
- 사용자가 commit/push/merge/PR 생성 또는 CI 확인을 명시적으로 요청하면 해당 범위는 단계마다 재승인 없이 검증까지 완료
- 요청받지 않은 commit/push/merge/PR 생성·브랜치 간 전파는 임의 수행 금지; 문서 공유 대상과 적용 범위만 기록
- 승인된 Git·네트워크 명령 형식을 재사용하고 독립 확인을 묶어 불필요한 승인 최소화
- 요청받은 원격 CI는 완료까지 확인하고 결과·최종 bot commit 확인; 불명확한 merge conflict는 사용자 판단 요청
- 공통 문서는 관련 주요 브랜치에 공유하되 branch별 설정 복사 금지; 상세 [공유 규칙](docs/AI_ENGINEERING_WORKFLOW.md#브랜치-간-문서-공유) 적용

## Validation Gate

- 완료 보고보다 acceptance criteria에 맞는 검증 evidence 우선
- 대표 전체 테스트: `.venv\Scripts\python.exe -m pytest -q`
- 현재 test count는 현재 실행 출력으로 확인; 과거 pass count를 현재 결과로 재사용 금지
- 변경 성격에 맞는 contract/readiness·smoke·manifest render·CI·ordering/failure/recovery·성능 검증 선택
- 실패 시 Gate, expected/actual, 로그, 위반 invariant, 수정 가능 범위를 다음 iteration에 전달
- 실행 불가능하면 환경 원인과 실제 실행 범위 보고; 검증하지 않은 결과를 성공으로 보고 금지
- 개발 환경과 반복 실행 명령: [QUICK_START.md](docs/QUICK_START.md)

## Authority Boundary

- Destructive operation, secret·실제 데이터 손실 위험, 의미가 불확실한 merge conflict는 사람 판단 필요
- Architecture contract 변경과 stable baseline 승격은 사람이 정한 범위·기준에 따라 결정
- 증거 없는 성능, production SLA, exactly-once/global ordering/무손실, 미검증 HA/failover 주장 금지
- DB outage/recovery를 primary promotion 성공으로 재표현 금지; 역사적 failover와 현재 재검증 범위 구분
- Rollback은 status·commit·diff로 기준점을 특정하고 요청 범위만 복원; 광범위한 삭제성 명령은 명확한 승인 필요

## Context Routing

관련 작업이 아니면 아래 세부 문서를 미리 읽지 않습니다. 문서 안에서도 필요한 주제부터 확인합니다.

| 작업 | 읽을 문서 |
| --- | --- |
| 구조·event contract·ordering·schema 경계 | [ARCHITECTURE](docs/ARCHITECTURE.md), [SERVICE_REQUIREMENTS](docs/SERVICE_REQUIREMENTS.md) |
| 성능·benchmark·과거 검증 수치 | [TEST_RESULTS](docs/TEST_RESULTS.md), [원본·provenance](results/README.md) |
| Observability·지표·readiness 해석 | [OBSERVABILITY](docs/OBSERVABILITY.md), [RELIABILITY_POLICY](docs/RELIABILITY_POLICY.md) |
| GitOps·image workflow·배포 순서 | [GITOPS](docs/GITOPS.md), [k8s 구조](k8s/README.md) |
| 운영·장애·PostgreSQL 복구 | [OPERATIONS](docs/OPERATIONS.md), [RUNBOOK](docs/RUNBOOK.md) |
| Ops Agent·condition·diagnosis·recovery | [OPS_AGENT](docs/OPS_AGENT.md), [CLI와 보안 경계](ops_agent/README.md) |
| Demo UI·카운터·Advisor·버전 변경 | [DEMO_GUIDE](docs/DEMO_GUIDE.md), [OPERATIONS](docs/OPERATIONS.md) |
| demo-lite·저사양 profile | [DEMO_LITE](docs/DEMO_LITE.md), [현재 검증 상태](docs/TEST_RESULTS.md) |
| AI/Codex 협업·문서/README 작성·rollback | [AI_ENGINEERING_WORKFLOW](docs/AI_ENGINEERING_WORKFLOW.md) |
| 개발 환경·실행·반복 검증 명령 | [QUICK_START](docs/QUICK_START.md) |
| 과거 발전 과정·commit/image/checkpoint | [PROJECT_EVOLUTION](docs/PROJECT_EVOLUTION.md), [PATCH_NOTES](docs/PATCH_NOTES.md) |
| Outbox candidate·notification transaction | [TRANSACTIONAL_OUTBOX](docs/TRANSACTIONAL_OUTBOX.md) |
| 배포 실패 lab·backup/restore drill | [RELEASE_FAILURE_LAB](docs/RELEASE_FAILURE_LAB.md), [OFFHOST_BACKUP_DRILL](docs/OFFHOST_BACKUP_DRILL.md) |
| AWS migration blueprint | [AWS_IAC_PLAN](docs/AWS_IAC_PLAN.md) |
| 개선 우선순위·파일 위치 | [IMPROVEMENT_ROADMAP](docs/IMPROVEMENT_ROADMAP.md), [REPOSITORY_STRUCTURE](docs/REPOSITORY_STRUCTURE.md) |
