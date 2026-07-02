# 서비스 요구사항

주문 이후 이벤트 처리 포트폴리오는 쇼핑몰 사용자가 확인하는 완료 응답과 운영자가 관리하는 후속 처리 경로를 Kafka / Worker / PostgreSQL HA / DLQ / 관측성으로 연결합니다.

## 서비스 가정

기본 가정 서비스는 쇼핑몰 주문 이후 이벤트 처리입니다. 사용자는 결제 완료와 주문 완료 응답을 확인하고, 이후 저장 / 분류 / 알림 / 재처리 상태는 운영자가 관리합니다.

- 사용자는 결제 성공과 주문 생성 결과를 빠르게 확인해야 합니다.
- 주문 이후에는 결제 승인, 주문 생성, 배송 시작, 환불 요청, 주문 관련 문의 같은 event가 발생합니다.
- 같은 `order_id` 또는 업무 stream 안에서는 후속 event 순서가 운영 처리와 복구에 영향을 줍니다.
- 순간적인 트래픽 증가나 PostgreSQL write 지연이 있어도 API는 가능한 한 event를 수락해야 합니다.
- 영속화가 늦어지는 event는 추적 가능해야 하며, 실패 event는 DLQ와 replay 경로로 복구할 수 있어야 합니다.
- 분류는 큰 업무 단위로 시작합니다. 세부 AI 분류와 자동 응답은 후속 과제로 둡니다.

이 포트폴리오의 핵심 질문은 “DB write path가 흔들릴 때도 주문 이후 이벤트 수락, 순서, 복구, 관측을 어떻게 유지할 것인가”입니다.

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
| 복구 담당자 | 실패 event를 안전하게 재처리 | Kafka DLQ topic, DLQ summary API, manual replay API, replay count guard |
| 플랫폼 담당자 | 배포 상태와 runtime 상태를 분리해서 확인 | Argo CD `Synced / Healthy`, workload readiness, kafka-exporter |

## 기능 요구

- API는 정상 주문 이후 event를 Kafka ingress topic에 append하고 `202 Accepted`를 반환합니다.
- 사용자 응답은 결제 완료, 주문 완료, 주문 번호 같은 비즈니스 결과를 중심으로 구성합니다.
- Kafka append 이후의 `accepted`, `persisted`, `notified`, `dlq` 같은 내부 상태는 운영자 추적용으로 둡니다.
- event는 운영 카테고리로 분류할 수 있어야 합니다. 1차 범위는 `payment`, `order`, `delivery`, `refund`, `support`, `needs_review`입니다.
- 기본 read fallback은 Kafka ingress event가 아니라 DB commit 이후 snapshot 기반 local materialized cache로 조회할 수 있어야 합니다.
- message read는 fresh snapshot cache를 먼저 사용하고, cache miss / stale / DB failure 상태를 응답 메타데이터로 구분해야 합니다.
- 같은 주문 또는 업무 stream event는 같은 Kafka partition boundary 안에 유지합니다.
- Worker는 Kafka consumer group으로 event를 처리하고 PostgreSQL에 최종 영속화합니다.
- transient DB failure는 같은 offset에서 inline retry하여 뒤 event가 앞 event를 추월하지 않게 합니다.
- retry 한도를 넘긴 event는 Kafka DLQ topic으로 격리합니다.
- DLQ Replayer는 replay count guard를 지키며 복구 가능한 event만 ingress topic으로 재주입합니다.
- 운영자는 DLQ summary API로 reason, replayable, blocked, stream 분포를 확인할 수 있습니다.
- 운영자는 manual replay API 또는 데모 UI 버튼으로 replay 가능한 DLQ event를 재투입할 수 있어야 합니다.
- replay guard 도달 event는 자동/수동 replay보다 사용자 확인과 데이터 보정 대상으로 남겨야 합니다.
- 운영자는 Prometheus / Grafana / status check script로 intake, persistence, lag, DLQ, replica 상태를 확인할 수 있습니다.

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

아래 값은 장기 운영 SLA가 아니라, 운영형 데모에서 정상과 이상을 구분하기 위한 1차 SLO guardrail입니다.

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

- Kafka가 unavailable이면 request intake path 중단이므로 즉시 critical입니다.
- PostgreSQL primary가 흔들리더라도 Kafka append가 가능하면 API intake는 degraded로 볼 수 있습니다.
- Worker lag이 증가하면 먼저 Worker replica, KEDA desired replica, PostgreSQL write latency를 함께 봅니다.
- read cache hit ratio가 급락하거나 `snapshot_age_seconds`가 증가하면 snapshot consumer lag, API pod restart, compacted topic consume 상태를 먼저 확인합니다.
- `degraded=true`, `source=cache` 응답이 증가하면 PostgreSQL read path 장애가 사용자 read 경험에 전파되기 시작한 것으로 보고 DB primary / Pgpool / membership snapshot 상태를 함께 봅니다.
- 같은 주문 또는 업무 stream 순서가 깨졌다면 Kafka key뿐 아니라 Worker retry와 offset commit 경계를 확인합니다.
- DLQ가 증가하면 reason 분포를 보고 poison data, schema mismatch, DB transient failure를 분리합니다.
- `oldest_age_seconds`가 계속 증가하면 자동 replay가 되지 않는 운영 부채로 보고 replay 조건, blocked count, 원인 수정 여부를 먼저 확인합니다.
- replay 가능한 DLQ event가 여러 건이면 일괄 재처리 요청 후 DB 저장 완료와 남은 blocked event를 다시 확인합니다.
- GitOps가 `Synced / Healthy`가 아니면 runtime 장애 분석 전에 원하는 manifest와 live state 차이를 먼저 확인합니다.

## 구조 연결

- 빠른 event 수락: API는 PostgreSQL write보다 Kafka append를 우선합니다.
- 같은 주문 / 업무 stream ordering: `stream_id` key와 Worker inline retry가 같은 ordering boundary를 유지합니다.
- 장애 격리: Worker retry 한도 초과 event는 Kafka DLQ topic으로 이동합니다.
- 복구 가능성: DLQ Replayer와 manual replay API가 replay guard 안에서 event를 재주입합니다.
- 운영 가시성: Prometheus alert, Grafana dashboard, kafka-exporter, status check script가 같은 신호를 바라봅니다.
- 배포 일관성: Argo CD GitOps가 runtime manifest를 선언형으로 유지합니다.
