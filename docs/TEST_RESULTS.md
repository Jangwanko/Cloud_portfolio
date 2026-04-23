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
| KEDA worker scaling | live `k6` + `worker-keda-hpa` observation | Pass | Worker replica `2 -> 4 -> 6 -> 8` scale-out 확인 |
| Full quick start | `scripts/quick_start_all.ps1` | Pass | fresh kind cluster 기준 smoke, DB, Redis, API HPA, Worker KEDA 포함 |
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

### KEDA Worker Scaling
- Worker KEDA scale-up observed during live `k6` load:
  - `02:08:39`: `worker=2`, `hpa=2`, queue depth signal `0`
  - `02:09:10`: `worker=4`, `hpa_desired=4`
  - `02:09:42`: `worker=6`, `hpa_desired=6`
  - `02:10:13`: `worker=8`, `hpa_desired=8`
- Grafana `Worker Replicas` 패널과 `kubectl get hpa worker-keda-hpa -n messaging-app` 결과가 같은 흐름을 보였습니다.
- 이번 검증으로 Worker autoscaling은 CPU HPA가 아니라 queue depth 기반 KEDA 경로가 실제로 동작함을 확인했습니다.

### Quick Start
- `scripts/quick_start_all.ps1` recent successful run:
  - smoke test: pass
  - DB recovery test: pass
  - Redis complete outage test: pass
  - API HPA scaling test: pass
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
`scripts/test_k6_load.ps1`는 현재 실행 경로 자체는 정상입니다. latency threshold는 성능 개선 과제로 추적합니다.

성능 개선 이력:

| 단계 | 처리 요청 수 | 평균 응답시간 | p95 응답시간 | Error Rate | 반영 여부 | 변경사항 |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| 초기 기준 | `5,434 req` | `3,660 ms` | `8,175 ms` | - | 기준선 | HA kind 환경에서 k6 성능 기준선을 측정했습니다. |
| 1차 개선 | `7,966 req` | `2,285 ms` | `4,936 ms` | - | 반영 | event intake 전 stream membership 확인을 Redis cache 우선으로 바꿔 DB 조회 round-trip을 줄였습니다. |
| 2차 개선 | `9,102 req` | `1,934 ms` | `3,851 ms` | - | 반영 | request status 저장과 queue push를 Redis pipeline으로 묶고, idempotency hot path에서 불필요한 추가 조회를 줄였습니다. |
| pgpool / DB pool 조정 | `11,314 req` | `1,519 ms` | `3,333 ms` | - | 반영 | API DB pool 크기와 pgpool connection / resource 설정을 조정해 connection 병목, `too many clients`, OOM 가능성을 완화했습니다. |
| Redis hot path 실험 A | `16,024 req` | `1,011.08 ms` | `2,219.59 ms` | `0.00%` | 현재 최종 | 요청마다 `get_redis()`가 수행하던 확인용 `PING` round-trip을 제거하고, 실패 시 reconnect하는 방식으로 변경했습니다. |
| API worker 실험 B | `20,055 req` | `797.81 ms` | `3,088.99 ms` | `5.82%` | 미채택 | 실험 A에 `UVICORN_WORKERS=2`를 추가했습니다. 처리량과 평균 응답시간은 개선됐지만 error rate와 p95가 악화되어 최종 반영하지 않았습니다. |
| KEDA worker scaling 적용 후 | `19,528 req` | `811.01 ms` | `1,953.64 ms` | `0.00%` | 현재 운영 기준 | Worker autoscaling을 CPU HPA에서 KEDA queue depth scaling으로 전환했습니다. live load 중 worker replica가 `2 -> 4 -> 6 -> 8`까지 올라갔고, Grafana `Worker Replicas` 패널에서도 desired / available 흐름을 직접 확인할 수 있습니다. |

참고:
- `7435 req`, avg `2459.75ms`, p95 `5092.95ms` 결과는 `5461b2f` 시점의 과거 단일 k6 확인 결과입니다.
- 위 성능 개선 표의 기준선과 차수 실험은 이후 `093317e`, `5e6ba0a` 기준으로 정리했습니다.

현재 해석:
- 실행 오류가 아니라 성능 튜닝 대상입니다.
- 즉 load test infrastructure는 동작하며, latency tuning은 후속 개선 과제로 남아 있습니다.
- Redis command hot path의 불필요한 round-trip 제거는 효과가 있었고, 다음 실험은 API worker 병렬성 증가 효과를 분리해서 확인하는 것이 맞습니다.
- `UVICORN_WORKERS=2`는 로컬 kind HA 환경에서 tail latency와 error rate를 악화시켜 채택하지 않았고, 최종 코드는 실험 A 상태로 되돌렸습니다.
- Worker autoscaling을 queue depth 기반 KEDA로 전환한 뒤에는, 같은 로컬 kind 환경에서도 worker replica가 실제 backlog에 반응해 `8`개까지 scale-out 되는 것을 확인했습니다.
- 다만 latency threshold 초과가 완전히 사라진 것은 아닙니다. 현재 임계 초과는 설계 실패라기보다 local kind 환경에서 HA, async buffering, ordering, PostgreSQL persistence 비용이 함께 드러나는 상태로 해석하는 것이 맞습니다.

## Current Interpretation
- 기능 검증 경로는 현재 저장소 상태에서 다시 재현 가능합니다.
- autoscaling은 API는 CPU HPA, Worker는 KEDA queue depth scaling으로 실제 동작을 확인했습니다.
- ingress 기반 외부 진입은 기본적으로 `http://localhost` 기준으로 검증합니다.
- backup / restore도 수동 운영 경로 기준으로 실제 검증했습니다.
- Redis는 complete outage와 HA failover를 별도 시나리오로 분리해 검증합니다.
- 현재 가장 큰 남은 과제는 `k6` latency threshold 추가 개선과 운영 정책 고도화입니다.

## 신뢰성 정책 기준 해석
- Redis complete outage 시 기대 상태는 `not_ready`입니다.
- Redis single-node failover 시 기대 상태는 곧바로 `not_ready`로 고정되는 것이 아니라, writable master가 유지되면 `degraded`를 거쳐 회복하는 흐름입니다.
- PostgreSQL primary가 write 불가 상태가 되어도 Redis enqueue가 가능하면 기대 상태는 `degraded`입니다.
- 이 경우 API는 새 요청을 `accepted` 하고, PostgreSQL 복구 후 worker가 영속화하는 것이 이 프로젝트의 핵심 설계입니다.
- Redis replica 부족, Redis link 불안정, PostgreSQL standby 부족, PostgreSQL replication state 이상은 `degraded` 기준으로 해석합니다.
- 로컬 데모에서는 async streaming standby를 정상 ready 상태로 봅니다.
- `30초`는 readiness 유예가 아니라 alert 승격 유예로 해석합니다.

즉, 이후 검증에서는 단순 성공 / 실패뿐 아니라 `ready / degraded / not_ready` 전환이 정책대로 나타나는지도 함께 보는 것이 맞습니다.
