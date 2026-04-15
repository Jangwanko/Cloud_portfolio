# Quick Start

## Before You Run
- Docker Desktop가 실행 중이어야 합니다.
- Windows PowerShell 기준으로 실행합니다.

저장소에 포함된 도구:
- `tools/kind.exe`
- `tools/helm/windows-amd64/helm.exe`

로컬에서 사용하는 포트:
- `30080` for API
- `30300` for Grafana
- `9090` for Prometheus when failover alert validation runs

quick start script는 배포 전에 이 포트들의 충돌 여부를 먼저 확인하고, 이미 사용 중이면 초기에 중단합니다.

## One Command
전체 로컬 검증 흐름은 아래 명령 하나로 실행할 수 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1
```

이 흐름에는 아래 작업이 포함됩니다.
- 로컬 kind cluster 재생성
- Kubernetes autoscaling을 위한 `metrics-server` 설치
- application image build 및 kind load
- PostgreSQL HA / Redis HA 배포
- application stack 배포
- API readiness 확인
- smoke, DB recovery, Redis recovery, HPA scaling test 실행

기본 접근 URL:
- API: `http://localhost:30080`
- Grafana: `http://localhost:30300`

## Expected Duration
아래 시간은 최근 kind + Docker Desktop 기준 실측값에 가까운 대략치입니다. 환경에 따라 달라질 수 있습니다.

| Scenario | Script | Typical duration |
| --- | --- | --- |
| Full quick start | `scripts/quick_start_all.ps1` | about 12-18 min |
| Smoke test | `scripts/smoke_test.ps1` | about 15-30 sec |
| DB recovery test | `scripts/test_db_down.ps1` | about 1-2 min |
| Redis recovery test | `scripts/test_redis_down.ps1` | about 1-2 min |
| HPA scaling test | `scripts/test_hpa_scaling.ps1` | about 30-45 sec |
| DLQ flow test | `scripts/test_dlq_flow.ps1` | about 1-2 min |
| Failover + alert test | `scripts/test_failover_alerts.ps1` | about 4-5 min |
| k6 load test | `scripts/test_k6_load.ps1` | about 1 min |

## Optional
failover alert validation까지 포함해서 실행하려면:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1 -IncludeFailoverAlerts
```

추가로 아래 항목을 검증합니다.
- Prometheus alert firing for DB outage
- Prometheus alert firing for Redis outage
- alert resolution after recovery

## Separate Load Test
k6 performance test는 별도로 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_k6_load.ps1
```

참고:
- 이 테스트는 단순 health check가 아니라 performance test입니다.
- 현재 저장소 상태에서는 실행 자체는 정상이나 latency threshold는 아직 실패할 수 있습니다.

## Individual Scenarios
Smoke test:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1
```

DB outage and recovery:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_db_down.ps1
```

Redis outage and recovery:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_redis_down.ps1
```

Kubernetes autoscaling:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_hpa_scaling.ps1
```

DLQ flow:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_dlq_flow.ps1
```

Failover and alert validation:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_failover_alerts.ps1
```
