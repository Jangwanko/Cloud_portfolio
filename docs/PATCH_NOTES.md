# Patch Notes

프로젝트를 만들고 검증 범위를 넓혀 오는 과정에서 어떤 변경이 있었는지 단계별로 정리한 문서입니다.

## v0.1 Initial Messaging Flow
추가된 범위:
- FastAPI API
- Redis queue
- Worker 비동기 처리
- PostgreSQL 영속화
- read receipt / unread count

배경:
- 요청 수락과 영속화 경로를 분리한 기본 비동기 처리 구조가 필요했습니다.

영향:
- API가 요청을 받고, worker가 뒤에서 처리하는 기본 transaction pipeline이 만들어졌습니다.

## v0.2 Failure Recovery and DLQ
추가된 범위:
- DB 장애 중 요청 수락
- retry 로직
- DLQ
- DLQ replayer

배경:
- 정상 경로만으로는 복구 전략과 장애 대응 흐름을 설명하기 어려웠습니다.

영향:
- DB 장애 중에도 요청을 Redis에 보존하고, 복구 후 재처리하는 경로가 생겼습니다.

## v0.3 Observability
추가된 범위:
- Prometheus metrics
- Grafana dashboard
- queue depth / health / worker latency 지표
- alert rule

배경:
- 장애 대응 구조가 있어도 상태를 관측할 수 없으면 운영 구조로 설명하기 어렵습니다.

영향:
- DB, Redis, Worker 상태와 주요 메트릭을 바로 확인할 수 있게 됐습니다.

## v0.4 Kubernetes and HA
추가된 범위:
- kind 기반 Kubernetes 실행 환경
- PostgreSQL HA
- Redis replica + Sentinel
- API / Worker 다중 replica
- HPA 리소스
- k8s용 k6 Job

배경:
- 단일 프로세스 환경만으로는 HA와 failover를 보여주기 어려웠습니다.

영향:
- 로컬에서도 HA, failover, autoscaling 구조를 재현할 수 있게 됐습니다.

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
- 수동 명령 나열만으로는 재현성과 설명력이 부족했습니다.

영향:
- 주요 장애 시나리오를 반복 가능한 스크립트로 검증할 수 있게 됐습니다.

## v0.6 Reliability Fixes
추가된 범위:
- readiness 상태 코드 보정
- Redis idempotency fallback 정리
- Observer XSS 제거
- kind bootstrap / namespace 정리

배경:
- 문서상 의도와 실제 동작 사이에 어긋나는 지점이 있었습니다.

영향:
- readiness와 보안, 재기동 안정성이 실제 동작 기준에 더 가깝게 정리됐습니다.

## v0.7 Quick Start Consolidation
추가된 범위:
- `scripts/quick_start_all.ps1`
- `docs/QUICK_START.md`

배경:
- 주요 시나리오를 한 번에 실행할 수 있는 진입점이 필요했습니다.

영향:
- cluster 생성, image load, HA 배포, smoke / DB / Redis / HPA 검증을 한 번에 실행할 수 있게 됐습니다.

## v0.8 Autoscaling and Recovery Stabilization
추가된 범위:
- `metrics-server` 설치 경로 추가
- `scripts/test_hpa_scaling.ps1` 추가
- DB recovery 시 pgpool-backed DB query 안정화 대기 추가

배경:
- HPA가 선언만 있고 실제로는 metrics API 부재로 동작하지 않았습니다.
- DB recovery가 fresh cluster 직후 간헐적으로 흔들렸습니다.

영향:
- API HPA가 실제 CPU 메트릭을 읽고 replica를 늘리는 것을 검증했습니다.
- DB recovery 시나리오와 all-in-one quick start가 안정적으로 통과하게 됐습니다.

## v0.9 Auth and Ingress
추가된 범위:
- `/v1/auth/login`
- bearer token 기반 인증
- stream membership 기반 최소 인가
- `ingress-nginx`
- `ClusterIP + Ingress` 기반 외부 진입

