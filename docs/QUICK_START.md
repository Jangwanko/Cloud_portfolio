# Quick Start

## Before You Run
- Docker Desktop must be running
- Windows PowerShell 기준으로 실행합니다

저장소에 포함된 도구:
- `tools/kind.exe`
- `tools/helm/windows-amd64/helm.exe`

로컬에서 사용하는 포트:
- `80` for ingress HTTP
- `443` for ingress HTTPS
- `9090` for Prometheus when failover alert validation runs

`scripts/quick_start_all.ps1`는 실행 전에 포트 충돌을 확인하고, 충돌이 있으면 배포 전에 중단합니다.

## One Command
전체 로컬 검증은 아래 명령 하나로 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1
```

이 흐름에는 아래 작업이 포함됩니다.
- kind cluster 생성
- `ingress-nginx` 설치
- `metrics-server` 설치
- application image build and kind load
- PostgreSQL HA / Redis HA 배포
- application stack 배포
- ingress readiness 확인
- smoke, DB recovery, Redis recovery, HPA scaling test 실행

기본 접근 URL:
- API: `http://localhost`
- TLS API: `https://localhost`
- Grafana: `http://localhost/grafana`
- TLS Grafana: `https://localhost/grafana`
- Prometheus: `http://localhost/prometheus/`
- TLS Prometheus: `https://localhost/prometheus/`

참고:
- HTTPS는 local self-signed certificate 기반입니다
- 브라우저에서는 처음 한 번 보안 경고가 날 수 있습니다

## Expected Duration
아래 시간은 최근 kind + Docker Desktop 기준의 대략적인 실행 시간입니다.

| Scenario | Script | Typical duration |
| --- | --- | --- |
| Full quick start | `scripts/quick_start_all.ps1` | about 12-18 min |
| Smoke test | `scripts/smoke_test.ps1` | about 15-30 sec |
| DB recovery test | `scripts/test_db_down.ps1` | about 1-2 min |
| Redis complete outage test | `scripts/test_redis_down.ps1` | about 2-3 min |
| Redis single-node failover test | `scripts/test_redis_failover.ps1` | about 2-3 min |
| HPA scaling test | `scripts/test_hpa_scaling.ps1` | about 30-45 sec |
| DLQ flow test | `scripts/test_dlq_flow.ps1` | about 1-2 min |
| Failover + alert test | `scripts/test_failover_alerts.ps1` | about 4-5 min |
| k6 load test | `scripts/test_k6_load.ps1` | about 1 min |

## Optional
failover alert validation까지 포함해서 실행하려면:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1 -IncludeFailoverAlerts
```

추가로 검증하는 항목:
- Prometheus alert firing for DB outage
- Prometheus alert firing for Redis outage
- alert resolution after recovery

## Separate Load Test
k6 performance test는 별도로 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_k6_load.ps1
```

참고:
- 이 테스트는 health check가 아니라 performance test입니다
- 현재 저장소 상태에서는 실행은 정상이나 latency threshold는 아직 미통과입니다

## Individual Scenarios
Smoke test:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1
```

DB outage and recovery:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_db_down.ps1
```

Redis complete outage and recovery:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_redis_down.ps1
```

Redis single-node failover:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_redis_failover.ps1
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
