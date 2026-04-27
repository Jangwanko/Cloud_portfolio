# Reliability Policy

이 문서는 Kafka event intake, Worker persistence, PostgreSQL HA 기준의 readiness / alert 해석 정책을 정리합니다.

## Core Model

- Kafka는 request intake의 event log입니다.
- API는 요청을 PostgreSQL에 직접 쓰지 않고 Kafka ingress topic에 append한 뒤 `202 Accepted`를 반환합니다.
- Worker consumer group은 Kafka partition을 소비해 PostgreSQL에 영속화합니다.
- retry 한도를 넘긴 event는 Kafka DLQ topic으로 이동합니다.
- DLQ Replayer는 복구 가능한 event를 ingress topic으로 재주입합니다.

Design choice: 이 시스템은 최소 latency보다 요청 수락 안정성과 복구 가능성을 우선합니다. Kafka event log와 Worker persistence를 거치며 일부 latency를 감수하지만, DB 장애 전파를 줄이고 replay 가능한 처리 경로를 확보합니다.

## Readiness States

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

## Alert Policy

- Kafka unavailable은 intake write path 중단이므로 즉시 critical입니다.
- PostgreSQL primary loss는 persistence 중단이므로 즉시 critical입니다.
- Worker consumer lag 증가는 warning에서 시작하고, 일정 시간 이상 지속되면 critical로 승격합니다.
- DLQ 증가가 일시적이면 warning, 같은 reason으로 반복되면 critical 후보입니다.
- PostgreSQL standby 부족이나 replication lag 증가는 degraded warning으로 봅니다.

## Failure Scenarios

### Kafka broker unavailable

- API readiness는 Kafka append가 가능하면 `degraded`로 유지됩니다.
- 새 event append가 실패하면 API는 fail-fast합니다.
- Kafka recovery 후 API publish와 Worker consume이 정상화됩니다.

### PostgreSQL / Pgpool unavailable

- API는 Kafka append가 가능하면 request를 계속 accepted 할 수 있습니다.
- Worker persistence는 실패하고 retry 후 DLQ로 이동할 수 있습니다.
- 복구 후 DLQ Replayer가 event를 ingress topic으로 재주입합니다.

### Worker saturation

- Kafka consumer lag가 증가합니다.
- KEDA Kafka scaler가 lag 기준으로 Worker replica를 늘립니다.
- replica 증가 후에도 lag가 줄지 않으면 DB persistence 병목으로 해석합니다.

### Poison event

- retry 한도를 넘긴 event는 Kafka DLQ topic으로 이동합니다.
- DLQ payload의 `failed_reason`, `retry_count`, `replay_count`를 확인합니다.
- 데이터 조건 문제가 해결되지 않으면 replay해도 다시 DLQ에 쌓일 수 있습니다.

## Current Note

초기 Kafka 실험에서는 request status / idempotency / sequence 일부를 PostgreSQL state table에 배치했고, 이 방식이 API hot path를 Pgpool에 다시 묶을 수 있음을 확인했습니다.

기본 Kafka 모드는 Worker가 persistence 시점에 sequence와 request status를 갱신하며, API intake는 Kafka append 중심으로 동작합니다.
