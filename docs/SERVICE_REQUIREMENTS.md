# 서비스 요구사항

이 문서는 Kafka 이벤트 스트림 포트폴리오가 어떤 서비스 상황을 가정하고 설계되었는지 정리합니다. 구조 설명만으로 끝내지 않고, 사용자가 겪는 문제와 운영자가 지켜야 하는 기준을 먼저 둔 뒤 Kafka / Worker / PostgreSQL HA / DLQ / 관측성을 연결합니다.

## 서비스 가정

기본 가정 서비스는 실시간 협업 메시징입니다. 다만 이 구조의 핵심은 특정 메시징 화면보다 “순서가 중요하고 유실되면 안 되는 event request를 먼저 받아두고, 뒤에서 처리 / 복구 / 관측하는 방식”입니다.

- 사용자는 여러 stream 또는 room에 참여하고 짧은 message event를 계속 보냅니다.
- 같은 stream 안에서는 message 순서가 사용자 경험에 직접 영향을 줍니다.
- 순간적인 트래픽 증가나 PostgreSQL write 지연이 있어도 API는 가능한 한 request를 수락해야 합니다.
- 영속화가 늦어지는 event는 추적 가능해야 하며, 실패 event는 DLQ와 replay 경로로 복구할 수 있어야 합니다.

이 포트폴리오의 핵심 질문은 “DB write path가 흔들릴 때도 메시지 수락, 순서, 복구, 관측을 어떻게 유지할 것인가”입니다.

## 적용 가능한 서비스 관점

| 서비스 관점 | ordering key 예시 | 이 구조가 맞는 이유 |
| --- | --- | --- |
| 실시간 협업 메시징 | `stream_id`, `room_id` | 같은 stream message 순서와 unread / status 갱신이 중요 |
| 주문 / 결제 이벤트 | `order_id` | 주문 생성, 결제 승인, 재고 차감 같은 단계가 순서와 복구를 요구 |
| 알림 발송 파이프라인 | `user_id`, `notification_id` | 발송 요청을 빠르게 수락하고 실패 발송을 DLQ / replay로 다룸 |
| 감사 로그 / 활동 로그 | `actor_id`, `resource_id` | 이벤트 유실 방지와 장애 후 재처리가 중요 |
| IoT / 센서 수집 | `device_id` | 같은 장비의 시계열 이벤트 순서와 backlog 관측이 중요 |

## 사용자와 관심사

| 사용자 | 관심사 | 시스템 기준 |
| --- | --- | --- |
| 메시지를 보내는 사용자 | 요청이 빠르게 수락되고 중복 처리되지 않음 | API `202 Accepted`, request status 조회, idempotency guard |
| 같은 stream을 보는 사용자 | 같은 stream message가 순서대로 보임 | Kafka `stream_id` key, partition ordering boundary, Worker inline retry |
| 운영자 | 장애 위치와 영향 범위를 빠르게 구분 | readiness, Prometheus alert, Grafana dashboard, runbook |
| 복구 담당자 | 실패 event를 안전하게 재처리 | Kafka DLQ topic, DLQ summary API, replay count guard |
| 플랫폼 담당자 | 배포 상태와 runtime 상태를 분리해서 확인 | Argo CD `Synced / Healthy`, workload readiness, kafka-exporter |

## 기능 요구

- API는 정상 request를 Kafka ingress topic에 append하고 `202 Accepted`를 반환합니다.
- 같은 stream event는 같은 Kafka partition boundary 안에 유지합니다.
- Worker는 Kafka consumer group으로 event를 처리하고 PostgreSQL에 최종 영속화합니다.
- transient DB failure는 같은 offset에서 inline retry하여 뒤 event가 앞 event를 추월하지 않게 합니다.
- retry 한도를 넘긴 event는 Kafka DLQ topic으로 격리합니다.
- DLQ Replayer는 replay count guard를 지키며 복구 가능한 event만 ingress topic으로 재주입합니다.
- 운영자는 DLQ summary API로 reason, replayable, blocked, stream 분포를 확인할 수 있습니다.
- 운영자는 Prometheus / Grafana / status check script로 intake, persistence, lag, DLQ, replica 상태를 확인할 수 있습니다.

## 비기능 요구

