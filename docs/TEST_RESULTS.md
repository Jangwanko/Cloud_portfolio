# Test Results

현재 저장소 상태에서 최근 다시 확인한 검증 결과를 정리한 문서입니다.

## Verified Scenarios

| Scenario | Script | Result | Notes |
| --- | --- | --- | --- |
| Smoke test | `scripts/smoke_test.ps1` | Pass | 메시지 생성, 영속화, 읽음 처리, unread count 확인 |
| DB outage and recovery | `scripts/test_db_down.ps1` | Pass | DB down 중 `accepted`, 복구 후 `persisted` 확인 |
| Redis outage and recovery | `scripts/test_redis_down.ps1` | Pass | Redis down 중 API 실패, 복구 후 정상 처리 확인 |
| DLQ flow | `scripts/test_dlq_flow.ps1` | Pass | DB down 상태에서 DLQ 적재 확인 |
| Failover and alert validation | `scripts/test_failover_alerts.ps1` | Pass | Prometheus alert firing / resolution 확인 |
| HPA scaling | `scripts/test_hpa_scaling.ps1` | Pass | API replica scale-up 확인 |
| Full quick start | `scripts/quick_start_all.ps1` | Pass | fresh kind cluster 기준 smoke, DB, Redis, HPA 포함 |

## Recent Measured Results

### Failover and Alert
- DB outage + recovery time: about `102s`
- Redis outage + recovery time: about `114s`

### HPA Scaling
- API HPA scale-up observed:
  - `initial_replicas=3 -> max_replicas=5`, `cpu_target=88`
  - `initial_replicas=3 -> max_replicas=6`, `cpu_target=138`
  - fresh quick start run: `initial_replicas=3 -> max_replicas=6`, `cpu_target=164`

### Quick Start
- `scripts/quick_start_all.ps1` recent successful run:
  - smoke test: pass
  - DB recovery test: pass
  - Redis recovery test: pass
  - HPA scaling test: pass

## k6 Load Test
`scripts/test_k6_load.ps1`는 현재 실행 경로 자체는 정상입니다. 다만 성능 threshold는 아직 통과하지 못합니다.

최근 확인된 결과:
- total requests: `7435`
- error rate: `0.00%`
- average latency: `2459.75ms`
- p95 latency: `5092.95ms`

현재 해석:
- 실행 오류가 아니라 성능 미달입니다.
- 즉, load test infrastructure는 동작하지만 latency tuning은 아직 필요합니다.

## Current Interpretation
- 기능 검증 경로는 현재 저장소 상태에서 다시 재현 가능합니다.
- autoscaling(HPA)은 metrics-server 추가 후 실제 동작이 확인됐습니다.
- 남은 핵심 리스크는 `k6` latency 개선과 `Ingress` 기반 외부 진입 구조 정리입니다.
