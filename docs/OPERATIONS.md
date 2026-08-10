# Operations Guide

운영 점검, secret, backup/restore, demo reset, Kafka/DLQ 경계를 정리합니다. 장애별 명령과 escalation은 [RUNBOOK.md](RUNBOOK.md)를 사용합니다.

처음 실행부터 성능 기준선까지의 전체 순서는 [SERVICE_PROCESS_CHECKLIST.md](SERVICE_PROCESS_CHECKLIST.md)를 사용합니다.

## Runtime Profiles

| Profile | Purpose | Status command |
| --- | --- | --- |
| manual local full profile | kind에서 full stack 확인 | `check_portfolio_status.ps1 -SkipArgoCd` |
| local GitOps | master revision과 Argo CD sync 확인 | `check_portfolio_status.ps1` |
| demo-lite | 2코어급 demo flow | full HA / performance 증거에서 제외 |

## Runtime Secrets

설치:

```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/install-runtime-secrets.ps1
```

Secret keys:

- `AUTH_SECRET_KEY`
- `ACCESS_TOKEN_TTL_SECONDS`
- `GRAFANA_ADMIN_USER`
- `GRAFANA_ADMIN_PASSWORD`

운영 기준:

- `AUTH_SECRET_KEY`: script-generated random value 또는 명시적 override
- Grafana admin password: Windows/Linux quick start가 암호학적 random value 생성 후 `messaging-runtime-secrets`에 저장; 평문 출력 제외
- anonymous Viewer: demo dashboard 조회 전용
- `-ShowCredentials`: 공유 로그와 화면 녹화에서 사용 제외
- production: external secret manager, rotation, least-privilege workload identity
- 운영 전환: 외부 secret manager와 workload identity 연동

Kubernetes Secret에 넣는 행위는 값 자체의 안전성을 보장하지 않습니다. local generated credential도 production secret manager, rotation, RBAC, audit의 대체가 아닙니다.

Runtime request 경계:

- `REQUEST_BODY_MAX_BYTES=1048576`: HTTP body 전체 기본 상한. `Content-Length`와 chunked body를 모두 검사하고 초과 시 `413`
- 허용 설정 범위: `1..16777216` bytes. generic envelope 내부의 payload/metadata 상한은 각각 `65,536`/`16,384` UTF-8 JSON bytes로 더 작게 적용
- schema migration startup이 끝나지 않았거나 non-local auth secret이 unsafe하면 `/v1/*`, `/v2/*` business API를 readiness 우회 호출해도 `503`

Runtime log 경계:

- Uvicorn request별 access log 비활성화; request rate·status·latency는 Prometheus metric으로 집계
- Uvicorn server lifecycle과 application warning/error logger 유지; Alembic logging 설정이 기존 logger를 비활성화하지 않도록 분리
- PostgreSQL startup retry `2→4→8→16→30초` exponential backoff, 반복 warning 최대 60초당 1회
- request audit가 필요한 운영 환경: ingress 또는 외부 log pipeline에서 sampling·retention·개인정보 redaction을 별도 설계

### PostgreSQL Helm Credential

- Secret: `messaging-postgresql-ha-postgresql`
- application key reference: `DB_PASSWORD` <- `password`
- first install: chart-managed random credential 생성
- upgrade: 기존 Secret lookup과 credential 재사용
- 순서: PostgreSQL install/upgrade 완료 뒤 API/Worker/notification-worker/DLQ Replayer 적용
- Secret 유실 + PVC 유지: silent regeneration 금지, 기존 database credential 확인·복구 뒤 Secret 복원

Secret과 PVC의 lifecycle은 함께 관리합니다. Secret만 새로 만들면 기존 PostgreSQL data directory의 credential과 달라져 application 연결이 끊길 수 있습니다.

### PostgreSQL Synchronous Replication Lifecycle

Chart의 `POSTGRESQL_NUM_SYNCHRONOUS_REPLICAS=1` 환경은 fresh boot 설정에 사용되지만 persisted volume을 가진 pod 재시작 때 `postgresql.conf`가 다시 생성되면 sync standby 설정이 빠질 수 있습니다. Pod `Ready`와 StatefulSet rollout 완료만으로 HA recovery를 판정하지 않습니다.

Install/scale-up/recovery 경로는 아래 helper를 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/configure_postgres_sync.ps1 `
  -Namespace messaging-app
```

