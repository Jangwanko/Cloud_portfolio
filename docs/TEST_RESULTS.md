# Test Results

현재 저장소 상태에서 검증한 결과와 신뢰성 정책 기준 해석을 함께 정리한 문서입니다.

## Current Live Check

최근 라이브 클러스터에서 다시 확인한 현재 상태:
- `/health/ready`: `ready`
- Redis: writable master reachable, replica `2`, Sentinel master resolved
- PostgreSQL: writable primary reachable, standby `2`, sync standby `0`
- 로컬 데모 정책: async streaming standby는 `ready`로 해석
- Prometheus active alerts: 없음
- Grafana datasource: Prometheus 연결 정상

## Verified Scenarios

| Scenario | Script | Result | Notes |
| --- | --- | --- | --- |
| Smoke test | `scripts/smoke_test.ps1` | Pass | 요청 생성, 영속화, 읽음 처리, unread count 확인 |
| DB outage and recovery | `scripts/test_db_down.ps1` | Pass | PostgreSQL primary loss 중 Redis enqueue 가능 시 `degraded`로 수락하고, 복구 후 `persisted` 확인 |
| Redis complete outage and recovery | `scripts/test_redis_down.ps1` | Pass | 전체 Redis 접근 불가 시 event intake 실패, 복구 후 다시 `accepted` 확인 |
| Redis single-node failover | `scripts/test_redis_failover.ps1` | Pass | Redis pod 하나 재시작 후 readiness 복구와 event intake 유지 확인 |
| DLQ flow | `scripts/test_dlq_flow.ps1` | Pass | 실패 요청의 DLQ 적재와 재처리 흐름 확인 |
| Failover and alert validation | `scripts/test_failover_alerts.ps1` | Pass | Prometheus alert firing / resolution 확인 |
| HPA scaling | `scripts/test_hpa_scaling.ps1` | Pass | API replica scale-up 확인 |
| Full quick start | `scripts/quick_start_all.ps1` | Pass | fresh kind cluster 기준 smoke, DB, Redis, HPA 포함 |
| PostgreSQL backup | `scripts/backup_postgres_k8s.ps1` | Pass | SQL dump 파일 생성 확인 |
| PostgreSQL restore | `scripts/restore_postgres_k8s.ps1` | Pass | backup SQL 적용 후 readiness 정상 확인 |

## Recent Measured Results

### Failover and Alert
- DB outage + recovery time: about `102s`
- Redis outage + recovery time: about `114s`

### Redis Scenario Split
- complete outage:
  - `scripts/test_redis_down.ps1`
  - readiness가 `not_ready`로 내려가면 같이 확인
  - 핵심 기준은 event intake 실패와 복구 후 재수락
- single-node failover:
  - `scripts/test_redis_failover.ps1`
  - Redis pod 하나 재시작 후 readiness 복구와 event intake 유지 확인

### HPA Scaling
- API HPA scale-up observed:
  - `initial_replicas=3 -> max_replicas=5`, `cpu_target=88`
  - `initial_replicas=3 -> max_replicas=6`, `cpu_target=138`
  - recent quick start run: `initial_replicas=3 -> max_replicas=6`, `cpu_target=241`

### Quick Start
- `scripts/quick_start_all.ps1` recent successful run:
  - smoke test: pass
  - DB recovery test: pass
  - Redis complete outage test: pass
  - HPA scaling test: pass
  - ingress readiness at `http://localhost/health/ready`: pass
  - TLS readiness at `https://localhost/health/ready`: pass, TLS 보조 검증

### Ingress UI Endpoints
- Grafana UI:
  - `http://localhost/grafana`
- Prometheus UI:
  - `http://localhost/prometheus/`

참고:
- 문서와 데모의 기본 경로는 HTTP입니다.
- HTTPS는 local self-signed TLS 검증용 보조 경로입니다.

## Backup and Restore Verification

### Backup
- command:
  - `powershell -ExecutionPolicy Bypass -File scripts/backup_postgres_k8s.ps1`
- verified output:
  - `backups/postgres-20260416-131433.sql`
  - `backups/postgres-20260416-163842.sql`

### Weekly Backup Schedule
- cluster resource:
  - `CronJob/postgres-weekly-backup`
