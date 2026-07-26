# Reliability Policy

이 문서는 API readiness와 운영 alert의 의미를 분리합니다. 장기 SLA 선언은 범위에서 제외합니다.

## Reliability Claim Scope

`Reliable Event Processing System`의 이름은 다음 구현·검증 경계를 뜻합니다.

- per-stream Kafka partition ordering과 failed record inline retry
- 성공 또는 terminal 처리 뒤 record 단위 explicit offset commit
- PostgreSQL transaction/idempotency state 기반 중복 persistence 방어
- retry exhaustion 뒤 DLQ 격리와 replay guard
- accepted, persisted, consumer lag, DLQ 표본의 독립 관측
- DB outage, ordering, recovery 실험 원본 보존

현재 증명 범위에서 제외:

- exactly-once delivery
- partition 간 global ordering
- 모든 process/broker/database failure 조합에서의 무손실
- 검증 환경 밖 production SLA
- DB commit과 후속 Kafka publish의 원자성

## Processing Boundaries

- API: Kafka append 성공 뒤 `202 Accepted`
- Envelope: `schema_version`, `event_type`, JSON `payload`, JSON `metadata`
- Worker: Kafka record 처리와 PostgreSQL commit
- Post-commit publish: request status, snapshots, notification job
- Failure: inline retry → DLQ publish → replay guard
- Read model: DB-committed snapshot 기반 cache

DB commit과 후속 Kafka publish는 하나의 transaction이 아닙니다. 현재 publish는 best-effort이며 process crash gap은 남아 있습니다.

## API Readiness Contract

`GET /health/ready`가 직접 결정하는 상태:

### `ready`

- schema migration startup 완료
- Kafka bootstrap reachable
- PostgreSQL writable primary reachable
- HA mode의 ready/sync standby minimum 충족
- replication byte lag threshold 이내
- non-local environment의 기본값·빈 값·32-byte 미만 auth secret 미사용

### `degraded`

- schema/secret/Kafka hard failure 없음
- PostgreSQL writable primary unreachable, standby minimum 미달, sync standby minimum 미달, 또는 replication byte lag threshold 초과
- API intake는 Kafka append를 통해 계속 가능
- Worker persistence는 retry/backlog 상태로 전환 가능

### `not_ready`

- schema startup 미완료
- Kafka bootstrap unreachable
- non-local environment에서 unsafe auth secret 사용

Readiness state의 직접 조건에서 제외되는 신호:

- Kafka broker replica count / ISR
- Pgpool replica availability
- Worker/notification-worker replica와 consumer lag
- materialized cache ready/error telemetry
- DLQ / replay activity
- Prometheus scrape availability

이 신호들은 alerts와 `check_portfolio_status.ps1`로 확인합니다. readiness response의 Worker 정보가 있더라도 상태 결정 조건으로 해석하지 않습니다.

`grace_remaining_seconds`는 `degraded` 시작 뒤 `READINESS_DEGRADED_GRACE_SECONDS` 기준 남은 시간을 보여주는 context field입니다. `degraded` 판정을 늦추거나 HTTP status를 바꾸지 않습니다.

## Incident Interpretation

### Kafka broker loss

- 일부 broker 손실, bootstrap/append 가능: API readiness가 계속 `ready` 또는 DB 상태에 따른 `degraded`일 수 있음
- replication/ISR 감소: Kafka broker count와 exporter signal로 경고
- bootstrap/append 불가: `not_ready`, event intake 실패

### PostgreSQL / Pgpool loss

- API: Kafka append가 가능하면 accepted 유지
- Worker: DB retry, consumer lag 증가
- retry terminal: DLQ publish 가능
- recovery: Worker backlog와 replay path 처리

StatefulSet 재시작 뒤 pod `Ready`만으로 HA 복구를 완료 처리하지 않습니다. 모든 PostgreSQL pod의 persisted sync 설정을 복원하고 현재 primary에서 streaming `sync`/`quorum` standby 1개 이상을 확인해야 readiness의 HA guardrail을 충족합니다. 전체 outage/recovery 검증은 primary promotion/failover 성공 증거와 분리합니다.