- 모든 ready PostgreSQL pod: `ALTER SYSTEM`으로 `synchronous_commit=on`, `synchronous_standby_names=ANY 1` 지속 저장 후 reload
- 현재 primary 재탐색: `pg_stat_replication`의 streaming `sync`/`quorum` standby `>=1` 확인
- credential: pod 내부 chart-managed password file 사용, host command argument/출력으로 전달 제외
- 실패 처리: helper timeout 또는 sync standby `0`이면 readiness 복구 완료로 간주 제외

## Local Status Check

Manual quick start:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_portfolio_status.ps1 -SkipArgoCd
```

Argo CD bootstrap 완료 뒤:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_portfolio_status.ps1
```

확인 대상:

- API readiness
- Kafka broker/topic와 consumer group lag
- PostgreSQL primary, standby, replication delay
- Pgpool replicas
- API / Worker / notification-worker replicas
- KEDA status
- Prometheus scrape
- backup CronJob / PVC
- Argo CD sync/health — GitOps profile에서만

## Readiness and Alerts

API `/health/ready` 상태:

| State | Direct condition |
| --- | --- |
| `ready` | schema startup 완료, Kafka reachable, PostgreSQL primary/HA guardrail 충족, non-local secret 안전 |
| `degraded` | hard failure 없음, PostgreSQL primary/standby/sync/replication guardrail 이탈 |
| `not_ready` | schema 미준비, Kafka unreachable, non-local unsafe auth secret |

Standby/sync standby count와 replication byte lag는 degraded reason에 반영됩니다. Broker count와 Worker lag/replica는 readiness state 결정과 분리된 alert/status 신호입니다. Worker replica는 `/ops/summary`와 Grafana에서 확인합니다. 상세 정책은 [RELIABILITY_POLICY.md](RELIABILITY_POLICY.md)를 사용합니다.

`grace_remaining_seconds`는 degraded 지속 시간을 읽기 위한 countdown context입니다. state는 첫 guardrail 이탈부터 즉시 `degraded`이며 이 값이 HTTP status를 지연시키지 않습니다.

Response의 `app_version`은 실행 중인 API build version입니다. 현재 `dev-kafka` GitOps target은 UI `2.3.1`, API `2.1.0`, image `a2b157f1283f`입니다. Public demo-lite는 2026-08-10 신규 서버에서 image `8640ca010960`, UI `2.3.1`, API `2.1.0`, generic event `202`, Argo `Synced / Healthy`를 확인했습니다.

## Demo Access

Dev-kafka source candidate: UI `2.3.1`, API `2.1.0`

Public demo-lite Demo UI: `2.3.1` / API `2.1.0` / image `8640ca010960` (2026-08-10 live)

Local surfaces:

- Demo: `http://localhost/demo/order-dashboard.html`
- Swagger: `http://localhost/docs`
- Grafana: `http://localhost/grafana/d/messaging-portfolio-overview/reliable-event-processing-operations-overview?orgId=1&refresh=5s`
- Prometheus: `http://localhost/prometheus/`
- Readiness: `http://localhost/health/ready`

Demo counter semantics:

- `예약 건수`: 남은 예약 / 전체 예약, Kafka append 성공 시 감소
- `Kafka 적재`: API가 ingress topic append를 완료한 수
- `DB 저장`: Worker가 PostgreSQL commit을 완료한 수
- `총 소요시간`: 전송 시작부터 해당 run의 DB 저장 확인 완료까지
- Worker: 현재 core replica / 최대 replica; full profile `2/4`, demo-lite `1/2`

UI operating behavior:

- identity: 범용 Reliable Event Processing System; 주문 lifecycle은 reference scenario
- event intake: `POST /v2/streams/{stream_id}/events`
- readiness/DLQ refresh: 기본 30초, 선택 60초
- auth token: 동일 base/user에서 memory cache 재사용, UI 기준 30분
- persistence: event append 전송과 동시에 stream 단위 `/persistence-summary`를 1초 간격 polling
- Worker scaling: 운영 상태 패널에서 현재 replica / 최대 replica 확인, 시계열 확장 증거는 Grafana에서 확인
- Advisor: 전송·저장 추적 중 `처리 중`, run 종료 뒤 카운터 불일치 판정
- DB storage evidence: Pipeline Evidence의 DB 단계 아래에 저장 컬럼과 envelope 검증 배치
- send failure: event `send_failed` 종료
- persistence timeout: 일부 미확인 상태, 완료 표시 제외
- structured evidence: `schema_version`, producer-defined `event_type`, JSON `payload`, JSON `metadata`
- DLQ: authenticated user-filtered `recent_log_sample`, detail view, manual replay

