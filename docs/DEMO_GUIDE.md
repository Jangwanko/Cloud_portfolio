# Demo Guide

Master / dev-kafka source: UI `2.3.1`, API `2.1.0`

Public demo-lite last verified: UI `2.3.1`, API `2.1.0` (2026-08-09)

## Purpose

- 브라우저에서 범용 event acceptance부터 PostgreSQL persistence까지의 흐름 확인
- 주문·결제 lifecycle은 generic event contract에 올린 reference scenario로 사용
- 확인 흐름:
  - API intake
  - Kafka append
  - Worker persistence
  - PostgreSQL storage
  - DLQ / Operations Advisor 상태

## Demo URLs

| Surface | URL | Use |
| --- | --- | --- |
| Deployed Demo UI | `https://vm118.js-banjiha.cloud/demo/order-dashboard.html` | demo-lite `2.3.1` generic v2 reference scenario 시연 |
| Deployed Swagger | `https://vm118.js-banjiha.cloud/docs` | API contract 확인 |
| Deployed Grafana | `https://vm118.js-banjiha.cloud/grafana/d/messaging-portfolio-overview/reliable-event-processing-operations-overview?orgId=1&refresh=5s` | Kafka lag, Worker replica, persistence 지연 확인 |
| Deployed Readiness | `https://vm118.js-banjiha.cloud/health/ready` | Kafka / PostgreSQL 상태 확인 |
| Deployed DLQ summary | `https://vm118.js-banjiha.cloud/v1/dlq/ingress/summary?limit=200&sample_limit=5` | 로그인 user 범위 recent log sample 확인 |
| Local Demo UI | `http://localhost/demo/order-dashboard.html` | 로컬 데모 확인 |
| Local Swagger | `http://localhost/docs` | 로컬 API contract 확인 |
| Local Grafana | `http://localhost/grafana/d/messaging-portfolio-overview/reliable-event-processing-operations-overview?orgId=1&refresh=5s` | 로컬 운영 지표 확인 |

Grafana 접근:

- 대시보드 조회: anonymous Viewer, 로그인 없이 확인
- admin 계정: 설정 변경용 secret로 유지

Version boundary:

- master / `dev-kafka` source: UI `2.3.1`, API `2.1.0`, generic `/v2/streams/{stream_id}/events`, PostgreSQL read model, `/ops/summary` 사용
- dev GitOps release: UI `2.3.1`, API `2.1.0`, image `8d334b8abeaf`
- public demo-lite 2026-08-10: title `Reliable Event Processing Console`, UI `2.3.1`, API `2.1.0`, image `8640ca010960`, readiness `ready`, generic v2 event `202`
- `demo-dev` profile: Kafka `1`, PostgreSQL `1`, API·core Worker `1→2`, notification Worker fixed `1`
- 검증 방법: 화면 `ver.` badge와 `/health/ready`의 `app_version`을 각각 확인

API boundary:

- shared auth/resource APIs: `POST /v1/auth/login`, `POST /v1/streams`
- generic intake: `POST /v2/streams/{stream_id}/events`
- generic read aliases: `GET /v2/event-requests/{request_id}`, `GET /v2/streams/{stream_id}/events`
- demo batch summary: `GET /v1/streams/{stream_id}/persistence-summary`
- operator summary: `GET /ops/summary` (Worker replica, 15초 cache)

## Quick Start

- 새 disposable cluster에서 처음 실행:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1
```

- 현재 `2.3.1` source image build/load:

```powershell
docker build -t messaging-portfolio:local .
tools\kind.exe load docker-image messaging-portfolio:local --name messaging-ha
```

기존 cluster에서 API만 restart하는 UI-only 갱신은 generic v2 rollout에 사용하지 않습니다. 수동 local manifest는 `GENERIC_EVENTS_V2_ENABLED=false`로 시작해 migration 동안 v2 intake를 막습니다. Quick start는 Worker rollout을 기다린 뒤 API env를 `true`로 바꾸고 API rollout을 확인합니다. GitOps는 gate `false`인 `messaging-env` Secret wave `-3`, 일반 Sync migration Job wave `-2`, Worker wave `-1`, API wave `0` 순서입니다. 구 Worker가 v2 job을 처리하면 body preview만 남고 JSON `payload`/`metadata`가 보존되지 않습니다.

## Demo Flow

- 새 disposable cluster를 `quick_start_all.ps1`로 만들었거나 staged rollout을 완료한 로컬 데모: `http://localhost/demo/order-dashboard.html` 접속
- 외국인 리크루터에게 보여줄 때 `EN` 선택
- `샘플 10개 추가`, `샘플 100개 추가`, `샘플 1000개 추가` 중 하나 선택
- `Reference 이벤트 보내기` / `Send Reference Events` 클릭
- 숫자 흐름 확인:
  - `예약 건수`
  - `Kafka 적재`
  - `DB 저장`
  - `총 소요시간`
