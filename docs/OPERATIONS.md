# 운영 지침

운영 관점 정리 범위:

- secret
- backup / restore
- 운영 UI 경로
- 데모 운영 작업
- Kafka / DLQ / 보안 기준

서비스 전체 프로세스 점검 순서: [SERVICE_PROCESS_CHECKLIST.md](SERVICE_PROCESS_CHECKLIST.md)

## Runtime secret
로컬 kind 기준 runtime secret 별도 생성.

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
- `scripts/quick_start_all.ps1`와 `k8s/scripts/setup-kind.ps1` 자동 실행
- Grafana 자격증명 매니페스트 하드코딩 제외
- API / Worker / DLQ replayer 동일 secret 사용

## PostgreSQL monitoring role
PostgreSQL HA 설치 후 `k8s/scripts/install-ha.ps1`는 `portfolio` 사용자에게 `pg_monitor` 역할을 부여합니다.

`pg_monitor` 용도:

- `pg_stat_replication` 읽기
- standby `state`, `sync_state`, replication lag 확인
- API Prometheus metric 노출

## PostgreSQL 백업
로컬 HA PostgreSQL logical backup 생성.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/backup_postgres_k8s.ps1
```

결과:
- `backups/postgres-<timestamp>.sql`

동작 방식:
- `messaging-postgresql-ha-postgresql` secret에서 DB password 조회
- `pgpool` 서비스 경유 `pg_dump` 수행
- 결과를 로컬 `backups/` 디렉터리에 저장

## 주간 백업 일정
HA 배포 포함 항목:

- 주 1회 PostgreSQL logical backup `CronJob`

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
- 현재 구성: 주기 backup 설정이 포함된 운영 흐름 검증
- 확장 방향: 일 단위 또는 더 짧은 주기 변경

## PostgreSQL 복구
logical backup을 현재 클러스터 DB에 적용.

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
- 기본 실행 차단, `-Force` 필수
- `-ResetSchema`: `public` schema 초기화 후 backup SQL 적용
- 현재 복구 절차: disposable local cluster 기준 운영 흐름 검증

## 데모 접근
로컬 데모 기준 운영 UI 경로:
- Grafana overview: `http://localhost/grafana/d/messaging-portfolio-overview/messaging-portfolio-operations-overview?orgId=1&refresh=5s`
- Grafana 로그인: `ID admin` / `비밀번호 1q2w3e4r`
- Prometheus: `http://localhost/prometheus/`

참고:
- 기본 운영 문서와 데모 경로: `http://localhost`
- HTTPS: local self-signed certificate 기반 TLS 검증용 보조 경로
- 브라우저 보안 경고: 최초 1회 가능

## 데모 운영 작업

데모 화면 운영자 이벤트 큐 목적:

- 실제 Kafka / Worker / PostgreSQL 흐름 표시
- 로컬 운영 패널

`전송 전 예약 비우기`:
- 아직 Kafka로 보내지 않은 `reserved` 이벤트만 취소
- 이미 API 전송이 시작된 작업 취소 제외
- 시작된 작업 Kafka 적재와 DB 저장까지 계속 추적
- 버튼 클릭 시점 전송 중 이벤트: `sending` 상태 분리, 예약 취소 대상 제외

데모 counter 기준:
- `예약 건수`: 전송 시작 후 `남은 예약/전체 예약`, Kafka append 성공 시 감소
- `Kafka 적재`: API ingress topic append 완료 수
- `DB 저장`: Worker PostgreSQL commit 완료 수
- `총 소요시간`: 전송 시작부터 현재 run의 DB 저장 완료까지 걸린 시간
- Worker 표시: 현재 replica / 최대 replica. 예: `2/8`, `6/8`

`Demo event DB reset`:
- 화면에서 `RESET DEMO DB` 입력 후 실행
- API endpoint: `POST /v1/admin/demo/reset-events`
- 유지: 사용자 계정
- 초기화: 데모 주문 stream, messages, request status, idempotency state, notification attempt 데이터
- DLQ: `message-ingress-dlq` topic 삭제 후 재생성, DLQ summary 초기화
- 허용 환경: 로컬 / 개발 / 테스트 성격의 `APP_ENV`
- 사용 시점: 포트폴리오 시연 전 누적 데모 이벤트 초기화

## 접근 정책
현재 운영 경로는 일반 서비스 경로와 구분하되, 포트폴리오 데모 기준으로 쉽게 접근할 수 있게 유지합니다.

- API
  - 서비스 경로 취급
  - bearer token 기반 인증 적용
- Grafana
  - 운영 UI 취급
  - 로그인 유지 상태 노출
  - 데모 접근 허용
  - 실서비스 별도 접근 제한 필요
- Prometheus
  - 운영 / 관측 UI 취급
  - 데모 편의상 ingress 직접 접근 허용
  - 실서비스 내부망, VPN, basic auth, SSO 제한 필요

현재 목적:
- 운영 경로와 일반 경로 구분 표시
- 데모 중 Grafana / Prometheus 직접 확인 경로 유지

## Secret 처리
현재 민감한 값 처리:

- 코드 하드코딩 제외
- 매니페스트 하드코딩 제외
- Kubernetes secret 분리

현재 분리된 값:
- `AUTH_SECRET_KEY`
- `ACCESS_TOKEN_TTL_SECONDS`
- `GRAFANA_ADMIN_USER`
- `GRAFANA_ADMIN_PASSWORD`

현재 방식:
- 로컬 kind 기준: `messaging-runtime-secrets` 생성 후 주입
- 앱과 운영 UI: secret 환경변수 사용