일부 event가 append 또는 persistence 확인에 실패하면 결과를 완료로 닫지 않습니다.

## Demo Reset

UI에서 `RESET DEMO DB` 확인 문구 입력 뒤 실행합니다.

- endpoint: `POST /v1/admin/demo/reset-events`
- gate: `DEMO_RESET_ENABLED=true`; default는 local/development/dev/test에서만 true
- preserved: user accounts
- reset: demo streams, messages, request status, idempotency state, notification attempts
- DLQ: `message-ingress-dlq` topic 삭제 후 재생성

이 기능은 disposable demo state 초기화용입니다. production DLQ 이력을 지우는 운영 절차가 아닙니다.

## Kafka Intake and Persistence

- generic contract: `POST /v2/streams/{stream_id}/events`
- envelope: `schema_version`, `event_type`, JSON `payload`, JSON `metadata`
- compatibility: `/v1/orders/{order_id}/events` order reference adapter와 legacy body-only stream route 유지
- success: ingress append 뒤 HTTP `202`
- Kafka bootstrap/append failure: fail-fast `503`
- final durable state: PostgreSQL
- same-stream ordering: `stream_id` key와 partition
- retry: same offset inline
- offset: successful/terminal record 단위 explicit commit
- Worker scaling: `message-worker` lag 기반 KEDA

DB commit 뒤 Worker가 발행하는 항목:

- notification job

이 발행은 현재 transactional outbox에 묶여 있지 않습니다. publish failure log/metric과 notification reconciliation이 필요합니다. request status와 event read는 Worker의 PostgreSQL transaction 결과를 조회합니다.

## Reliability Claim Boundary

이 운영 문서에서 고신뢰는 다음 구현 경계를 뜻합니다.

- 같은 `stream_id`의 partition ordering boundary와 inline retry
- 성공 또는 terminal DLQ 처리 뒤 record 단위 explicit offset commit
- PostgreSQL transaction과 idempotency state 기반 중복 persistence 방어
- retry, DLQ 격리, replay guard, PostgreSQL read model
- accepted, persisted, lag, DLQ 표본을 분리한 관측

exactly-once delivery, partition 간 global ordering, 모든 failure mode의 무손실, production SLA는 현재 검증 범위가 아닙니다. DB commit 이후 notification publish는 best-effort이며 transactional outbox gap이 남아 있습니다.

## DLQ 운영 기준

현재 DLQ API는 append-only failure log sample입니다.

```powershell
Invoke-RestMethod `
  -Headers @{ Authorization = "Bearer <token>" } `
  "http://localhost/v1/dlq/ingress/summary?limit=200&sample_limit=5"
```

| Field | Meaning |
| --- | --- |
| `total` / `count` | 조회 범위의 sample count |
| `replayable` | sample에서 replay guard 미도달 event 수 |
| `blocked` | sample에서 replay guard 도달/제외 event 수 |
| `oldest_sample_age_seconds` | sample에서 가장 오래된 log record age |
| `by_reason` | sampled `failed_reason` count |
| `by_stream` | sampled stream count |
| `recent_samples` | recent log event |

API scope fields:

- `scope=recent_log_sample`
- `user_filtered=true`
- list/summary와 manual replay는 authenticated user event 범위
- 전체 수동 재처리: UI에 로드된 replayable sample 대상
- replay guard 도달 event: manual button 비활성
- manual/automatic replay 중복: `(request_id, replay_generation)` PostgreSQL claim 공유
- 이미 persisted 또는 published claim: 동일 generation 재주입 제외

제공하지 않는 값:

- unresolved current depth
- replay 성공을 차감한 backlog
- oldest unresolved event SLO age

조사 순서:

1. Worker failure와 DLQ publish metric 확인
2. `failed_reason`, `retry_count`, `replay_count` 확인
3. data validation과 transient infrastructure failure 분리
4. 원인 수정과 PostgreSQL/Kafka 상태 확인
5. replayer log와 ingress/persistence reconciliation 확인

DLQ replayer는 시작 전과 각 polled batch 처리 전에 DB reachability를 다시 확인합니다. primary가 unavailable이면 현재 consumer를 닫고 replay를 일시 중지합니다. `DLQ_REPLAY_MAX_COUNT` 기본값은 `3`입니다.

## PostgreSQL Monitoring Role

HA 설치 script는 application user에 `pg_monitor`를 부여합니다.

용도:

- `pg_stat_replication`
- standby state / sync state
- replication byte lag
- API Prometheus gauges