배경:
- 사용자 경계와 서비스형 진입 구조가 부족했습니다.

영향:
- 최소 범위의 인증 / 인가가 추가됐고, API와 운영 UI를 ingress 경로로 노출하게 됐습니다.

## v0.10 Local TLS
추가된 범위:
- local self-signed TLS certificate 생성 스크립트
- `localhost` ingress TLS 설정
- quick start의 HTTPS readiness 검증

배경:
- ingress만으로는 실제 서비스형 진입 구조로 보기에 부족했습니다.

영향:
- `https://localhost`와 하위 경로를 로컬에서도 검증할 수 있게 됐습니다.

## v0.11 Prometheus Ingress
추가된 범위:
- Prometheus UI ingress path 노출
- `/prometheus/` 경로 정리

배경:
- Grafana뿐 아니라 raw metrics와 alert 상태도 같은 진입점 아래에서 보여줄 필요가 있었습니다.

영향:
- Prometheus UI를 `http://localhost/prometheus/`와 `https://localhost/prometheus/`에서 확인할 수 있게 됐습니다.

## v0.12 Operations Hardening
추가된 범위:
- `messaging-runtime-secrets` 기반 runtime secret 분리
- Grafana admin credential 외부화
- 수동 PostgreSQL backup 스크립트
- PostgreSQL restore 스크립트
- 주 1회 PostgreSQL backup `CronJob`
- 운영 문서 보강

배경:
- 인증 키와 운영 자격증명이 매니페스트에 직접 들어가 있었고, backup / restore 경로도 운영 문서 수준에서 정리되지 않았습니다.

영향:
- runtime secret가 별도 관리되며, 수동 backup과 restore가 실제로 동작하는 상태가 됐습니다.
- HA 배포에는 주 1회 logical backup 설정이 포함되며, 운영 보강 흐름을 문서로 설명할 수 있게 됐습니다.

## v0.13 Redis Scenario Split
추가된 범위:
- `scripts/test_redis_down.ps1` 의미 재정의
- `scripts/test_redis_failover.ps1` 추가

배경:
- 기존 Redis 테스트는 "complete outage"와 "HA failover"가 하나의 시나리오에 섞여 있어, 무엇을 검증하는지 설명하기가 어려웠습니다.
- 특히 Redis HA + Sentinel 환경에서는 일부 노드 중단이 곧바로 전체 Redis down과 같지 않았습니다.

영향:
- `test_redis_down.ps1`는 이제 전체 Redis 접근 불가 시 event intake 실패와 복구 후 재수락을 검증합니다.
- `test_redis_failover.ps1`는 단일 Redis pod 재시작 후 HA가 복구되고 event intake가 계속 가능한지 검증합니다.
- Redis 장애와 Redis failover를 별도 운영 시나리오로 설명할 수 있게 됐습니다.

## v0.14 Stream and Event Refactor
추가된 범위:
- 외부 API 경로를 `stream / event` 기준으로 재정의
- 외부 응답 키를 `stream_id`, `stream_seq`, `event_id` 기준으로 변경
- smoke / DB down / Redis down / DLQ / k6 스크립트의 외부 경로 반영
- Observer UI와 주요 문서 표현 정리

배경:
- 프로젝트 해석은 점점 일반 transaction pipeline 쪽으로 넓어졌지만, 외부에 보이는 용어는 여전히 `room / message`에 묶여 있었습니다.
- 코드와 문서의 해석 범위를 넓히기 위해, DB 내부 테이블은 유지하되 외부 인터페이스는 더 일반적인 용어로 정리할 필요가 있었습니다.

