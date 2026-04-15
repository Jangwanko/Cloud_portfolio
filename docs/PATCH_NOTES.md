# Patch Notes

프로젝트를 만들고 검증 흐름을 붙이는 과정에서 어떤 변경이 있었는지 단계별로 정리한 문서입니다.

## v0.1 Initial Messaging Flow
추가된 범위:
- FastAPI API
- Redis queue
- Worker 비동기 처리
- PostgreSQL 영속화
- read receipt / unread count

배경:
- 먼저 정상 요청 흐름을 만들고 API와 영속화 경로를 분리하는 것이 목표였습니다.

영향:
- 메시지 요청 수락과 비동기 저장의 기본 구조가 만들어졌습니다.

남은 과제:
- 장애 상황에서의 복구 흐름은 아직 제한적이었습니다.

## v0.2 Failure Recovery and DLQ
추가된 범위:
- DB 장애 중 요청 수락
- retry 로직
- DLQ
- DLQ replayer

배경:
- 정상 경로만으로는 메시징 시스템의 복구 흐름을 설명하기 어려웠습니다.

영향:
- DB 장애 중에도 요청을 Redis에 보존하고, 복구 후 다시 처리할 수 있게 됐습니다.

남은 과제:
- 장애 상태를 외부에서 더 명확하게 관찰할 필요가 있었습니다.

## v0.3 Observability
추가된 범위:
- Prometheus metrics
- Grafana dashboard
- queue depth / health / worker latency 지표
- alert rule

배경:
- 장애를 재현하는 것만으로는 충분하지 않았고, 상태를 수치로 확인할 수 있어야 했습니다.

영향:
- DB, Redis, Worker 상태와 기본 성능 지표를 시각적으로 확인할 수 있게 됐습니다.

남은 과제:
- queue lag, DB transaction latency 같은 더 세밀한 지표는 추가 여지가 남았습니다.

## v0.4 Kubernetes and HA
추가된 범위:
- kind 기반 Kubernetes 실행 환경
- PostgreSQL HA
- Redis replica + Sentinel
- API / Worker 다중 replica
- HPA 리소스
- k8s 내부 k6 Job

배경:
- 단일 프로세스 구성만으로는 HA와 장애 복구를 충분히 설명하기 어려웠습니다.

영향:
- 로컬에서 quorum 기반 DB/Redis failover와 다중 replica 구성을 재현할 수 있게 됐습니다.

남은 과제:
- 외부 진입은 여전히 NodePort 중심이어서 배포형 구조로 더 다듬을 여지가 남았습니다.

## v0.5 Scenario Scripts
추가된 범위:
- `scripts/smoke_test.ps1`
- `scripts/test_db_down.ps1`
- `scripts/test_redis_down.ps1`
- `scripts/test_dlq_flow.ps1`
- `scripts/test_failover_alerts.ps1`
- `scripts/test_k6_load.ps1`
- `scripts/reset_k8s_state.ps1`

배경:
- 수동 명령 나열만으로는 재현성과 설명력이 떨어졌습니다.

영향:
- 주요 장애 시나리오를 반복 가능한 스크립트로 검증할 수 있게 됐습니다.

남은 과제:
- first-run UX와 단계 간 안정화는 더 정리할 필요가 있었습니다.

## v0.6 Reliability Fixes
추가된 범위:
- readiness 상태 코드 보정
- Redis idempotency fallback 정리
- Observer XSS 제거
- kind bootstrap / namespace 정리

배경:
- 겉보기 동작과 실제 운영 관점의 동작이 맞지 않는 지점들이 있었습니다.

영향:
- readiness probe가 실제 상태를 더 잘 반영하고, 요청 상태 불일치와 UI 보안 문제가 줄었습니다.

남은 과제:
- 로컬 실행 UX와 외부 진입 구조는 여전히 개선 대상이었습니다.

## v0.7 Quick Start Consolidation
추가된 범위:
- `scripts/quick_start_all.ps1`
- `docs/QUICK_START.md`

배경:
- 주요 시나리오를 한 번에 재현할 수 있는 진입점이 필요했습니다.

영향:
- cluster 생성, image load, HA 배포, smoke/DB/Redis 검증을 한 번에 실행할 수 있게 됐습니다.

남은 과제:
- load test와 autoscaling 검증은 별도로 더 정리할 필요가 있었습니다.

## v0.8 Autoscaling and Recovery Stabilization
추가된 범위:
- `metrics-server` 설치 경로 추가
- `scripts/test_hpa_scaling.ps1` 추가
- DB recovery 시 pgpool-backed DB query 안정화 대기 추가

배경:
- HPA가 선언만 되어 있고 실제로는 metrics API 부재로 동작하지 않았습니다.
- DB recovery도 fresh cluster 직후에는 간헐적으로 흔들렸습니다.

영향:
- API HPA가 실제 CPU 메트릭을 읽고 replica를 늘리는 것을 확인했습니다.
- DB recovery 시나리오가 올인원 quick start에서도 더 안정적으로 통과하게 됐습니다.

남은 과제:
- `k6` 성능 기준은 아직 통과하지 못했고, Ingress 중심 외부 진입 구조도 아직 남아 있습니다.

## Current Known Gaps
- 외부 진입 구조는 아직 `Ingress` 중심으로 정리되지 않았습니다.
- `k6`는 실행은 되지만 현재 latency threshold는 실패할 수 있습니다.
- 멀티 파드 환경에서 메시지 순서 보장 검증은 더 필요합니다.

## Next Changes
1. `TEST_RESULTS.md`를 기준으로 검증 결과를 정리하고 유지
2. `k6` 성능 병목 분석 및 threshold 재검토
3. `Ingress / prod-like endpoint` 기반 진입 구조 도입