production에서는 monitoring identity를 application write identity와 분리하는 방향을 권장합니다.

## Backup

Manual logical backup:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/backup_postgres_k8s.ps1
```

- output: `backups/postgres-<timestamp>.sql`
- source: Pgpool service
- credential: temporary non-root pod에 PostgreSQL HA Secret `secretKeyRef` 주입; host decode/command argument 제외
- transfer: pod file을 `kubectl cp`로 복사해 SQL text encoding 보존

Scheduled local backup:

- CronJob: `postgres-weekly-backup`
- schedule: `0 3 * * 0`
- storage: `postgres-backups` PVC
- write boundary: `.partial` 파일에 dump 완료 후 최종 `.sql`로 atomic rename
- retention: 생성 후 7일을 넘긴 `postgres-*.sql` 삭제

수동 script도 기본 `RetentionDays=7`을 적용합니다. 다른 기간이 필요한 일회성 검증은 `-RetentionDays`를 명시합니다.

CronJob 존재는 restore 성공 증거가 아닙니다. 최근 job exit, dump size, restore drill을 함께 확인합니다.

2026-07-21 local restore drill:

- host logical dump: `39,433,414` bytes
- target: 새 disposable database `portfolio_restore_validation_20260721`
- 일치 확인: 10개 table row count, Alembic `0008_generic_event_envelope`, generic v2 row `33,840`, message max id `33,840`, max sequence `25,378`
- 정리: 비교 성공 뒤 disposable database 삭제
- 해석 제한: 같은 cluster의 logical restore 검증; object storage/cluster-loss 복구와 자동 RPO/RTO는 별도 과제

## Restore

```powershell
powershell -ExecutionPolicy Bypass -File scripts/restore_postgres_k8s.ps1 `
  -BackupFile backups/postgres-<timestamp>.sql `
  -Force
```

Disposable cluster schema reset:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/restore_postgres_k8s.ps1 `
  -BackupFile backups/postgres-<timestamp>.sql `
  -ResetSchema `
  -Force
