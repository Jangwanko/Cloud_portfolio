# 서비스 요구사항

- 대상 서비스: 쇼핑몰 주문 이후 이벤트 처리
- 사용자 화면: 결제 완료 / 주문 완료 응답
- 내부 경로: Kafka / Worker / PostgreSQL HA / DLQ / 관측성
- 운영 목적: 저장, 분류, 알림, 실패 격리, 재처리 확인

## 서비스 가정

기본 가정 서비스:

- 결제 성공과 주문 생성 결과 빠른 확인
- 주문 이후 결제 승인, 주문 생성, 배송 시작, 환불 요청, 주문 관련 문의 event 발생
- 같은 `order_id` 또는 업무 stream 안의 event 순서가 운영 처리와 복구에 영향
- 순간 트래픽 증가나 PostgreSQL write 지연 중에도 가능한 event 수락
- 영속화 지연 event 추적
- 실패 event DLQ 격리와 replay 복구
- 1차 분류 범위: 큰 업무 단위
- 세부 AI 분류와 자동 응답: 후속 과제

핵심 질문:

- DB write path 장애 중 주문 이후 이벤트 수락 유지
- 같은 stream 순서 유지
- 실패 event 복구 경로 유지
- 운영자가 볼 수 있는 관측 신호 유지

## 적용 가능한 서비스 관점

| 서비스 관점 | ordering key 예시 | 이 구조가 맞는 이유 |
| --- | --- | --- |
| 주문 이후 이벤트 처리 | `order_id`, `payment_id` | 결제 승인, 주문 생성, 배송, 환불 이벤트가 순서와 복구를 요구 |
| 알림 발송 파이프라인 | `user_id`, `notification_id` | 발송 요청을 빠르게 수락하고 실패 발송을 DLQ / replay로 다룸 |
| 고객 문의 / CS 이벤트 | `order_id`, `ticket_id` | 주문 관련 문의를 업무 카테고리로 분류하고 운영 큐에서 처리 |
| 실시간 협업 메시징 | `stream_id`, `room_id` | 같은 stream message 순서와 unread / status 갱신이 중요 |
| 감사 로그 / 활동 로그 | `actor_id`, `resource_id` | 이벤트 유실 방지와 장애 후 재처리가 중요 |
| IoT / 센서 수집 | `device_id` | 같은 장비의 시계열 이벤트 순서와 backlog 관측이 중요 |

## 사용자와 관심사

| 사용자 | 관심사 | 시스템 기준 |
| --- | --- | --- |
| 쇼핑몰 사용자 | 결제 완료와 주문 완료를 빠르게 확인 | API `202 Accepted`, order / payment response, 내부 Kafka 상태 비노출 |
| 서비스 운영자 | 주문 이후 event 처리 상태와 업무 분류 확인 | request status, event category, DB snapshot materialized cache |
| CS / 정산 담당자 | 결제 / 배송 / 환불 / 문의 이벤트를 큰 분류로 확인 | `payment`, `order`, `delivery`, `refund`, `support`, `needs_review` |
| 장애 운영자 | 장애 위치와 영향 범위를 빠르게 구분 | readiness, Prometheus alert, Grafana dashboard, runbook |
| 복구 담당자 | 실패 event를 안전하게 재처리 | Kafka DLQ topic, DLQ summary API, replay count guard |
| 플랫폼 담당자 | 배포 상태와 runtime 상태를 분리해서 확인 | Argo CD `Synced / Healthy`, workload readiness, kafka-exporter |

## 기능 요구

- API: 정상 주문 이후 event를 Kafka ingress topic에 append, `202 Accepted` 반환
- 사용자 응답: 결제 완료, 주문 완료, 주문 번호 중심
- 내부 상태: `accepted`, `persisted`, `notified`, `dlq` 운영자 추적용
- 운영 카테고리: `payment`, `order`, `delivery`, `refund`, `support`, `needs_review`
- read fallback: Kafka ingress event 제외, DB commit 이후 snapshot 기반 local materialized cache 사용
- message read: fresh snapshot cache 우선, cache miss / stale / DB failure 응답 메타데이터 구분
- 같은 주문 또는 업무 stream event: 같은 Kafka partition boundary 안에 유지
- Worker: Kafka consumer group event 처리, PostgreSQL 최종 영속화
- transient DB failure: 같은 offset에서 inline retry, 뒤 event 추월 방지
- retry 한도 초과 event: Kafka DLQ topic 격리
- DLQ Replayer: replay count guard 유지, 복구 가능한 event만 ingress topic 재주입
- DLQ summary API: reason, replayable, blocked, stream 분포 확인
- 운영 확인: Prometheus / Grafana / status check script 기반 intake, persistence, lag, DLQ, replica 상태 확인

## 비기능 요구

