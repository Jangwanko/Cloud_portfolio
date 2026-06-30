# Demo Guide / 데모 가이드

브라우저 데모는 주문 완료 이후 이벤트가 API에서 Kafka로 들어가고, Worker를 거쳐 PostgreSQL에 저장되는 흐름을 한 화면에서 보여줍니다.

The browser demo shows the post-order event path from API intake to Kafka append, Worker persistence, and PostgreSQL storage.

## Demo URLs

| Surface | URL | Purpose |
| --- | --- | --- |
| Deployed Demo UI | `https://vm118.js-banjiha.cloud/demo/order-dashboard.html` | 주문 이후 이벤트 흐름 시연 |
| Deployed Swagger | `https://vm118.js-banjiha.cloud/docs` | API contract 확인 |
| Deployed Grafana | `https://vm118.js-banjiha.cloud/grafana/d/messaging-portfolio-overview/messaging-portfolio-operations-overview?orgId=1&refresh=5s` | Kafka lag, Worker replica, persistence 지연 확인 |
| Deployed Readiness | `https://vm118.js-banjiha.cloud/health/ready` | Kafka / PostgreSQL 상태 확인 |
| Deployed DLQ summary | `https://vm118.js-banjiha.cloud/v1/dlq/ingress/summary?limit=200&sample_limit=5` | DLQ reason, replayable, blocked 확인 |
| Local Demo UI | `http://localhost/demo/order-dashboard.html` | 로컬 데모 확인 |
| Local Swagger | `http://localhost/docs` | 로컬 API contract 확인 |
| Local Grafana | `http://localhost/grafana/d/messaging-portfolio-overview/messaging-portfolio-operations-overview?orgId=1&refresh=5s` | 로컬 운영 지표 확인 |
| Local Readiness | `http://localhost/health/ready` | 로컬 Kafka / PostgreSQL / standby 상태 확인 |
| Local DLQ summary | `http://localhost/v1/dlq/ingress/summary?limit=200&sample_limit=5` | 로컬 DLQ reason, replayable, blocked 확인 |

Grafana 접근:

- 대시보드 조회: anonymous Viewer, 로그인 없이 확인
- admin 계정: 설정 변경용 secret로 유지

## Quick Start

처음 실행할 때는 Docker Desktop을 켠 뒤 전체 bootstrap을 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1
```

2코어 2스레드급 서버에서는 full HA 대신 lite profile을 사용합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_lite.ps1
```

이미 클러스터가 있고 데모 화면 변경만 반영하려면 이미지를 다시 빌드하고 API deployment를 재시작합니다.

```powershell
docker build -t messaging-portfolio:local .
tools\kind.exe load docker-image messaging-portfolio:local --name messaging-ha
kubectl rollout restart deployment/api -n messaging-app
kubectl rollout status deployment/api -n messaging-app --timeout=180s
```

## Demo Flow

- 배포 데모: `https://vm118.js-banjiha.cloud/demo/order-dashboard.html` 접속
- 로컬 데모: `http://localhost/demo/order-dashboard.html` 접속
- 외국인 리크루터에게 보여줄 때 `EN` 선택. 기본 event body도 영어 문구로 변경
- `샘플 10개 추가`, `샘플 100개 추가`, `샘플 1000개 추가`로 전송 전 예약 생성
- `결제 완료 / 주문 완료 이벤트 보내기` 클릭
- 숫자 흐름 확인:
  - `예약 건수`
  - `Kafka 적재`
  - `DB 저장`
  - `총 소요시간`
- 오른쪽 패널 확인:
  - `Operations Advisor`
  - Worker replica
  - Readiness
  - DLQ summary
- DLQ가 있으면 `DLQ 상세 보기` 클릭
- replay 가능한 event는 `수동 재처리` 클릭
- replay guard 도달 event는 `수동 확인` 상태로 원인 확인

English demo script:

1. Open `https://vm118.js-banjiha.cloud/demo/order-dashboard.html`.
2. Click `EN`.
3. Click `Add 10 Samples`, `Add 100 Samples`, or `Add 1000 Samples`.
4. Click `Send Post-Order Events`.
5. Watch the counters move from `Reserved` to `Kafka Appended` to `DB Persisted`.
6. Open Grafana when you want to show consumer lag and Worker scaling evidence.
7. If DLQ appears, click `Show DLQ details`.
8. Click `Manual replay` for replayable events.