```

주의:

- `-Force` 필수
- `-ResetSchema`: destructive local recovery option
- unrelated 또는 production data에 사용 금지
- restore 뒤 row count, sequence, request status 정합성 확인

## 보안 기본선

현재 local implementation:

- PBKDF2-SHA256 password hash, per-user salt, iteration metadata
- legacy hash verification compatibility
- HMAC-signed bearer token
- runtime secret injection
- `.env` Git 제외, `.env.example` placeholder만 추적
- Grafana anonymous Viewer

제약:

- 코드 기본값·`.env.example` placeholder·빈 값·32-byte 미만 `AUTH_SECRET_KEY`는 local fallback 범위에서만 허용하며 non-local readiness와 business API에서 차단
- Prometheus/Grafana public ingress는 demo 편의 설정
- DLQ endpoints는 event payload를 포함할 수 있어 operator authorization과 audit 필요
- schema/key/route validation에 실패한 invalid ingress는 replay 원본 대신 bounded diagnostic(size, SHA-256, 1KiB base64 preview)을 저장; 정상 처리 중 retry exhaustion record는 replay용 payload 유지
- local self-signed TLS는 production trust 증거에서 제외

Production direction:

- external identity / token key rotation
- external secret manager
- SSO/VPN/private network for operations surfaces
- network policy and least privilege
- Kafka authentication과 topic별 producer ACL: API는 ingress, Worker는 notification, 승인된 replayer는 ingress replay만 발행
- DLQ payload redaction / retention / audit

## API Contract Changes

Route, request model, response model, OpenAPI, contract test를 함께 변경합니다.

FastAPI `response_model`은 readiness, event acceptance/status, stream read, DLQ list/summary/replay의 public response 경계를 고정합니다.

- Swagger: `/docs`
- schema: `/openapi.json`
- generic event acceptance: `POST /v2/streams/{stream_id}/events`, HTTP `202`
- generic read aliases: `GET /v2/event-requests/{request_id}`, `GET /v2/streams/{stream_id}/events`
- shared resource APIs: `/v1/auth/login`, `/v1/streams`, stream persistence summary
- persistence status: request status endpoint에서 별도 확인

2026-06 performance 원본의 event status `200`은 contract 명시 전 historical evidence입니다. 현재 image의 계약 증거는 새 test와 suite로 갱신합니다.

Generic v2 GitOps rollout:

1. `GENERIC_EVENTS_V2_ENABLED=false`인 `messaging-env` Secret wave `-3` 적용
2. 일반 Sync `messaging-schema-migration` Job wave `-2` 완료, Alembic head `0008` 확인; `Force=true,Replace=true`, runtime secret 의존 없음
3. Worker Deployment wave `-1` rollout 완료, 구 Worker replica `0` 확인
4. API Deployment wave `0` rollout, `local-ha` overlay가 API container에만 `GENERIC_EVENTS_V2_ENABLED=true`를 넣어 v2 공개
5. v2 canary event의 PostgreSQL `payload`/`metadata`와 v2 request status/event-list 응답 확인

Generic v2 manual local rollout:

1. `k8s/app/manifests-ha.yaml`의 `GENERIC_EVENTS_V2_ENABLED=false` 상태로 application 적용
2. API startup migration 동안 v2 POST가 `503 Generic event v2 intake is not enabled`인지 확인
3. quick start가 Worker rollout 완료를 기다림
4. `kubectl set env deployment/api -n messaging-app GENERIC_EVENTS_V2_ENABLED=true` 적용
5. API rollout/readiness 뒤 v2 canary 확인

이 순서를 뒤집지 않습니다. 구 Worker는 v2 job의 compatibility `body` preview는 읽을 수 있지만 구조화 `payload`/`metadata`를 저장하지 못합니다. old/new Worker 혼합 상태에서 v2 traffic을 열면 data fidelity가 깨집니다.

`GENERIC_EVENTS_V2_ENABLED`는 v2 POST intake gate입니다. GitOps base와 수동 local app manifest의 Secret 값은 `false`입니다. `local-ha` overlay가 API container에만 `true`를 추가하고, Windows/Linux quick start는 Worker 준비 뒤 API env를 `true`로 전환합니다. GET v2 aliases와 v1 shared resource API는 같은 read/resource 경계를 사용합니다.

### Generic v2 Downgrade Safety

Alembic `0008` downgrade는 모든 v2 row와 기존 컬럼만으로 복원할 수 없는 schema v1 `payload`/`metadata`를 검사합니다. 하나라도 있으면 lossy downgrade를 거부합니다. Order reference adapter의 추가 metadata도 검사 대상입니다. 아래 조건을 순서대로 모두 확인한 뒤에만 `0007_drop_legacy_room_sequence_allocations`로 내립니다.

1. 모든 desired state에서 `GENERIC_EVENTS_V2_ENABLED=false` 적용
2. API rollout 완료와 모든 API pod의 gate `false` 확인; v2 POST가 `503`인지 확인
3. `message-worker` consumer lag `0` 확인
4. accepted/retry 상태의 v2 request와 producer 재시도가 없어 inflight v2 event `0`임을 reconciliation으로 확인
5. 아래 PostgreSQL query의 `downgrade_unsafe_rows` 결과 `0` 확인
6. 조건과 확인 시각을 기록한 뒤 Alembic downgrade 실행

```sql
SELECT count(*) AS downgrade_unsafe_rows
FROM messages
WHERE schema_version >= 2
   OR payload IS DISTINCT FROM jsonb_build_object('text', body)
   OR metadata IS DISTINCT FROM jsonb_strip_nulls(
        jsonb_build_object(
            'classification', category,
            'external_references',
            CASE WHEN payment_id IS NULL THEN NULL
                 ELSE jsonb_build_object('payment', payment_id) END
        )
   );
```

consumer lag `0`만으로 v2 inflight 부재를 증명하지 않습니다. compacted request status, producer retry, DLQ/replay 경로를 함께 확인합니다. 어느 조건이든 불확실하거나 downgrade-unsafe row가 남아 있으면 downgrade를 시도하지 않고 새 schema/code를 유지한 채 forward recovery합니다.

Generic v2의 첫 performance 후보는 2026-07-21 local `dev-kafka`에서 실행했고 event `25,378건`, error `0.00%`, p95 `123.96ms`를 확인했습니다. Fresh DB의 hot single-stream 단일 실행이므로 stable baseline으로 승격하지 않습니다. 2026-04/06 Kafka baseline은 당시 legacy/order contract의 역사적 intake evidence로 분리합니다.

## Deployment Boundary

- manual local: build → kind image load → rollout
- master GitOps: push → tests → GHCR SHA image → overlay tag bot commit → Argo CD sync
- Argo CD: source/static file을 image로 빌드하지 않음
- registry package private: imagePullSecret 필요

상세 배포 흐름과 현재 quick-start 제한은 [GITOPS.md](GITOPS.md)를 확인합니다.