운영 확장 방향:
- local: Kubernetes secret
- staging / prod: 외부 secret manager 또는 배포 파이프라인 연동 secret 관리

Secret 확장 기준:

- 로컬 검증: Kubernetes Secret
- 운영형 환경: 외부 secret manager

## TLS 기준
현재 ingress TLS:

- local self-signed certificate 기반

현재 목적:
- 로컬 TLS termination 구조 확인
- API / Grafana / Prometheus 같은 ingress 구성 확인
- 기본 운영: HTTP
- 필요 시 HTTPS TLS 동작 검증

운영 확장 방향:
- local: self-signed certificate
- actual deployment: trusted certificate, `cert-manager`, 또는 cloud-managed certificate

TLS 다음 단계:

- 로컬 검증용 구현 유지
- 운영 단계에서 신뢰된 인증서 체계 적용

## 현재 운영 기준
현재 상태:

- runtime secret 분리: 적용됨
- 수동 PostgreSQL backup: 구현 및 검증 완료
- backup 기반 restore: 구현 및 검증 완료
- 주 1회 backup `CronJob`: HA 매니페스트에 포함 및 클러스터 적용 완료
- 운영 UI 접근 제한: 로컬 검증 기준으로 접근 가능하게 유지

## Kafka 운영 시나리오
현재 event intake 기준:

- Kafka append 성공
- write-path 수락 판단

- Kafka bootstrap 또는 topic append 불가: API 새 write request fail-fast 거절
- Worker: Kafka consumer group으로 ingress topic 처리
- 재시도 한계 초과 event: DLQ topic 이동
- Worker autoscaling: KEDA Kafka lag 기준

## Kafka persistence 정책
- Kafka: accepted write를 순서 있는 commit log로 보관
- 최종 영속 저장소: PostgreSQL
- 같은 stream: 같은 Kafka key 사용
- 같은 key: 같은 partition 배치, partition 내부 순서 유지
- PostgreSQL 영속화 이전 내구성: Kafka topic replication factor와 `acks` 정책 기준

## Kafka fail-fast 정책
- Kafka bootstrap unreachable: write path failure
- Kafka topic append 실패: write path failure
- Kafka 장애 중 API: 새 write request fail-fast 응답
- enqueue 불가 상태: write path failure 취급

## readiness / alert 운영 기준
- readiness: `ready`, `degraded`, `not_ready` 즉시 반영
- degraded 판단: replica count와 standby count 사용
- PostgreSQL degraded: primary write 불가, standby 부족, replication state 불안정, replication lag 상승 포함
- PostgreSQL primary loss + Kafka append path 생존: API readiness `degraded`
- 로컬 데모: async streaming standby를 정상 ready 상태로 판단
- `30초`: alert 승격 유예, readiness 지연 제외
- Kafka outage와 PostgreSQL primary loss: 즉시 critical

자세한 상태 모델과 응답 예시: [RELIABILITY_POLICY.md](RELIABILITY_POLICY.md)

## 운영 확장 포인트
- 운영 UI 접근 정책 강화
- secret 외부화 방향 정리
- incident 대응 절차는 [RUNBOOK.md](RUNBOOK.md)에서 관리

## DLQ 운영 기준

DLQ 기준:

- 실패 event 격리
- replay 가능한 장애 복구 경로

현재 정책:

- Worker: retry 한도 초과 event를 Kafka DLQ topic으로 전송
- DLQ payload: `failed_reason`, `retry_count`, `replay_count`, `failed_at`
- `GET /v1/dlq/ingress`: 최근 DLQ event 요약 반환
- DLQ Replayer: PostgreSQL writable path 복구 후 replay 가능한 event를 ingress topic 재주입
- `DLQ_REPLAY_MAX_COUNT` 기본값: `3`
- `replay_count` 최대값 이상: replayer ingress topic 재주입 제외

확인 순서:

1. `GET /v1/dlq/ingress?limit=20`으로 최근 실패 event 확인
2. `failed_reason` 기준 일시 장애 / 데이터 조건 문제 구분
3. `replay_count`와 `max_replay_count` 도달 여부 확인
4. 같은 reason 반복 시 replay보다 원인 수정 우선
5. PostgreSQL / Pgpool 복구 후 replayer log에서 재주입 여부 확인

같은 stream 순서 보장 기준:

- Worker: transient persistence failure 시 같은 Kafka offset에서 inline retry 수행
- 앞 event 처리 또는 DLQ 이동 전: 같은 partition 뒤 event 추월 불가
- 앞 event 최종 DLQ: 운영자가 DLQ reason 확인 후 replay 여부 결정

## 보안 기본선

현재 보안 기준:

- 로컬 포트폴리오 검증
- 운영형 확장 방향
- 두 범위 분리

현재 적용:

- 사용자 비밀번호: `pbkdf2_sha256` hash 저장
- API 인증: bearer token 기반
- `AUTH_SECRET_KEY`, token TTL, Grafana credential: `messaging-runtime-secrets` 분리
- `.env`: Git 추적 대상 제외
- local Grafana 기본 비밀번호는 데모용이며 운영 credential이 아닙니다.

운영형 확장 기준:

- `AUTH_SECRET_KEY` 기본값 `dev-secret-change-me`: local 외 환경 사용 금지
- Grafana / Prometheus: public ingress 직접 노출 제외
- 접근 제한: 내부망, VPN, SSO, basic auth
- secret: Kubernetes Secret에서 외부 secret manager로 확장
- credential rotation: 배포 파이프라인 관리

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

핵심 운영 endpoint는 FastAPI `response_model`로 응답 계약을 고정합니다. 현재 고정된 계약은 readiness, event request status, DLQ list, DLQ summary, unread count, read receipt입니다.

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