| 영역 | 기준 | 확인 방법 |
| --- | --- | --- |
| Request intake | Kafka append 중심 경로에서 100 VU / 30초 기준 error `0.00%` | `scripts/run_kafka_performance_suite.ps1` |
| API latency | 100 VU / 30초 기준 p95 `80.65ms`, p99 `103.57ms` baseline | k6 `http_req_duration` |
| Persistence lag | accepted-to-persisted p95 warning `> 5s`, critical `> 15s` | `messaging_event_persist_lag_seconds` |
| Kafka backlog | topic wait p95 warning `> 10s`, critical `> 30s` | `messaging_queue_wait_seconds` |
| Consumer lag | `message-worker` lag이 낮은 값으로 회복되어야 함 | `kafka_consumergroup_lag` |
| DLQ age | 가장 오래된 DLQ event age가 warning `> 10m`, critical `> 30m` 전에 처리되어야 함 | `GET /v1/dlq/ingress/summary`의 `oldest_age_seconds` |
| Availability topology | 로컬 Kafka 3 broker, PostgreSQL 3 replica, Pgpool 2 replica | `scripts/check_portfolio_status.ps1` |
| Recovery | poison event가 DLQ에 도달하고 replay guard가 동작 | `scripts/test_dlq_flow.ps1`, `scripts/test_dlq_replay_guard.ps1` |
| Deployment consistency | GitOps desired state와 live state 일치 | Argo CD `Synced / Healthy` |

## SLO 가드레일

이 값은 포트폴리오용 1차 운영 기준입니다. 실제 장기 트래픽에서 얻은 SLA가 아니라, 운영형 데모에서 정상과 이상을 구분하기 위한 SLO guardrail입니다.

| 신호 | Warning | Critical |
| --- | ---: | ---: |
| API 5xx ratio | 5분 동안 `> 1%` | 5분 동안 `> 5%` |
| API p95 latency | 10분 동안 `> 2s` | 5분 동안 `> 4s` |
| accepted-to-persisted p95 | 5분 동안 `> 5s` | 5분 동안 `> 15s` |
| Kafka topic wait p95 | 5분 동안 `> 10s` | 5분 동안 `> 30s` |
| Worker failure ratio | 5분 동안 `> 10%` | - |
| DLQ event 증가 | 5분 안에 1건 이상 증가 | `skipped_max_replay > 0` |
| DLQ oldest age | `oldest_age_seconds > 600` | `oldest_age_seconds > 1800` |
| PostgreSQL replication | standby 부족, non-streaming, 1MiB 초과 lag | primary down |
| Deployment availability | 2분 이상 unavailable replica `> 0` | - |

## 운영 판단 기준

- Kafka가 unavailable이면 request intake path 중단이므로 즉시 critical입니다.
- PostgreSQL primary가 흔들리더라도 Kafka append가 가능하면 API intake는 degraded로 볼 수 있습니다.
- Worker lag이 증가하면 먼저 Worker replica, KEDA desired replica, PostgreSQL write latency를 함께 봅니다.
- 같은 stream 순서가 깨졌다면 Kafka key뿐 아니라 Worker retry와 offset commit 경계를 확인합니다.
- DLQ가 증가하면 reason 분포를 보고 poison data, schema mismatch, DB transient failure를 분리합니다.
- `oldest_age_seconds`가 계속 증가하면 자동 replay가 되지 않는 운영 부채로 보고 replay 조건, blocked count, 원인 수정 여부를 먼저 확인합니다.
- GitOps가 `Synced / Healthy`가 아니면 runtime 장애 분석 전에 원하는 manifest와 live state 차이를 먼저 확인합니다.

## 구조 연결

- 빠른 request 수락: API는 PostgreSQL write보다 Kafka append를 우선합니다.
- 같은 stream ordering: `stream_id` key와 Worker inline retry가 같은 ordering boundary를 유지합니다.
- 장애 격리: Worker retry 한도 초과 event는 Kafka DLQ topic으로 이동합니다.
- 복구 가능성: DLQ Replayer가 replay guard 안에서 event를 재주입합니다.
- 운영 가시성: Prometheus alert, Grafana dashboard, kafka-exporter, status check script가 같은 신호를 바라봅니다.
- 배포 일관성: Argo CD GitOps가 runtime manifest를 선언형으로 유지합니다.
