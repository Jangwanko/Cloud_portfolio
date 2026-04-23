# Architecture

## 구성 요소
- API (`FastAPI`)
  - transaction 요청 수락
  - Redis ingress queue 적재
  - health / readiness / metrics 노출
- Worker
  - Redis queue 소비
  - PostgreSQL 영속화
  - retry / DLQ 처리
- DLQ Replayer
  - DLQ 적재 요청 재투입
- PostgreSQL HA
  - `bitnami/postgresql-ha` 기반
  - pgpool 경유 접근
- Redis HA
  - replica + Sentinel 구성
- Prometheus / Grafana
  - metrics 수집, alert, dashboard
- Kubernetes autoscaling
  - API CPU 기반 HPA
  - Worker KEDA queue depth scaling
- metrics-server
  - HPA용 resource metrics 제공
- ingress-nginx
  - 로컬 kind 환경의 ingress 진입
- Runtime Secrets
  - auth key와 운영 credential 분리
- PostgreSQL Backup / Restore
  - 수동 logical backup
  - backup본 기반 restore
  - 주 1회 backup `CronJob`

## 외부 진입
현재 로컬 검증 기준의 기본 진입점은 아래와 같습니다.

- API: `http://localhost`
- Grafana: `http://localhost/grafana`
- Prometheus: `http://localhost/prometheus/`

Service는 `ClusterIP`로 두고, 외부 요청은 `ingress-nginx`가 받아 각 서비스로 라우팅합니다. 기본 문서와 데모 경로는 HTTP 기준이며, HTTPS는 self-signed certificate 기반 TLS 종료를 확인하는 보조 경로입니다.

## 요청 처리 흐름
1. 클라이언트가 API로 transaction 요청을 보냅니다.
2. API는 요청을 바로 DB에 쓰지 않고 Redis ingress queue에 적재합니다.
3. Worker가 queue에서 요청을 가져와 PostgreSQL에 영속화합니다.
4. 실패하면 retry를 수행합니다.
5. retry 한도를 넘기면 DLQ로 이동합니다.
6. DLQ Replayer가 복구 조건이 맞으면 다시 ingress queue로 재투입합니다.

## 인증 / 인가
현재 최소 범위의 인증 / 인가가 적용되어 있습니다.

- 사용자 생성 시 `password_hash` 저장
- `/v1/auth/login`으로 bearer token 발급
- 주요 API는 로그인 사용자 기준으로 처리
- stream membership 검증 적용

중요한 점:
- 인증은 token payload 기준으로 처리해서 DB down 중에도 인증 경로 자체 때문에 요청이 막히지 않도록 했습니다.
- DB down 수락 경로를 유지하기 위해 stream membership 일부를 Redis에 캐시합니다.

## 장애 시나리오별 동작

### DB down
- Redis queue 기반 구조라 API 프로세스 내부의 enqueue 경로는 DB write와 분리되어 있습니다.
- PostgreSQL writable primary가 없더라도 Redis enqueue가 가능하면 API readiness는 `degraded`로 유지합니다.
- 이때 서비스는 새 요청을 `accepted` 할 수 있고, worker는 DB 복구 후 밀린 요청을 영속화합니다.
- persistence path가 막힌 상태이므로 운영 alert에서는 critical로 다룹니다.
- Worker는 DB 쓰기 실패 시 retry를 수행합니다.
- retry 한도를 넘긴 요청은 DLQ로 이동합니다.
- DB recovery 후 pgpool-backed query가 안정화되면 worker와 replayer가 다시 영속화를 진행합니다.

### Redis down
- complete outage 기준에서는 API가 queue 적재를 할 수 없어 event intake 실패가 증가합니다.
- readiness는 `not_ready`로 내려갈 수 있습니다.
- Worker는 queue 소비를 중단합니다.
- Redis recovery 후 queue 처리와 readiness가 정상화됩니다.

### Redis failover
- Redis pod 하나가 재시작되더라도 Sentinel과 replica 구성이 살아 있으면 전체 outage와는 다른 시나리오로 봅니다.
- 이 경우 핵심 검증 포인트는 "일시 흔들림 이후 readiness와 event intake가 계속 복구되는가"입니다.
- 현재는 `scripts/test_redis_failover.ps1`로 단일 pod 재시작 기준 failover 흐름을 별도로 검증합니다.