| 영역 | 기준 | 확인 방법 |
| --- | --- | --- |
| Request intake | Kafka append 중심 경로에서 100 VU / 30초 기준 error `0.00%` | `scripts/run_kafka_performance_suite.ps1` |
| API latency | 100 VU / 30초 기준 p95 `80.65ms`, p99 `103.57ms` baseline | k6 `http_req_duration` |
| Persistence lag | accepted-to-persisted p95 warning `> 5s`, critical `> 15s` | `messaging_event_persist_lag_seconds` |
| Kafka backlog | topic wait p95 warning `> 10s`, critical `> 30s` | `messaging_queue_wait_seconds` |
| Consumer lag | `message-worker` lag이 낮은 값으로 회복되어야 함 | `kafka_consumergroup_lag` |
| Read cache hit ratio | 정상 read traffic에서 fresh snapshot cache 응답 비율을 추적 | 현재는 `source=cache` 응답 샘플 / 향후 Prometheus counter 후보 |
| Snapshot age | cached read의 snapshot age가 stale 기준을 넘지 않아야 함 | `snapshot_age_seconds`, warning `> 30s`, critical `> 120s` |
| Cache rebuild time | API pod 재시작 후 snapshot topic replay로 read cache가 복구되는 시간 | pod restart 후 첫 `source=cache` 응답까지의 시간 |
| Stale response count | DB failure 중 stale snapshot fallback이 얼마나 발생하는지 추적 | `degraded=true`, `source=cache` 응답 count |
| Degraded read count | read path가 DB fallback 실패 또는 stale cache fallback에 의존하는 빈도 | `degraded=true` 응답 count |
| Snapshot consumer lag | `message-snapshots` / `stream-snapshots` consumer lag이 낮게 유지되어야 함 | snapshot cache consumer group lag |
| DLQ age | 가장 오래된 DLQ event age가 warning `> 10m`, critical `> 30m` 전에 처리되어야 함 | `GET /v1/dlq/ingress/summary`의 `oldest_age_seconds` |
| Availability topology | 로컬 Kafka 3 broker, PostgreSQL 3 replica, Pgpool 2 replica | `scripts/check_portfolio_status.ps1` |
| Recovery | poison event가 DLQ에 도달하고 replay guard가 동작 | `scripts/test_dlq_flow.ps1`, `scripts/test_dlq_replay_guard.ps1` |
| Deployment consistency | GitOps desired state와 live state 일치 | Argo CD `Synced / Healthy` |

## SLO 가드레일

아래 값의 성격:

- 장기 운영 SLA 제외
- 운영형 데모에서 정상 / 이상 구분
- 1차 SLO guardrail

| 신호 | Warning | Critical |
| --- | ---: | ---: |
| API 5xx ratio | 5분 동안 `> 1%` | 5분 동안 `> 5%` |
| API p95 latency | 10분 동안 `> 2s` | 5분 동안 `> 4s` |
| accepted-to-persisted p95 | 5분 동안 `> 5s` | 5분 동안 `> 15s` |
| Kafka topic wait p95 | 5분 동안 `> 10s` | 5분 동안 `> 30s` |
| Worker failure ratio | 5분 동안 `> 10%` | - |
| Read cache snapshot age | `snapshot_age_seconds > 30s` | `snapshot_age_seconds > 120s` |
| Degraded read ratio | 5분 동안 `> 1%` | 5분 동안 `> 5%` |
| Snapshot consumer lag | 5분 동안 지속 증가 | read cache rebuild 지연 또는 stale read 증가 |
| DLQ event 증가 | 5분 안에 1건 이상 증가 | `skipped_max_replay > 0` |
| DLQ oldest age | `oldest_age_seconds > 600` | `oldest_age_seconds > 1800` |
| PostgreSQL replication | standby 부족, non-streaming, 1MiB 초과 lag | primary down |
| Deployment availability | 2분 이상 unavailable replica `> 0` | - |

## 운영 판단 기준

- Kafka unavailable: request intake path 중단, 즉시 critical
- PostgreSQL primary 불안정 + Kafka append 가능: API intake degraded
- Worker lag 증가: Worker replica, KEDA desired replica, PostgreSQL write latency 동시 확인
- read cache hit ratio 급락 또는 `snapshot_age_seconds` 증가: snapshot consumer lag, API pod restart, compacted topic consume 상태 확인
- `degraded=true`, `source=cache` 증가: DB primary / Pgpool / membership snapshot 상태 확인
- 같은 주문 또는 업무 stream 순서 이상: Kafka key, Worker retry, offset commit 경계 확인
- DLQ 증가: reason 분포 기준 poison data, schema mismatch, DB transient failure 분리
- `oldest_age_seconds` 지속 증가: replay 조건, blocked count, 원인 수정 여부 확인
- GitOps `Synced / Healthy` 불일치: manifest와 live state 차이 먼저 확인

## 구조 연결

- 빠른 event 수락: PostgreSQL write보다 Kafka append 우선
- 같은 주문 / 업무 stream ordering: `stream_id` key와 Worker inline retry로 ordering boundary 유지
- 장애 격리: Worker retry 한도 초과 event Kafka DLQ topic 이동
- 복구 가능성: DLQ Replayer replay guard 안에서 event 재주입
- 운영 가시성: Prometheus alert, Grafana dashboard, kafka-exporter, status check script 신호 공유
- 배포 일관성: Argo CD GitOps로 runtime manifest 선언형 유지
