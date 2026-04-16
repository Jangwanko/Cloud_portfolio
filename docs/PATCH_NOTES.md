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
- 요청 수락과 비동기 영속화의 기본 구조가 만들어졌습니다.

## v0.2 Failure Recovery and DLQ
추가된 범위:
- DB 장애 중 요청 수락
- retry 로직
- DLQ
- DLQ replayer

배경:
- 정상 경로만으로는 복구 흐름을 설명하기 어려웠습니다.

영향:
- DB 장애 중에도 요청을 Redis에 보존하고, 복구 후 재처리하는 구조가 생겼습니다.

## v0.3 Observability
추가된 범위:
- Prometheus metrics
- Grafana dashboard
- queue depth / health / worker latency 지표
- alert rule

배경:
- 장애를 재현하는 것만으로는 충분하지 않았고, 상태를 수치로 확인할 수 있어야 했습니다.

영향:
- DB, Redis, Worker 상태와 기본 성능 지표를 볼 수 있게 됐습니다.

## v0.4 Kubernetes and HA
추가된 범위:
- kind 기반 Kubernetes 실행 환경
- PostgreSQL HA
- Redis replica + Sentinel
- API / Worker 다중 replica
- HPA 리소스
- k8s 내 k6 Job

배경:
- 단일 프로세스 구성만으로는 HA와 장애 복구를 설명하기 어려웠습니다.

영향:
- 로컬에서도 HA 구성과 failover 실험이 가능해졌습니다.

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
- 수동 명령 나열만으로는 재현성과 설명력이 약했습니다.

영향:
- 주요 장애 시나리오를 반복 가능한 스크립트로 검증할 수 있게 됐습니다.

## v0.6 Reliability Fixes
추가된 범위:
- readiness 상태 코드 보정
- Redis idempotency fallback 정리
- Observer XSS 제거
- kind bootstrap / namespace 정리

배경:
- 겉보기 동작과 실제 운영 관점의 동작이 어긋나는 지점이 있었습니다.

영향:
- readiness와 상태 노출이 실제 조건을 더 잘 반영하게 됐고, 보안과 안정성도 보강됐습니다.

## v0.7 Quick Start Consolidation
추가된 범위:
- `scripts/quick_start_all.ps1`
- `docs/QUICK_START.md`

배경:
- 주요 시나리오를 한 번에 재현할 수 있는 진입점이 필요했습니다.

영향:
- cluster 생성, image load, HA 배포, smoke/DB/Redis/HPA 검증을 한 번에 실행할 수 있게 됐습니다.

## v0.8 Autoscaling and Recovery Stabilization
추가된 범위:
- `metrics-server` 설치 경로 추가
- `scripts/test_hpa_scaling.ps1` 추가
- DB recovery 시 pgpool-backed DB query 안정화 대기 추가

배경:
- HPA가 선언만 있고 실제로는 metrics API 부재로 동작하지 않았습니다.
- DB recovery는 fresh cluster 직후에 간헐적으로 흔들렸습니다.

영향:
- API HPA가 실제 CPU 메트릭을 읽고 replica를 늘리는 것을 검증했습니다.
- DB recovery 시나리오와 all-in-one quick start가 더 안정적으로 통과하게 됐습니다.

## v0.9 Auth and Ingress
추가된 범위:
- `/v1/auth/login` 및 bearer token 흐름
- room membership 기반 최소 인가
- `ingress-nginx` 설치 스크립트
- `ClusterIP + Ingress` 기반 외부 진입 구조

배경:
- 시스템은 동작했지만 사용자 경계와 서비스형 진입점이 부족했습니다.
- `NodePort` 중심 접근은 로컬 실험에는 충분했지만 배포형 구조로는 아쉬웠습니다.

영향:
- 최소 범위의 인증 / 인가가 들어가서 요청 주체를 구분할 수 있게 됐습니다.
- 외부 접근은 `http://localhost`와 `http://localhost/grafana`로 ingress를 통해 라우팅됩니다.

## v0.10 Local TLS
추가된 범위:
- local self-signed TLS certificate 생성 스크립트
- ingress `localhost` TLS 설정
- quick start의 HTTPS readiness 검증

배경:
- ingress는 붙었지만 HTTPS가 없으면 실제 서비스형 진입 구조로는 아쉬움이 남았습니다.

영향:
- `https://localhost`와 `https://localhost/grafana` 경로가 로컬에서도 동작합니다.
- quick start는 HTTP와 HTTPS readiness를 모두 확인합니다.

## v0.11 Prometheus Ingress
추가된 범위:
- Prometheus UI의 ingress path 노출
- `/prometheus/` 경로 정리

배경:
- Grafana뿐 아니라 raw metrics와 alert 상태도 같은 진입점 아래에서 바로 볼 수 있어야 했습니다.

영향:
- Prometheus UI를 `http://localhost/prometheus/`와 `https://localhost/prometheus/`에서 확인할 수 있습니다.

## Current Known Gaps
- HTTPS는 local self-signed certificate 기준입니다.
- `k6`는 실행되지만 현재 latency threshold는 통과하지 못합니다.
- 멀티 파드 환경에서 순서 보장 검증은 더 필요합니다.

## Next Changes
1. TLS와 운영 도구 접근 제어 정리
2. `k6` 병목 분석 및 성능 기준 재정리
3. staging / prod 분리를 고려한 배포 구조 정리