- schedule:
  - `0 3 * * 0`
- backing storage:
  - `PVC/postgres-backups`

주의:
- 현재 포트폴리오 기준에서는 “주기 backup 설정이 포함되어 있다”는 점을 보여주는 용도입니다.
- 수동 backup 스크립트가 실제 dump 생성까지 검증된 경로입니다.

### Restore
- command:
  - `powershell -ExecutionPolicy Bypass -File scripts/restore_postgres_k8s.ps1 -Namespace messaging-app -BackupFile backups/postgres-20260416-163842.sql -ResetSchema -Force`
- observed behavior:
  - schema reset 수행
  - `CREATE TABLE`, `COPY`, `ALTER TABLE` 진행
  - restore 완료 후 API readiness 정상 응답

## k6 Load Test
`scripts/test_k6_load.ps1`는 현재 실행 경로 자체는 정상입니다. 다만 성능 threshold는 아직 통과하지 못합니다.

최근 기준 결과:
- total requests: `7435`
- error rate: `0.00%`
- average latency: `2459.75ms`
- p95 latency: `5092.95ms`

Redis hot path 실험 A 결과:
- change: `get_redis()`의 per-call `PING` 제거, `UVICORN_WORKERS=1` 유지
- image: `messaging-portfolio:exp-a-redis-no-ping`
- total requests: `16024`
- error rate: `0.00%`
- average latency: `1011.08ms`
- p95 latency: `2219.59ms`
- result: threshold failed, but throughput and latency improved materially

API worker 실험 B 결과:
- change: 실험 A 유지 + `UVICORN_WORKERS=2`
- image: `messaging-portfolio:exp-b-redis-no-ping-workers2`
- total requests: `20055`
- error rate: `5.82%`
- average latency: `797.81ms`
- p95 latency: `3088.99ms`
- result: throughput and average latency improved, but error rate and p95 latency regressed

현재 해석:
- 실행 오류가 아니라 성능 미달입니다.
- 즉 load test infrastructure는 동작하지만 latency tuning이 아직 필요합니다.
- Redis command hot path의 불필요한 round-trip 제거는 효과가 있었고, 다음 실험은 API worker 병렬성 증가 효과를 분리해서 확인하는 것이 맞습니다.
- `UVICORN_WORKERS=2`는 로컬 kind HA 환경에서 tail latency와 error rate를 악화시켜 채택하지 않았고, 최종 코드는 실험 A 상태로 되돌렸습니다.

## Current Interpretation
- 기능 검증 경로는 현재 저장소 상태에서 다시 재현 가능합니다.
- autoscaling(HPA)은 metrics-server 추가 후 실제 동작을 확인했습니다.
- ingress 기반 외부 진입은 기본적으로 `http://localhost` 기준으로 검증합니다.
- backup / restore도 수동 운영 경로 기준으로 실제 검증했습니다.
- Redis는 complete outage와 HA failover를 별도 시나리오로 분리해 검증합니다.
- 현재 가장 큰 남은 과제는 `k6` latency 개선과 운영 정책 고도화입니다.

## 신뢰성 정책 기준 해석
- Redis complete outage 시 기대 상태는 `not_ready`입니다.
- Redis single-node failover 시 기대 상태는 곧바로 `not_ready`로 고정되는 것이 아니라, writable master가 유지되면 `degraded`를 거쳐 회복하는 흐름입니다.
- PostgreSQL primary가 write 불가 상태가 되어도 Redis enqueue가 가능하면 기대 상태는 `degraded`입니다.
- 이 경우 API는 새 요청을 `accepted` 하고, PostgreSQL 복구 후 worker가 영속화하는 것이 이 프로젝트의 핵심 설계입니다.
- Redis replica 부족, Redis link 불안정, PostgreSQL standby 부족, PostgreSQL replication state 이상은 `degraded` 기준으로 해석합니다.
- 로컬 데모에서는 async streaming standby를 정상 ready 상태로 봅니다.
- `30초`는 readiness 유예가 아니라 alert 승격 유예로 해석합니다.

즉, 이후 검증에서는 단순 성공 / 실패뿐 아니라 `ready / degraded / not_ready` 전환이 정책대로 나타나는지도 함께 보는 것이 맞습니다.
