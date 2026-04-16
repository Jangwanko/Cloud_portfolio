# Test Results

현재 저장소 상태에서 최근 다시 확인한 검증 결과를 정리한 문서입니다.

## Verified Scenarios

| Scenario | Script | Result | Notes |
| --- | --- | --- | --- |
| Smoke test | `scripts/smoke_test.ps1` | Pass | 요청 생성, 영속화, 읽음 처리, unread count 확인 |
| DB outage and recovery | `scripts/test_db_down.ps1` | Pass | DB down 중 `accepted`, 복구 후 `persisted` 확인 |
| Redis outage and recovery | `scripts/test_redis_down.ps1` | Pass | Redis down 중 API 실패, 복구 후 정상 처리 확인 |
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

### HPA Scaling
- API HPA scale-up observed:
  - `initial_replicas=3 -> max_replicas=5`, `cpu_target=88`
  - `initial_replicas=3 -> max_replicas=6`, `cpu_target=138`
  - recent quick start run: `initial_replicas=3 -> max_replicas=6`, `cpu_target=241`

### Quick Start
- `scripts/quick_start_all.ps1` recent successful run:
  - smoke test: pass
  - DB recovery test: pass
  - Redis recovery test: pass
  - HPA scaling test: pass
  - ingress readiness at `http://localhost/health/ready`: pass
  - TLS readiness at `https://localhost/health/ready`: pass

### Ingress UI Endpoints
- Grafana UI:
  - `http://localhost/grafana`
  - `https://localhost/grafana`
- Prometheus UI:
  - `http://localhost/prometheus/`
  - `https://localhost/prometheus/`

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

최근 확인 결과:
- total requests: `7435`
- error rate: `0.00%`
- average latency: `2459.75ms`
- p95 latency: `5092.95ms`

현재 해석:
- 실행 오류가 아니라 성능 미달입니다.
- 즉 load test infrastructure는 동작하지만 latency tuning이 아직 필요합니다.

## Current Interpretation
- 기능 검증 경로는 현재 저장소 상태에서 다시 재현 가능합니다.
- autoscaling(HPA)은 metrics-server 추가 후 실제 동작을 확인했습니다.
- ingress 기반 외부 진입은 `http://localhost`와 `https://localhost` 기준으로 검증했습니다.
- backup / restore도 수동 운영 경로 기준으로 실제 검증했습니다.
- 현재 가장 큰 남은 과제는 `k6` latency 개선과 운영 정책 고도화입니다.