영향:
- 외부 API는 이제 `stream`과 `event` 기준으로 읽히며, 채팅 전용 구조보다 범용 event pipeline에 가까운 표현을 갖게 됐습니다.
- DB 내부 스키마를 전면 변경하지 않고도, 면접관이 보게 되는 주요 표면 레이어의 용어를 일관되게 정리했습니다.
- 기본 검증 경로에서는 새 `stream / event` 경로로 smoke와 DB recovery가 다시 통과하는 것을 확인했습니다.

## v0.15 k6 Performance Tuning
異붽???踰붿쐞:
- `scripts/test_event_persist_latency.ps1` 異붽?
- event intake 寃쎈줈???덈씪?대뱶 Redis hot path 珥덉젙
- `scripts/load_test_k6.js` setup retry 異붽?
- HA ?섍꼍??DB pool / pgpool ?ㅼ젙 蹂닿컯

諛곌꼍:
- `k6`???ㅽ뻾?섏?留?latency threshold瑜?겕寃??덇꼬 ?덉뻽?듬땲??
- `k6` 寃곌낵留뚯쑝濡쒕뒗 accept 寃쎈줈? persisted 寃쎈줈 以묒뿉 ?댁꽌 媛 ?ㅼ젣 蹂묐ぉ?몄? 遺꾨━?섍린 ?대젮?좎뒿?덈떎.
- ?ㅼ젣 移≪젙 怨쇱젙?먯꽌 API 肄붾뱶?쇰쭔 ?꾨땲??`pgpool` connection ?쒗븳怨?硫붾え由?遺議깊룄 ?좏븿猿?蹂묐ぉ?쇰줈 ?쒕윭?ν뒿?덈떎.

?곹뼢:
- accept latency??persisted latency瑜?遺꾨━??蹂닿린 ?꾪븳 蹂댁“ ?깅뒫 寃利?寃쎈줈媛 異붽??섏뿀?듬땲??
- event intake 寃쎈줈?먯꽌 stream membership cache, request status ???щ? push pipeline, idempotency hot path ?⑥냼?붿솕媛 諛섏쁺?섏뿀?듬땲??
- `pgpool`??먮뒗 `too many clients`? OOM ?ъ떆?쒖옉??愿痢?섏뼱, DB pool ?ш린??pgpool connection / resource ?ㅼ젙???⑥쾶 議곗젙?섏뿀?듬땲??
- 理쒓렐 ?ъ측痢?湲곗??`k6` 寃곌낵?뒗 ?ㅼ쓬怨??숈씠 媛쒖꽑?섏뿀?듬땲??
  - 珥덇린 湲곗?? `5434 req`, avg `3660ms`, p95 `8175ms`
  - 1李?媛쒖꽑 ??? `7966 req`, avg `2285ms`, p95 `4936ms`
  - 2李?媛쒖꽑 ??? `9102 req`, avg `1934ms`, p95 `3851ms`
  - pgpool / DB pool 議곗젙 ??? `11314 req`, avg `1519ms`, p95 `3333ms`
- threshold?뒗 ?꾩쭅 誘몄쭊?낅땲留? ?꾩옱 蹂묐ぉ?먭큵 ?⑥닚 ?꾨옉?ㅻ━耳댁씠???쇱쭊蹂대떎 HA DB 吏꾩엯?먯꽌怨?connection policy?먯??곕슧 媛源깆떎?붾뒗 ?먯쓣 ?뺤씤?섏뿀?듬땲??

## Current Known Gaps
- HTTPS는 local self-signed certificate 기반입니다.
- `k6`는 실행되지만 현재 latency threshold를 통과하지 못합니다.
- 멀티 파드 환경에서 stream 단위 event 순서 보장 검증은 추가 작업이 필요합니다.
- 운영 UI 접근 제한은 아직 데모 친화적인 수준으로 유지하고 있습니다.

## Next Changes
1. 운영 UI 접근 정책과 secret 외부화 방향 정리
2. `k6` 병목 분석 및 성능 기준 재정리
3. staging / prod 분리를 고려한 배포 구조 정리
