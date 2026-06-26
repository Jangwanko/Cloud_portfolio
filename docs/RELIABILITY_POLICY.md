# 신뢰성 정책

Kafka event intake, Worker persistence, PostgreSQL HA 기준 readiness / alert 정책.

- 상위 기준: [SERVICE_REQUIREMENTS.md](SERVICE_REQUIREMENTS.md)
- 이 문서 범위: runtime readiness와 alert 판단
- 제외 범위: 장기 SLA 선언

## 핵심 모델

- Kafka: request intake event log
- API: PostgreSQL 직접 write 제외, Kafka ingress topic append 후 `202 Accepted` 반환
- Worker consumer group: Kafka partition 소비, PostgreSQL 영속화
- retry 한도 초과 event: Kafka DLQ topic 이동
- DLQ Replayer: 복구 가능한 event ingress topic 재주입

설계 선택:

- 우선순위: 최소 latency보다 요청 수락 안정성과 복구 가능성
- 감수 비용: Kafka event log와 Worker persistence 경유 latency
- 확보 효과: DB 장애 전파 축소, replay 가능한 처리 경로

## Readiness 상태

### `ready`

- Kafka bootstrap reachable
- API Kafka publish path 정상
- PostgreSQL writable primary reachable
- PostgreSQL standby count가 로컬 HA 기준 충족
- Worker가 Kafka ingress topic을 consume 가능

### `degraded`

서비스는 동작하지만 처리 지연, HA 여력 저하, replay 증가가 관측되는 상태.

- PostgreSQL primary가 일시적으로 unavailable하지만 Kafka append path는 살아 있음
- PostgreSQL standby 부족 또는 replication lag 증가
- Worker backlog / consumer lag 증가
- DLQ replay 증가
- Worker replica는 증가했지만 persistence lag가 줄지 않음

### `not_ready`

새 write request를 정상 수락할 수 없는 상태.

- Kafka bootstrap unreachable
- Kafka ingress topic publish 실패
- API 내부 state path 장애로 request intake가 불가능

PostgreSQL writable primary unreachable 해석:

- API intake 관점: Kafka append path가 살아 있으면 `degraded`
- persistence 관점: PostgreSQL commit 중단
- alert 관점: critical
- 복구 경로: PostgreSQL 복구 후 Worker persistence 재개

## Alert 정책

- Kafka unavailable: intake write path 중단, 즉시 critical
- PostgreSQL primary loss: persistence 중단, 즉시 critical
- Worker consumer lag 증가: warning 시작, 지속 시 critical 승격
- DLQ 일시 증가: warning
- DLQ 같은 reason 반복: critical 후보
- PostgreSQL standby 부족 또는 replication lag 증가: degraded warning

## 장애 시나리오

### Kafka broker 장애

- API readiness: Kafka append 가능 시 `degraded` 유지
- 새 event append 실패: API fail-fast
- Kafka recovery 후: API publish와 Worker consume 정상화

### PostgreSQL / Pgpool 장애

- API는 Kafka append가 가능하면 request를 계속 accepted 할 수 있습니다.
- Worker persistence는 실패하고 retry 후 DLQ로 이동할 수 있습니다.
- 복구 후 DLQ Replayer event ingress topic 재주입

### Worker 포화

- Kafka consumer lag 증가
- KEDA Kafka scaler가 lag 기준으로 Worker replica를 늘립니다.
- replica 증가 후 lag 유지: DB persistence 병목 해석

### Poison event 처리

- retry 한도 초과 event: Kafka DLQ topic 이동
- DLQ payload `failed_reason`, `retry_count`, `replay_count` 확인
- 데이터 조건 문제가 해결되지 않으면 replay해도 다시 DLQ에 쌓일 수 있습니다.
- `DLQ_REPLAY_MAX_COUNT` 초과 event: DLQ Replayer ingress topic 재주입 제외

## 현재 메모

초기 Kafka 실험 확인:

- request status / idempotency / sequence 일부를 PostgreSQL state table에 배치
- API hot path가 Pgpool에 다시 묶이는 문제 확인
- 현재 Kafka 모드: Worker persistence 시점 sequence와 request status 갱신
- 현재 API intake: Kafka append 중심

장애별 확인 순서와 복구 절차: [RUNBOOK.md](RUNBOOK.md)

## 운영 알림 기준값

아래 값의 성격:

- Kafka event stream 포트폴리오 운영형 판단용 1차 기본값
- 장기 트래픽 기준값 축적 전 임시 SLO 가드레일
- 장애 조기 감지와 과도한 오탐 사이 균형 확인

| 신호 | Warning | Critical |
| --- | ---: | ---: |
| API 5xx ratio | 5분 동안 `> 1%` | 5분 동안 `> 5%` |
| API p95 latency | 10분 동안 `> 2s` | 5분 동안 `> 4s` |
| accepted-to-persisted p95 | 5분 동안 `> 5s` | 5분 동안 `> 15s` |
| Kafka topic wait p95 | 5분 동안 `> 10s` | 5분 동안 `> 30s` |
| Worker failure ratio | 5분 동안 `> 10%` | - |
| Worker last success age | 최근 처리량이 있는데 60초 이상 성공 없음 | - |
| DLQ events | 5분 안에 1건 이상 증가 | `skipped_max_replay` 누적값 `> 0` |
| DLQ oldest age | summary API `oldest_age_seconds > 600` | summary API `oldest_age_seconds > 1800` |
| PostgreSQL replication | standby 부족, non-streaming, 1MiB 초과 lag | primary down |
| Pod restarts | 15분 안에 restart 증가 | - |
| Deployment availability | 2분 이상 unavailable replica `> 0` | - |

알림 이름 기준:

- 파일: `monitoring/prometheus/alerts.yml`
- API: `MessagingApi5xxRateWarning`, `MessagingApiHigh5xxRate`
- persistence: `MessagingEventPersistLagHigh`, `MessagingEventPersistLagCritical`
- queue wait: `MessagingQueueWaitHigh`, `MessagingQueueWaitCritical`
- DLQ: `MessagingDlqEventsIncreasing`, `MessagingDlqReplayBlocked`
- 유지 기준: 문서와 매니페스트가 같은 값 참조

`DLQ oldest age` 기준:

- 현재 Prometheus counter 제외
- DLQ summary API 운영 판단 신호
- 확인 API: `GET /v1/dlq/ingress/summary`
- warning / critical 기준: `oldest_age_seconds`
- 우선 확인: `blocked`, `by_reason`, `recent_samples`
