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
- Kubernetes HPA
  - API / Worker CPU 기반 autoscaling
- metrics-server
  - HPA용 resource metrics 제공
- ingress-nginx
  - 로컬 kind 환경의 HTTP / HTTPS ingress 진입
- Runtime Secrets
  - auth key와 운영 credential 분리
- PostgreSQL Backup / Restore
  - 수동 logical backup
  - backup본 기반 restore
  - 주 1회 backup `CronJob`

## 외부 진입
현재 로컬 검증 기준의 기본 진입점은 아래와 같습니다.

- API: `http://localhost`
- TLS API: `https://localhost`
- Grafana: `http://localhost/grafana`
- TLS Grafana: `https://localhost/grafana`
- Prometheus: `http://localhost/prometheus/`
- TLS Prometheus: `https://localhost/prometheus/`

Service는 `ClusterIP`로 두고, 외부 요청은 `ingress-nginx`가 받아 각 서비스로 라우팅합니다. 로컬 HTTPS는 self-signed certificate 기반으로 종료합니다.

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
- API는 Redis queue로 요청을 계속 수락할 수 있습니다.
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
현재 autoscaling은 CPU 기반 HPA로 구성되어 있습니다.

- API HPA
  - min replicas: `3`
  - max replicas: `8`
  - target CPU: `65%`
- Worker HPA
  - min replicas: `2`
  - max replicas: `4`
  - target CPU: `70%`

최근 검증에서는 API replica가 `3 -> 5`, `3 -> 6`으로 scale-up 되는 것을 확인했습니다.

## Observability
현재 관측 가능한 항목:
- API request count / latency
- worker processing count / latency
- queue depth
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
- HTTPS는 local self-signed certificate 기반이라 브라우저 경고가 발생할 수 있습니다.
- `k6`는 실행 자체는 정상이지만 latency threshold는 아직 미통과입니다.
- 멀티 파드 환경에서 stream 단위 event 순서 보장 검증은 추가 작업이 필요합니다.
- 운영 UI는 면접용 데모 흐름을 위해 비교적 열려 있는 상태입니다.
