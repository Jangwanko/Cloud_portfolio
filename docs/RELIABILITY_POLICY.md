# 신뢰성 정책

이 문서는 Kafka event intake, Worker persistence, PostgreSQL HA 기준의 readiness / alert 해석 정책을 정리합니다.

## 핵심 모델

- Kafka는 request intake의 event log입니다.
- API는 요청을 PostgreSQL에 직접 쓰지 않고 Kafka ingress topic에 append한 뒤 `202 Accepted`를 반환합니다.
- Worker consumer group은 Kafka partition을 소비해 PostgreSQL에 영속화합니다.
- retry 한도를 넘긴 event는 Kafka DLQ topic으로 이동합니다.
- DLQ Replayer는 복구 가능한 event를 ingress topic으로 재주입합니다.

설계 선택: 이 시스템은 최소 latency보다 요청 수락 안정성과 복구 가능성을 우선합니다. Kafka event log와 Worker persistence를 거치며 일부 latency를 감수하지만, DB 장애 전파를 줄이고 replay 가능한 처리 경로를 확보합니다.

## Readiness 상태

### `ready`

- Kafka bootstrap reachable
- API Kafka publish path 정상
- PostgreSQL writable primary reachable
- PostgreSQL standby count가 로컬 HA 기준 충족
- Worker가 Kafka ingress topic을 consume 가능

### `degraded`

서비스는 동작하지만 처리 지연, HA 여력 저하, replay 증가가 관측되는 상태입니다.

- PostgreSQL primary가 일시적으로 unavailable하지만 Kafka append path는 살아 있음
- PostgreSQL standby 부족 또는 replication lag 증가
- Worker backlog / consumer lag 증가
- DLQ replay 증가
- Worker replica는 증가했지만 persistence lag가 줄지 않음

### `not_ready`

새 write request를 정상 수락할 수 없는 상태입니다.

- Kafka bootstrap unreachable
- Kafka ingress topic publish 실패
- API 내부 state path 장애로 request intake가 불가능

PostgreSQL writable primary unreachable은 API intake 관점에서는 `degraded`로 해석할 수 있습니다. Kafka append path가 살아 있으면 새 request를 event log에 남길 수 있고, PostgreSQL 복구 후 Worker가 persistence를 이어갈 수 있기 때문입니다. 단, persistence path가 막힌 상태이므로 alert에서는 critical로 봅니다.

## Alert 정책

- Kafka unavailable은 intake write path 중단이므로 즉시 critical입니다.
- PostgreSQL primary loss는 persistence 중단이므로 즉시 critical입니다.
- Worker consumer lag 증가는 warning에서 시작하고, 일정 시간 이상 지속되면 critical로 승격합니다.
- DLQ 증가가 일시적이면 warning, 같은 reason으로 반복되면 critical 후보입니다.
- PostgreSQL standby 부족이나 replication lag 증가는 degraded warning으로 봅니다.

## 장애 시나리오

### Kafka broker 장애

- API readiness는 Kafka append가 가능하면 `degraded`로 유지됩니다.
- 새 event append가 실패하면 API는 fail-fast합니다.
- Kafka recovery 후 API publish와 Worker consume이 정상화됩니다.

### PostgreSQL / Pgpool 장애

- API는 Kafka append가 가능하면 request를 계속 accepted 할 수 있습니다.
- Worker persistence는 실패하고 retry 후 DLQ로 이동할 수 있습니다.
- 복구 후 DLQ Replayer가 event를 ingress topic으로 재주입합니다.

### Worker 포화

- Kafka consumer lag가 증가합니다.
- KEDA Kafka scaler가 lag 기준으로 Worker replica를 늘립니다.
- replica 증가 후에도 lag가 줄지 않으면 DB persistence 병목으로 해석합니다.

### Poison event 처리

- retry 한도를 넘긴 event는 Kafka DLQ topic으로 이동합니다.
- DLQ payload의 `failed_reason`, `retry_count`, `replay_count`를 확인합니다.
- 데이터 조건 문제가 해결되지 않으면 replay해도 다시 DLQ에 쌓일 수 있습니다.
- DLQ Replayer는 `DLQ_REPLAY_MAX_COUNT`를 넘긴 event를 다시 ingress topic으로 재주입하지 않습니다.

## 현재 메모

초기 Kafka 실험에서는 request status / idempotency / sequence 일부를 PostgreSQL state table에 배치했고, 이 방식이 API hot path를 Pgpool에 다시 묶을 수 있음을 확인했습니다.

기본 Kafka 모드는 Worker가 persistence 시점에 sequence와 request status를 갱신하며, API intake는 Kafka append 중심으로 동작합니다.

장애별 확인 순서와 복구 절차는 [RUNBOOK.md](RUNBOOK.md)에서 관리합니다.
## 운영 알림 기준값

아래 값은 Kafka event stream 포트폴리오를 운영형으로 보이게 하기 위한 1차 기본값입니다. 실제 장기 트래픽 기준값이 쌓이기 전까지는 장애 조기 감지와 과도한 오탐 사이의 균형을 보는 임시 SLO 가드레일로 사용합니다.

| 신호 | Warning | Critical |
| --- | ---: | ---: |
| API 5xx ratio | 5분 동안 `> 1%` | 5분 동안 `> 5%` |
| API p95 latency | 10분 동안 `> 2s` | 5분 동안 `> 4s` |
| accepted-to-persisted p95 | 5분 동안 `> 5s` | 5분 동안 `> 15s` |
| Kafka topic wait p95 | 5분 동안 `> 10s` | 5분 동안 `> 30s` |
| Worker failure ratio | 5분 동안 `> 10%` | - |
| Worker last success age | 최근 처리량이 있는데 60초 이상 성공 없음 | - |
| DLQ events | 5분 안에 1건 이상 증가 | `skipped_max_replay` 누적값 `> 0` |
| PostgreSQL replication | standby 부족, non-streaming, 1MiB 초과 lag | primary down |
| Pod restarts | 15분 안에 restart 증가 | - |
| Deployment availability | 2분 이상 unavailable replica `> 0` | - |

알림 이름은 `monitoring/prometheus/alerts.yml`의 `MessagingApi5xxRateWarning`, `MessagingApiHigh5xxRate`, `MessagingEventPersistLagHigh`, `MessagingEventPersistLagCritical`, `MessagingQueueWaitHigh`, `MessagingQueueWaitCritical`, `MessagingDlqEventsIncreasing`, `MessagingDlqReplayBlocked`를 기준으로 문서와 매니페스트가 같은 값을 바라보게 유지합니다.
