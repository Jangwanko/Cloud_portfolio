# 운영 지침

운영 관점에서 필요한 secret, backup, restore, 운영 UI 경로를 정리한 문서입니다.
서비스 전체 프로세스의 점검 순서는 [SERVICE_PROCESS_CHECKLIST.md](SERVICE_PROCESS_CHECKLIST.md)에서 함께 관리합니다.

## Runtime secret
로컬 kind 기준 운영 보강을 위해 runtime secret를 별도로 생성합니다.

생성 스크립트:

```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/install-runtime-secrets.ps1
```

생성 대상:
- `AUTH_SECRET_KEY`
- `ACCESS_TOKEN_TTL_SECONDS`
- `GRAFANA_ADMIN_USER`
- `GRAFANA_ADMIN_PASSWORD`

특징:
- `scripts/quick_start_all.ps1`와 `k8s/scripts/setup-kind.ps1`에서 자동 실행됩니다.
- Grafana 자격증명은 더 이상 매니페스트에 하드코딩하지 않습니다.
- API / Worker / DLQ replayer가 동일 secret를 받아 인증 관련 값을 사용합니다.

## PostgreSQL monitoring role
PostgreSQL HA 설치 후 `k8s/scripts/install-ha.ps1`는 `portfolio` 사용자에게 `pg_monitor` 역할을 부여합니다.

이 권한은 `pg_stat_replication`을 읽기 위한 PostgreSQL 내장 읽기 전용 모니터링 역할입니다. API는 이 정보를 사용해 standby의 `state`, `sync_state`, replication lag를 Prometheus metric으로 노출합니다.

## PostgreSQL 백업
로컬 HA PostgreSQL에 대해 logical backup을 생성할 수 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/backup_postgres_k8s.ps1
```

결과:
- `backups/postgres-<timestamp>.sql`

동작 방식:
- `messaging-postgresql-ha-postgresql` secret에서 DB password를 읽습니다.
- `pgpool` 서비스 경유로 `pg_dump`를 수행합니다.
- 결과를 로컬 `backups/` 디렉터리에 저장합니다.

## 주간 백업 일정
HA 배포에는 주 1회 PostgreSQL logical backup을 남기는 `CronJob`이 포함되어 있습니다.

- 리소스 이름: `postgres-weekly-backup`
- 스케줄: `0 3 * * 0`
- 저장 위치: cluster PVC `postgres-backups`
- 보관 정책: 최근 8개 dump만 유지

확인 예시:

```powershell
kubectl get cronjob -n messaging-app
kubectl get pvc -n messaging-app
```

참고:
- 현재 구성은 주기 backup 설정이 포함된 운영 흐름을 검증합니다.
- 필요하면 이후 일 단위 또는 더 짧은 주기로 쉽게 변경할 수 있습니다.

## PostgreSQL 복구
logical backup을 현재 클러스터 DB에 적용할 수 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/restore_postgres_k8s.ps1 `
  -BackupFile backups/postgres-20260416-163842.sql `
  -Force
```

스키마 초기화 후 복원:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/restore_postgres_k8s.ps1 `
  -BackupFile backups/postgres-20260416-163842.sql `
  -ResetSchema `
  -Force
```

주의:
- 기본값으로는 실행하지 않습니다. 반드시 `-Force`가 필요합니다.
- `-ResetSchema`를 주면 `public` schema를 비운 뒤 backup SQL을 적용합니다.
- 현재 복구 절차는 disposable local cluster 기준의 운영 흐름을 검증합니다.

## 데모 접근
로컬 데모 기준 운영 UI 경로:
- Grafana overview: `http://localhost/grafana/d/messaging-portfolio-overview/messaging-portfolio-operations-overview?orgId=1&refresh=5s`
- Grafana 데모 접근: anonymous Viewer 활성화, 대시보드 링크 바로 열림
- Grafana admin 계정: `messaging-runtime-secrets`로 유지, 설정 변경 시 사용
- Prometheus: `http://localhost/prometheus/`

참고:
- 기본 운영 문서와 데모 경로는 `http://localhost` 기준입니다.
- HTTPS는 local self-signed certificate 기반의 TLS 검증용 보조 경로이며, 브라우저에서 보안 경고가 처음 한 번 표시될 수 있습니다.

## Demo Lite 운영 기준

2코어 2스레드급 서버에서는 full HA profile 대신 `demo-lite` profile을 사용합니다.