## DB Storage Evidence

데모 화면의 결과 패널은 PostgreSQL `messages` table에 어떤 구조로 저장되는지도 보여줍니다.
`event_type`, `category`, `payment_id`, `stream_seq`, `created_at`처럼 분석 파이프라인에서 바로 쓸 수 있는 컬럼을 확인할 수 있습니다.

The result panel also shows how events are stored in the PostgreSQL `messages` table.
It highlights analytics-friendly columns such as `event_type`, `category`, `payment_id`, `stream_seq`, and `created_at`.

최근 row 값은 화면에 직접 노출하지 않습니다. 데모에서는 raw id나 본문보다 저장 구조를 보여주는 편이 더 읽기 쉽습니다.
실제 운영 분석 파이프라인에서는 같은 저장 컬럼을 기준으로 batch export, CDC, warehouse load 같은 후속 경로를 붙일 수 있습니다.

## Counter Meaning

| Counter | Meaning |
| --- | --- |
| `예약 건수` / `Reserved` | 전송 시작 후 `남은 예약/전체 예약`으로 표시됩니다. API가 Kafka append에 성공하면 줄어듭니다. |
| `Kafka 적재` / `Kafka Appended` | API가 `message-ingress` topic append를 성공시킨 수입니다. |
| `DB 저장` / `DB Persisted` | Worker가 PostgreSQL commit까지 완료한 수입니다. |
| `총 소요시간` / `Total Elapsed` | 전송 시작부터 현재 run의 DB 저장 완료까지 걸린 시간입니다. |
| `Worker` | 현재 Worker replica와 최대 replica입니다. 예: `2/8`, `6/8`. |

`예약 건수`와 `DB 저장`은 같은 숫자가 아닙니다. API는 Kafka에 전달하고, Worker가 Kafka event를 소비해 DB에 저장합니다. 그래서 Kafka append가 빠르게 진행된 뒤 DB 저장이 뒤따라오는 모습을 분리해서 봅니다.

## Operations Advisor

Operations Advisor는 rule-based AX 보조 영역입니다. AI API를 호출하지 않습니다.

현재 역할:
- 예약, Kafka 적재, DB 저장, DLQ 신호를 해석합니다.
- 운영자가 다음에 볼 항목을 짧게 제시합니다.
- 향후 AI Worker가 같은 운영 신호를 읽어 설명을 생성할 수 있는 주입 지점을 보여줍니다.

중요한 경계:
- 핵심 주문 처리와 DB persistence path에는 AI를 넣지 않습니다.
- AI는 나중에 운영자 요약, 원인 후보 정리, runbook 추천 같은 보조 경로에 붙일 수 있습니다.

## Queue Reset And DB Reset

`전송 전 예약 비우기`:
- 아직 Kafka로 보내지 않은 예약만 취소합니다.
- 이미 전송이 시작된 이벤트는 Kafka 적재와 DB 저장까지 계속 추적합니다.

`RESET DEMO DB`:
- 로컬 데모 이벤트 DB를 초기화합니다.
- 사용자 계정은 유지하고 주문 stream, messages, request status, idempotency state, notification attempt 데이터를 지웁니다.
- `message-ingress-dlq` topic을 삭제 후 다시 만들어 DLQ summary도 비웁니다.
- 이 작업은 로컬 데모 reset입니다. 실제 운영에서는 DLQ 이력을 이런 식으로 지우지 않습니다.

## What To Say In An Interview

Korean:

> 이 데모는 사용자가 보는 결제 완료 응답 뒤에서 Kafka 기반 내부 이벤트 파이프라인이 어떻게 동작하는지 보여줍니다. API는 Kafka에 append하고 빠르게 수락 응답을 반환합니다. Worker는 consumer lag 기준으로 scale-out하면서 PostgreSQL에 최종 저장합니다. 장애 이벤트는 DLQ로 격리하고, 운영자는 Grafana와 DLQ summary로 상태를 확인합니다.

English:

> This demo shows what happens after the customer-facing payment/order completion response. The API appends post-order events to Kafka and returns `202 Accepted`. Workers consume the events, persist them to PostgreSQL, and scale by Kafka consumer lag. Failed events are isolated in DLQ, while Grafana and DLQ summary provide operator evidence.