- 오른쪽 패널 확인:
  - `Operations Advisor`
  - Worker replica
  - Readiness
  - user-filtered DLQ recent log sample
  - DLQ detail / manual replay
- 운영 상태 refresh: 기본 30초, 선택 60초
- source candidate 배포 뒤 화면 `ver. 2.3.1`과 API version `2.1.0` 표시 확인

Public demo-lite 확인:

- `https://vm118.js-banjiha.cloud/demo/order-dashboard.html` 접속
- 마지막 확인 title/badge: `Reliable Event Processing Console` / `2.3.1`; OpenAPI `2.1.0`, generic v2 event success `202`
- Kafka append와 DB persistence의 동시 진행률, Worker `현재/최대`, Operations Advisor 확인

## English Demo Script

- Start a fresh disposable cluster with the current `master` source, or complete the staged v2 rollout, then open `http://localhost/demo/order-dashboard.html`.
- Click `EN`.
- Click `Add 10 Samples`, `Add 100 Samples`, or `Add 1000 Samples`.
- Click `Send Reference Events`.
- Watch the counters move from `Reserved` to `Kafka Appended` to `DB Persisted`.
- Open Grafana to show consumer lag and Worker scaling evidence.

## Counter Meaning

| Counter | Meaning |
| --- | --- |
| `예약 건수` / `Reserved` | 전송 시작 후 `남은 예약/전체 예약`으로 표시. Kafka append 성공 시 감소 |
| `Kafka 적재` / `Kafka Appended` | API가 `message-ingress` topic append에 성공한 수 |
| `DB 저장` / `DB Persisted` | Worker가 PostgreSQL commit까지 끝낸 수 |
| `총 소요시간` / `Total Elapsed` | 전송 시작부터 현재 run의 DB 저장 완료까지 걸린 시간 |
| `Worker` | 현재 core Worker replica / 최대 replica. full profile `2/4`, demo-lite `1/2` |

Persistence 확인 방식:

- 한 batch에서 reference stream 1개 생성
- event append: `POST /v2/streams/{stream_id}/events`
- event append 전송과 동시에 `GET /v1/streams/{stream_id}/persistence-summary`를 1초 간격으로 polling
- accepted event 수와 `persisted_count` 비교
- 전송 진행 중 Operations Advisor는 `처리 중`, 종료 뒤에만 카운터 불일치 판정
- Pipeline Evidence의 DB 단계 아래에서 저장 컬럼과 envelope 샘플 검증 확인
- 최대 polling 안에 확인되지 않은 row는 `일부 미확인`
- API append 실패 event는 `send_failed`로 종료, 전체 화면이 무한 처리 중에 남지 않음

## Read The Counters This Way

- `예약 건수`와 `DB 저장`은 같은 숫자가 아니다.
- API는 Kafka에 event를 전달한다.
- Worker는 Kafka event를 소비해 DB에 저장한다.
- Kafka append와 DB 저장은 서로 다른 완료 조건으로 갱신
- UI `2.3.1`에서는 Kafka append가 끝나기 전에도 DB 저장 증가 확인 가능
- 일부 `send_failed` 또는 persistence 미확인 존재: `완료` 대신 부분 확인 상태

## Authentication Reuse

- demo login token을 memory에 cache
- 동일 username / API base에서 만료 전 재사용
- UI cache lifetime: 30분
- unauthorized 응답: token refresh 뒤 운영 status 재시도
- 브라우저 영구 저장소 사용 제외

## Structured DB Evidence

DB 저장 증거:

- `schema_version`: envelope contract version
- `event_type`: producer-defined event type
- `payload`: domain-neutral JSON event data
- `metadata`: classification, external reference 같은 선택적 JSON context
- DB row 조회: 로그인 user와 stream membership 범위
- `master` UI `2.0.0`: 별도 DB 저장 컬럼 패널
- `demo-dev` UI `2.3.0`: Pipeline Evidence의 DB 단계 뒤 compact evidence

주문 reference sample은 `reference.payment.completed`, `reference.order.created` 같은 `event_type`과 `metadata.external_references.payment`를 사용합니다. 이 값은 generic core의 필수 domain 규칙이 아닙니다.

Legacy compatibility:

- `/v1/orders/{order_id}/events`: order reference adapter
- body-only `/v1/streams/{stream_id}/events`: 이전 client 호환
- `category`, `payment_id`, `body`: 기존 row와 historical evidence 호환 alias

이 패널은 Kafka accepted envelope와 PostgreSQL persistence schema의 연결을 설명합니다.

## Operations Advisor

- 역할:
  - 예약, Kafka 적재, DB 저장, DLQ 신호 해석
  - 위험 상태와 다음 확인 항목 표시
  - 운영자가 봐야 할 신호 요약
- 현재 방식:
  - rule-based
  - AI API 미사용
  - `demo-dev` UI `2.3.0`: 전송·저장 추적 중 `처리 중`, run 종료 뒤 카운터 불일치 판정
- 확장 여지:
  - 별도 AI Worker
  - 운영 요약 생성
  - 원인 후보 정리
  - runbook 추천
- 경계:
  - 핵심 event persistence path에서 AI 제외
  - AI는 운영 보조 경로에만 배치
  - DLQ `total`, `replayable`, `blocked`: recent log sample 해석

## DLQ Detail And Manual Replay

- list/summary scope: `recent_log_sample`
- `user_filtered=true`: 로그인 user의 event만 표시
- `oldest_sample_age_seconds`: sample의 oldest record age
- unresolved depth / current backlog: 표시 제외
- detail: `failed_reason`, request/stream id, retry/replay count
- 개별 replay: replayable item의 request id 전송
- 전체 replay: 화면에 로드된 replayable sample만 요청
- guard 도달 item: button disabled
- replay 완료 확인: request status를 3초 간격 polling
- duplicate replay claim: manual/automatic 경로가 같은 request/replay generation claim을 공유해 중복 요청 방지

Manual replay는 original DLQ log record를 삭제하지 않습니다. Summary count 감소를 성공 기준으로 사용하지 않습니다.

## Queue Reset And DB Reset

`전송 전 예약 비우기`:

- 아직 Kafka로 보내지 않은 예약만 취소
- 이미 시작된 event는 Kafka 적재와 DB 저장까지 계속 추적

`RESET DEMO DB`:

- 로컬 데모 이벤트 DB 초기화
- 유지:
  - 사용자 계정
- 삭제:
  - demo reference stream
  - messages
  - request status
  - idempotency state
  - notification attempt
- DLQ:
  - `message-ingress-dlq` topic 삭제 후 재생성
  - DLQ summary 초기화
- 주의:
  - 로컬 데모 reset 전용
  - 실제 운영 DLQ 이력 삭제 절차 아님
  - `DEMO_RESET_ENABLED=true` 필요; non-demo environment에서는 false 유지

## Interview Lines

Korean:

> 이 데모는 Kafka 기반 고신뢰 이벤트 처리 시스템의 acceptance 경계부터 보여줍니다. 주문 lifecycle은 범용 contract에 넣은 참조 시나리오입니다. API는 typed JSON event를 Kafka에 적재하고 `202 Accepted`를 반환합니다. Worker는 PostgreSQL에 비동기 저장하며, 실패 event는 제한된 retry 뒤 DLQ로 격리합니다. 화면은 Kafka append와 DB persistence를 분리해 보여줍니다.

English:

> This demo shows a reliable event-processing boundary. The order lifecycle is a reference scenario on the generic contract. The API appends typed JSON events to Kafka and returns `202 Accepted`; workers persist them asynchronously to PostgreSQL. The UI keeps Kafka acceptance and database persistence as separate signals.