짧은 장애가 항상 DLQ로 이어지는 것은 아닙니다. inline retry 안에 DB가 복구되면 같은 record에서 persistence를 재개합니다.

### Worker saturation

- message-worker consumer lag 증가
- KEDA가 lag 기준 replica 조정
- replica 증가 뒤 lag 유지: DB throughput, connection pool, stream lock, partition 분포 확인
- API request count: Worker scaling 효과의 단독 근거에서 제외

### Poison event

- validation rejection: request status `failed`, Worker result `rejected`
- retryable failure 한도 초과: DLQ publish
- `DLQ_REPLAY_MAX_COUNT` 도달: automatic requeue 제외
- 원인 수정 없는 replay: 같은 failure 반복 가능

## Alert Policy

매니페스트 기준 1차 guardrail:

| Signal | Warning | Critical |
| --- | ---: | ---: |
| API 5xx ratio | 5분 동안 `>1%` | 5분 동안 `>5%` |
| API p95 latency | 10분 동안 `>2s` | 5분 동안 `>4s` |
| Worker-observed accepted-to-commit p95 | 5분 동안 `>5s` | 5분 동안 `>15s` |
| Kafka topic wait / Kafka-to-Worker consume wait p95 | 5분 동안 `>10s` | 5분 동안 `>30s` |
| message-worker lag | 5분 동안 `>100` | 운영 escalation 기준 별도 |
| notification-worker lag | 5분 동안 `>100` | 운영 escalation 기준 별도 |
| DLQ publish | 5분 increase `>0` | replay guard blocked cumulative `>0` |
| PostgreSQL replication | standby/streaming/lag 기준 이탈 | primary down signal |
| Pod restart | 15분 increase `>0` | — |
| Deployment unavailable | 2분 이상 `>0` | — |

Metric 의미:

- `messaging_event_persist_lag_seconds`: API `queued_at`부터 Worker의 PostgreSQL `commit()` 반환 직후까지. post-commit publish 시간 제외, API/Worker clock 차이 고려
- PowerShell suite의 2026-06 `accepted-to-persisted`: PostgreSQL row `created_at` / row-visible proxy
- 현재 PowerShell `accepted_to_status_observed_ms`: client가 `persisted` status를 관측할 때까지이며 polling/network 포함. 위 Prometheus histogram과 별도 측정
- `messaging_queue_wait_seconds`: queued timestamp부터 Worker consume 시작까지의 근사치

## DLQ Signal Policy

운영에 사용할 수 있는 현재 신호:

- `messaging_dlq_events_total` increase
- `messaging_dlq_replay_total{result="skipped_max_replay"}`
- `MessagingDlqReplayBlocked`: replay guard blocked cumulative value 감지
- sampled `by_reason`, `blocked`, `recent_samples`

현재 제공하지 않는 신호:

- unresolved DLQ depth
- replay 완료를 반영한 current backlog
- oldest unresolved event age

Summary API의 `oldest_sample_age_seconds`는 조회한 append-only log sample의 age입니다. warning `10m` / critical `30m` unresolved SLO로 사용하지 않습니다.

## Recovery Completion

incident 종료 조건:

- Kafka append / consume 정상
- PostgreSQL primary write 정상
- message-worker lag 감소 후 기대 수준 복귀
- accepted 수와 persisted 수 reconciliation
- DLQ / replay terminal event 조사
- post-commit status/snapshot/notification 누락 확인
- customer/event producer 재시도 영향 확인

서비스 요구와 SLO guardrail은 [SERVICE_REQUIREMENTS.md](SERVICE_REQUIREMENTS.md), 세부 절차는 [RUNBOOK.md](RUNBOOK.md), 개선 완료 조건은 [IMPROVEMENT_ROADMAP.md](IMPROVEMENT_ROADMAP.md)에 있습니다.