### Worker backlog
- API는 요청을 계속 수락하지만 queue depth가 증가합니다.
- Worker replica 증가 또는 부하 감소 시 backlog가 다시 줄어듭니다.

## Autoscaling
현재 autoscaling은 API와 Worker가 서로 다른 기준을 사용합니다.

- API HPA
  - min replicas: `3`
  - max replicas: `8`
  - target CPU: `65%`
- Worker KEDA
  - min replicas: `2`
  - max replicas: `8`
  - trigger: total Redis ingress queue depth
  - query: `sum(max by (queue) (messaging_queue_depth{job="api",queue=~"message_ingress:p.*"}))`
  - threshold: `400`

최근 검증에서는 아래를 확인했습니다.

- API replica: `3 -> 5`, `3 -> 6`
- Worker replica: `2 -> 4 -> 6 -> 8`

Worker를 CPU가 아니라 queue depth 기준으로 스케일링한 이유는, 이 프로젝트의 병목이 pure CPU보다 Redis backlog와 PostgreSQL persistence 대기에서 먼저 드러나기 때문입니다.

## Observability
현재 관측 가능한 항목:
- API request count / latency
- worker processing count / latency
- queue depth
- worker replica count / KEDA desired replicas
- Redis role / replica count / replica link / Sentinel master 상태
- PostgreSQL primary / standby / replication state / replication delay
- DB / Redis / Worker health
- Prometheus alert firing / resolution

현재 검증한 alert 흐름:
- DB outage alert
- Redis outage alert
- recovery 후 alert resolution

## Backup and Restore
현재 PostgreSQL 운영 보강은 아래처럼 구성되어 있습니다.

- 수동 backup
  - `scripts/backup_postgres_k8s.ps1`
  - `pgpool` 경유 `pg_dump`
  - 결과는 로컬 `backups/`에 저장
- restore
  - `scripts/restore_postgres_k8s.ps1`
  - 기존 backup SQL을 다시 적용
  - `-Force` 필수
  - 필요 시 `-ResetSchema` 지원
- 주기 backup
  - HA 매니페스트에 `postgres-weekly-backup` `CronJob`
  - 스케줄: `0 3 * * 0`
  - cluster PVC `postgres-backups` 사용

## 현재 한계
- HTTPS는 local self-signed certificate 기반의 TLS 검증용 보조 경로입니다.
- `k6`는 실행 경로와 측정 체계를 갖추었고, latency threshold는 성능 개선 과제로 추적합니다.
- 멀티 파드 환경에서 stream 단위 event 순서 보장 검증은 추가 작업이 필요합니다.
- 운영 UI는 로컬 포트폴리오 검증을 위해 접근 가능하게 구성되어 있으며, 실제 운영에서는 접근 제한이 필요합니다.

## 신뢰성 상태 모델
이번 기준에서 readiness는 단순 up/down이 아니라 실제 역할과 topology 상태를 함께 반영합니다.

### `ready`
- Redis writable master reachable
- Redis Sentinel master resolved
- Redis replica count `2+`
- Redis replica link 정상
- PostgreSQL writable primary reachable
- PostgreSQL standby count `2+`
- PostgreSQL standby replication state `streaming`
- PostgreSQL replication lag 정상

### `degraded`
- Redis master는 writable이지만 replica count가 `1` 이하
- Redis replica 중 `master_link_status`가 비정상
- Sentinel은 master를 찾았지만 quorum 또는 topology가 불안정
- Redis enqueue는 가능하지만 PostgreSQL writable primary가 일시적으로 unavailable
- PostgreSQL primary는 writable이지만 standby count가 `1` 이하
- PostgreSQL replication state가 불안정
- PostgreSQL replication lag가 임계치 초과

### `not_ready`
- Redis writable master unreachable
- Redis Sentinel master unresolved
- Redis 인증 / 연결 실패로 enqueue 불가

## readiness와 alert 해석
- readiness는 현재 사실을 즉시 반영합니다.
- `30초`는 readiness 유예가 아니라 alert 승격 유예입니다.
- 짧은 failover 흔들림 동안에는 `degraded`를 바로 노출하되, warning을 `30초` 유지한 뒤 필요 시 승격합니다.
- Redis total outage는 intake write path 중단이므로 즉시 critical로 해석합니다.
