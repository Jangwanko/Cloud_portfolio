# Demo Guide

## Purpose

- 브라우저에서 주문 이후 이벤트 처리 흐름을 확인한다.
- 확인 흐름:
  - API intake
  - Kafka append
  - Worker persistence
  - PostgreSQL storage
  - DLQ / Operations Advisor 상태

## Demo URLs

| Surface | URL | Use |
| --- | --- | --- |
| Demo UI | `http://localhost/demo/order-dashboard.html` | 주문 이후 이벤트 흐름 시연 |
| Swagger | `http://localhost/docs` | API contract 확인 |
| Grafana | `http://localhost/grafana/d/messaging-portfolio-overview/messaging-portfolio-operations-overview?orgId=1&refresh=5s` | Kafka lag, Worker replica, persistence 지연 확인 |
| Readiness | `http://localhost/health/ready` | Kafka / PostgreSQL 상태 확인 |
| DLQ summary | `http://localhost/v1/dlq/ingress/summary?limit=200&sample_limit=5` | DLQ reason, replayable, blocked 확인 |

## Quick Start

- 처음 실행:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1
```

- 이미 클러스터가 있고 데모 화면만 갱신:

```powershell
docker build -t messaging-portfolio:local .
tools\kind.exe load docker-image messaging-portfolio:local --name messaging-ha
kubectl rollout restart deployment/api -n messaging-app
kubectl rollout status deployment/api -n messaging-app --timeout=180s
```

## Demo Flow

- `http://localhost/demo/order-dashboard.html` 접속
- 외국인 리크루터에게 보여줄 때 `EN` 선택
- `샘플 10개 추가`, `샘플 100개 추가`, `샘플 1000개 추가` 중 하나 선택
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

## English Demo Script

- Open `http://localhost/demo/order-dashboard.html`.
- Click `EN`.
- Click `Add 10 Samples`, `Add 100 Samples`, or `Add 1000 Samples`.
- Click `Send Post-Order Events`.
- Watch the counters move from `Reserved` to `Kafka Appended` to `DB Persisted`.
- Open Grafana to show consumer lag and Worker scaling evidence.

## Counter Meaning

| Counter | Meaning |
| --- | --- |
| `예약 건수` / `Reserved` | 전송 시작 후 `남은 예약/전체 예약`으로 표시. Kafka append 성공 시 감소 |
| `Kafka 적재` / `Kafka Appended` | API가 `message-ingress` topic append에 성공한 수 |
| `DB 저장` / `DB Persisted` | Worker가 PostgreSQL commit까지 끝낸 수 |
| `총 소요시간` / `Total Elapsed` | 전송 시작부터 현재 run의 DB 저장 완료까지 걸린 시간 |
| `Worker` | 현재 Worker replica / 최대 replica. 예: `2/8`, `6/8` |

## Read The Counters This Way

- `예약 건수`와 `DB 저장`은 같은 숫자가 아니다.
- API는 Kafka에 event를 전달한다.
- Worker는 Kafka event를 소비해 DB에 저장한다.
- Kafka append가 먼저 움직이고, DB 저장이 뒤따라온다.
- 이 차이가 데모에서 봐야 할 핵심이다.

## Operations Advisor

- 역할:
  - 예약, Kafka 적재, DB 저장, DLQ 신호 해석
  - 위험 상태와 다음 확인 항목 표시
  - 운영자가 봐야 할 신호 요약
- 현재 방식:
  - rule-based
  - AI API 미사용
- 확장 여지:
  - 별도 AI Worker
  - 운영 요약 생성
  - 원인 후보 정리
  - runbook 추천
- 경계:
  - 핵심 주문 처리 path에서 AI 제외
  - AI는 운영 보조 경로에만 배치

## Queue Reset And DB Reset

`전송 전 예약 비우기`:

- 아직 Kafka로 보내지 않은 예약만 취소
- 이미 시작된 event는 Kafka 적재와 DB 저장까지 계속 추적

`RESET DEMO DB`:

- 로컬 데모 이벤트 DB 초기화
- 유지:
  - 사용자 계정
- 삭제:
  - 주문 stream
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

## Interview Lines

Korean:

> 이 데모는 결제 완료 이후의 내부 이벤트 처리를 보여줍니다. API는 Kafka에 event를 적재하고 빠르게 수락 응답을 반환합니다. Worker는 Kafka event를 소비해 PostgreSQL에 저장하고, 실패 event는 DLQ로 격리합니다. 운영자는 Grafana, DLQ summary, Operations Advisor로 처리 상태를 확인합니다.

English:

> This demo shows post-order event processing after the customer-facing completion response. The API appends events to Kafka and returns `202 Accepted`. Workers persist the events to PostgreSQL, failed events move to DLQ, and the operator checks the state through Grafana, DLQ summary, and Operations Advisor.