실행:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_lite.ps1
```

서버 배포:

```bash
HOST_NAME=your.domain.example BASE_URL=http://your.domain.example bash scripts/deploy_lite_k3s.sh
```

`demo-lite`는 Kafka 1 broker, PostgreSQL 1 primary, Pgpool 1 replica, API 1 replica, Worker 1~2 replica 기준입니다. 이 모드는 저사양 서버에서 주문 이후 이벤트 흐름을 보여주기 위한 profile이며, HA 장애 허용성과 성능 baseline 증명은 full-ha profile의 책임입니다.

Pgpool connection budget:
- `FATAL: Sorry, too many clients already` 발생 시 Pgpool client slot 고갈 확인.
- Grafana PostgreSQL Primary `0`, readiness `postgres_primary_unreachable`, login `503`이 함께 나타날 수 있음.
- `demo-lite` 기준 Pgpool `numInitChildren=16`, `reservedConnections=2`, app `DB_POOL_MAX_CONN=2` 유지.
- 서버 반영은 GitOps app sync가 아니라 PostgreSQL Helm release upgrade 필요.

DB 복구 후 자동 재처리:
- DB / Pgpool 같은 transient persistence 장애는 Kafka offset을 commit하지 않고 Worker가 계속 retry.
- retry backoff는 최대 `INGRESS_RETRY_MAX_DELAY_SECONDS=30` 안에서 제한.
- DB 복구 후 같은 Kafka event가 PostgreSQL에 저장되어야 정상.
- DLQ는 stream sequence gap, 잘못된 payload, replay guard처럼 운영자 확인이 필요한 event 격리 용도.
- `demo-lite`에서도 `dlq-replayer` 1개를 유지해 이미 DLQ로 격리된 replay 대상 event를 ingress topic으로 재주입.
- DLQ `blocked`는 replay guard에 걸린 event로 수동 확인 필요.
- Grafana에서 DLQ total, replay metric, DB 저장 증가를 함께 확인.

자동 재처리 경로:
- Kafka에 남아 있는 미커밋 event: Worker가 같은 message를 붙잡고 retry, DB 복구 후 persistence 성공, 그 다음 offset commit.
- 이미 DLQ로 격리된 replayable event: `dlq-replayer`가 `ping_db()`로 DB 복구를 확인한 뒤 ingress topic으로 재주입.
- 두 경로 모두 운영자가 수동으로 event를 다시 보내는 흐름이 아님.

로그 해석:
- PostgreSQL log의 `duplicate key value violates unique constraint "users_username_key"`가 `demo-order-user`에 반복되면 DB primary 다운 증거가 아닙니다.
- 데모 인증 준비 과정에서 이미 존재하는 계정을 다시 만들려 한 로그입니다.
- 데모 UI는 base URL / username 기준으로 계정 생성 시도를 한 번만 수행하고 이후에는 login만 수행해야 합니다.
- `pg_stat_activity`에서 `idle in transaction`이 보이면 `xact_age`, `state_age`, `query`를 함께 확인합니다.
- 수 초 단위 `request_statuses` polling은 pool 반환 전 transaction 정리 대상입니다. 수 분 이상 남으면 비정상 세션으로 보고 terminate 여부를 판단합니다.

## 데모 운영 작업

데모 화면의 운영자 이벤트 큐는 실제 Kafka / Worker / PostgreSQL 흐름을 보여주기 위한 로컬 운영 패널입니다.

`전송 전 예약 비우기`:
- 아직 Kafka로 보내지 않은 `reserved` 이벤트만 취소합니다.
- 이미 API 전송이 시작된 작업은 취소하지 않습니다.
- 시작된 작업은 Kafka 적재와 DB 저장까지 계속 추적합니다.
- 버튼을 누른 시점에 전송 중인 이벤트는 `sending` 상태로 분리되어 예약 취소 대상에 들어가지 않습니다.

데모 counter 기준:
- `예약 건수`는 전송 시작 후 `남은 예약/전체 예약`으로 표시하며, Kafka append 성공 시 줄어듭니다.
- `Kafka 적재`는 API가 ingress topic append를 완료한 수입니다.
- `DB 저장`은 Worker가 PostgreSQL commit까지 완료한 수입니다.
- `총 소요시간`은 전송 시작부터 현재 run의 DB 저장 완료까지 걸린 시간입니다.
- Worker 표시는 현재 replica와 최대 replica입니다. 예: `2/8`, `6/8`.
- demo-lite에서는 DB 저장 확인 polling도 DB connection budget에 포함됩니다.
- 대량 샘플 전송 시 `/v1/event-requests/{request_id}` 확인은 느린 batch와 제한된 동시성으로 수행해야 Pgpool slot 고갈을 피할 수 있습니다.

`Demo event DB reset`:
- 화면에서 `RESET DEMO DB`를 입력한 뒤 실행합니다.
- API endpoint는 `POST /v1/admin/demo/reset-events`입니다.
- 사용자 계정은 유지하고 데모 주문 stream, messages, request status, idempotency state, notification attempt 데이터를 초기화합니다.
- `message-ingress-dlq` topic도 삭제 후 다시 만들어 DLQ summary를 함께 비웁니다.
- 로컬 / 개발 / 테스트 성격의 `APP_ENV`에서만 허용합니다.
- 포트폴리오 시연 전 누적된 데모 이벤트를 비울 때 사용합니다.

## 접근 정책
현재 운영 경로는 일반 서비스 경로와 구분하되, 포트폴리오 데모 기준으로 쉽게 접근할 수 있게 유지합니다.

- API
  - 서비스 경로로 취급합니다.
  - bearer token 기반 인증이 적용됩니다.
- Grafana
  - 운영 UI 취급
  - anonymous Viewer로 데모 접근 허용
  - admin credential은 secret로 유지
  - 실서비스 별도 접근 제한 필요
- Prometheus
  - 운영 / 관측 UI로 취급합니다.
  - 현재는 데모 편의를 위해 ingress로 직접 접근 가능하게 둡니다.
  - 실서비스에서는 내부망, VPN, basic auth, SSO 같은 제한이 필요합니다.

현재 목적:
- 운영 경로와 일반 경로를 구분하고 있다는 점을 보여줍니다.
- 동시에 데모 중 Grafana / Prometheus를 직접 확인할 수 있는 경로는 유지합니다.

## Secret 처리
현재 민감한 값은 코드나 매니페스트 하드코딩 대신 Kubernetes secret로 분리합니다.

현재 분리된 값:
- `AUTH_SECRET_KEY`
- `ACCESS_TOKEN_TTL_SECONDS`
- `GRAFANA_ADMIN_USER`
- `GRAFANA_ADMIN_PASSWORD`

현재 방식:
- 로컬 kind 기준에서는 `messaging-runtime-secrets`를 생성해 주입합니다.
- 앱과 운영 UI는 이 secret를 환경변수로 읽습니다.

운영 확장 방향:
- local: Kubernetes secret
- staging / prod: 외부 secret manager 또는 배포 파이프라인 연동 secret 관리

로컬 검증에서는 Kubernetes Secret을 사용하고, 운영형 환경에서는 외부 secret manager로 확장할 수 있습니다.

## TLS 기준
현재 ingress TLS는 local self-signed certificate 기반입니다.

현재 목적:
- 로컬에서도 TLS termination 구조를 확인할 수 있게 합니다.
- API / Grafana / Prometheus가 같은 ingress 아래에서 열리는 구성을 HTTP 기준으로 운영하고, 필요할 때만 HTTPS로 TLS 동작을 검증합니다.

운영 확장 방향:
- local: self-signed certificate
- actual deployment: trusted certificate, `cert-manager`, 또는 cloud-managed certificate

즉 현재 TLS는 로컬 검증용 구현이고, 운영 단계에서는 신뢰된 인증서 체계로 바꾸는 것이 다음 단계입니다.

## 현재 운영 기준
현재 상태는 아래처럼 정리할 수 있습니다.

- runtime secret 분리: 적용됨
- 수동 PostgreSQL backup: 구현 및 검증 완료
- backup 기반 restore: 구현 및 검증 완료
- 주 1회 backup `CronJob`: HA 매니페스트에 포함 및 클러스터 적용 완료
- 운영 UI 접근 제한: 로컬 검증 기준으로 접근 가능하게 유지

## Kafka 운영 시나리오
현재 event intake는 Kafka append 성공을 write-path 수락 기준으로 봅니다.

- Kafka bootstrap 또는 topic append가 불가능하면 API는 새 write request를 fail-fast로 거절합니다.
- Worker는 Kafka consumer group으로 ingress topic을 처리하고, 재시도 한계를 넘은 event는 DLQ topic으로 이동합니다.
- Worker autoscaling은 KEDA Kafka lag 기준으로 동작합니다.

## Kafka persistence 정책
- Kafka는 accepted write를 순서 있는 commit log로 보관합니다.
- 최종 영속 저장소는 PostgreSQL입니다.
- 같은 stream은 같은 Kafka key를 사용해야 하며, 같은 key는 같은 partition에 들어가므로 partition 내부 순서가 유지됩니다.
- PostgreSQL 영속화 이전 구간의 내구성은 Kafka topic replication factor와 `acks` 정책에 의해 결정됩니다.

## Kafka fail-fast 정책
- Kafka bootstrap unreachable이면 write path failure로 봅니다.
- Kafka topic append 실패도 write path failure로 봅니다.
- Kafka 장애 동안에는 API가 새 write request를 계속 받지 않고 fail-fast 상태로 응답합니다.
- 즉, enqueue 불가 상태를 soft failure가 아니라 write path failure로 취급합니다.

## readiness / alert 운영 기준
- readiness는 `ready`, `degraded`, `not_ready`를 즉시 반영합니다.
- replica count와 standby count는 degraded 판단 기준으로 사용합니다.
- PostgreSQL degraded는 primary write 불가, standby 부족, replication state 불안정, replication lag 상승을 포함합니다.
- PostgreSQL primary loss 중에도 Kafka append path가 살아 있으면 API readiness는 `degraded`입니다.
- 로컬 데모에서는 async streaming standby를 정상 ready 상태로 봅니다.
- `30초`는 alert 승격 유예이며 readiness 지연에는 사용하지 않습니다.
- Kafka outage와 PostgreSQL primary loss는 즉시 critical로 봅니다.

자세한 상태 모델과 응답 예시는 [RELIABILITY_POLICY.md](RELIABILITY_POLICY.md)에서 함께 관리합니다.

## 운영 확장 포인트
- 운영 UI 접근 정책 강화
- secret 외부화 방향 정리
- incident 대응 절차는 [RUNBOOK.md](RUNBOOK.md)에서 관리

## DLQ 운영 기준

DLQ는 단순한 실패 보관소가 아니라, replay 가능한 장애 복구 경로입니다.

현재 정책:

- Worker는 retry 한도를 넘긴 event를 Kafka DLQ topic으로 보냅니다.
- DLQ payload에는 `failed_reason`, `retry_count`, `replay_count`, `failed_at`이 포함됩니다.
- `GET /v1/dlq/ingress`는 최근 DLQ event를 운영자가 읽기 쉬운 요약 형태로 반환합니다.
- `POST /v1/dlq/ingress/replay`는 request id 기준으로 replay 가능한 DLQ event를 ingress topic에 재투입합니다.
- DLQ Replayer는 PostgreSQL writable path가 복구된 뒤 replay 가능한 event를 ingress topic으로 재주입합니다.
- `DLQ_REPLAY_MAX_COUNT` 기본값은 `3`입니다.
- `replay_count`가 최대값 이상이면 replayer는 해당 event를 다시 ingress topic에 넣지 않습니다.
- 데모 화면의 `수동 재처리` 버튼은 DLQ log 삭제가 아니라 재투입 요청입니다.
- 데모 화면의 `전체 수동 재처리` 버튼은 replay 가능한 DLQ event를 일괄 재투입 요청합니다.
- 일괄 재처리 실패 또는 replay guard 도달 event는 사용자 확인 대상으로 남깁니다.

확인 순서:

1. `GET /v1/dlq/ingress?limit=20`으로 최근 실패 event를 확인합니다.
2. `failed_reason`이 일시 장애인지 데이터 조건 문제인지 구분합니다.
3. `replay_count`가 `max_replay_count`에 도달했는지 확인합니다.
4. replay 가능한 event가 여러 건이면 데모 화면 `전체 수동 재처리`로 먼저 재투입합니다.
5. 개별 확인이 필요한 event는 `수동 재처리` 또는 `POST /v1/dlq/ingress/replay`로 재투입합니다.
6. 같은 reason으로 반복되면 replay보다 원인 수정이 먼저입니다.
7. PostgreSQL / Pgpool 복구 후 replayer log에서 재주입 여부를 확인합니다.

같은 stream 순서 보장 기준:

- Worker는 transient persistence failure 시 같은 Kafka offset에서 inline retry를 수행합니다.
- 앞 event가 처리되거나 DLQ로 이동하기 전까지 같은 partition의 뒤 event는 앞지르지 못합니다.
- 앞 event가 최종 DLQ가 되면 운영자는 DLQ reason을 확인한 뒤 replay 여부를 결정합니다.

## 보안 기본선

현재 보안 기준은 로컬 포트폴리오 검증과 운영형 확장 방향을 분리해서 봅니다.

현재 적용:

- 사용자 비밀번호: `pbkdf2_sha256` hash 저장
- API 인증: bearer token 기반
- `AUTH_SECRET_KEY`, token TTL, Grafana credential: `messaging-runtime-secrets` 분리
- `.env`: Git 추적 대상 제외
- Grafana anonymous Viewer: 데모 대시보드 조회용
- local Grafana 기본 비밀번호: 데모용 admin credential, 운영 credential 제외

운영형 확장 기준:

- `AUTH_SECRET_KEY` 기본값 `dev-secret-change-me`는 local 외 환경에서 사용하지 않습니다.
- Grafana / Prometheus는 public ingress로 직접 열지 않고 내부망, VPN, SSO, basic auth 같은 접근 제한을 둡니다.
- secret은 Kubernetes Secret에서 외부 secret manager로 확장합니다.
- credential rotation은 배포 파이프라인에서 관리합니다.

### DLQ Summary API

운영자는 개별 event를 뒤지기 전에 summary endpoint로 DLQ 상태를 먼저 봅니다.

```powershell
Invoke-RestMethod -Headers @{ Authorization = "Bearer <token>" } http://localhost/v1/dlq/ingress/summary?limit=200&sample_limit=5
```

응답의 핵심 필드:

| Field | 의미 |
| --- | --- |
| `total` | 최근 조회 범위 안의 DLQ event 수 |
| `replayable` | 자동 replay 대상이 될 수 있는 event 수 |
| `blocked` | `DLQ_REPLAY_MAX_COUNT`에 도달했거나 replay 대상에서 제외된 event 수 |
| `oldest_age_seconds` | 조회 범위에서 가장 오래된 DLQ event age |
| `by_reason` | `failed_reason`별 count |
| `by_stream` | stream별 DLQ count |
| `recent_samples` | 최근 DLQ event 샘플 |

`blocked > 0`이면 replay보다 원인 수정이 먼저입니다. `by_reason`이 같은 값으로 몰리면 일시 장애보다 데이터 조건 또는 persistence logic 문제를 우선 의심합니다.

## API 계약과 운영 노출 기준

핵심 운영 endpoint는 FastAPI `response_model`로 응답 계약을 고정합니다. 현재 고정된 계약은 readiness, event request status, DLQ list, DLQ summary, DLQ replay, unread count, read receipt입니다.

운영 노출 기준:

| Surface | 로컬 포트폴리오 | 운영 전환 기준 |
| --- | --- | --- |
| API | ingress로 공개 | 인증 endpoint 외에는 JWT 필요, public path 최소화 |
| Grafana | ingress로 공개 | SSO, basic auth, VPN 또는 사설망 뒤에 배치 |
| Prometheus | ingress로 공개 | 직접 public 노출 금지, Grafana 또는 내부망에서만 접근 |
| DLQ API | JWT 필요 | 운영자 권한 분리, replay/blocked 판단 로그 보존 |
| Metrics | `/metrics` scrape | 외부 공개 금지, cluster-local scrape 우선 |

이 프로젝트의 로컬 ingress 노출은 포트폴리오 검증 편의를 위한 설정입니다. 실제 운영형 배포에서는 ingress class, auth proxy, network policy, secret manager를 함께 적용하는 것을 기준으로 둡니다.

## OpenAPI 사용 설명서

FastAPI는 핵심 API 계약을 `/openapi.json`으로 공개하고, 사람이 보는 문서는 `/docs`에서 제공합니다.

| Endpoint | 용도 |
| --- | --- |
| `/docs` | Swagger UI 기반 API 사용 설명서 |
| `/openapi.json` | client generator와 테스트가 읽는 OpenAPI schema |

운영 API를 변경할 때는 route, request model, `response_model`, API contract script, OpenAPI schema test를 함께 갱신합니다. 이렇게 해야 실제 응답과 공용 사용 설명서가 같은 계약을 유지합니다.
