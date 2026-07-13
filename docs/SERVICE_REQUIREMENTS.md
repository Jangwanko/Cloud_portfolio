# Reliable Event Processing System 요구사항

- 대상 서비스: domain-neutral typed event acceptance, persistence, failure recovery
- 핵심 contract: `POST /v2/streams/{stream_id}/events`
- envelope: client input `event_type`, JSON `payload`, JSON `metadata`; API-assigned `schema_version=2`
- reference scenario: 주문·결제 lifecycle과 order compatibility adapter
- 이 저장소 화면: event acceptance와 운영 demo
- 내부 경로: Kafka / Worker / PostgreSQL HA / DLQ / 관측성
- 운영 목적: 저장, metadata 전달, notification job 분리, 실패 격리, 재처리 확인

## 서비스 가정

기본 event producer 가정:

- producer는 domain event type과 JSON payload/metadata를 정의
- 같은 `stream_id` 안의 event 순서가 downstream 처리와 복구에 영향
- 순간 트래픽 증가나 PostgreSQL write 지연 중에도 가능한 event 수락
- 영속화 지연 event 추적
- 실패 event DLQ 격리와 replay 복구
- metadata 분류: producer 또는 domain adapter 책임
- 세부 AI 분류와 자동 응답: 후속 과제

핵심 질문:

- DB write path 장애 중 event 수락 경계 유지
- 같은 stream 순서 유지
- 실패 event 복구 경로 유지
- 운영자가 볼 수 있는 관측 신호 유지

## 적용 가능한 서비스 관점

| 서비스 관점 | ordering key 예시 | 이 구조가 맞는 이유 |
| --- | --- | --- |
| 주문 lifecycle reference | `order_id` | 결제 승인, 주문 생성, 배송, 환불 이벤트가 순서와 복구를 요구 |
| 알림 발송 파이프라인 | `user_id`, `notification_id` | 발송 요청을 빠르게 수락하고 실패 발송을 DLQ / replay로 다룸 |
| 고객 문의 / CS 이벤트 | `order_id`, `ticket_id` | 주문 관련 문의를 업무 카테고리로 분류하고 운영 큐에서 처리 |
| 실시간 협업 메시징 | `stream_id`, `room_id` | 같은 stream message 순서와 unread / status 갱신이 중요 |
| 감사 로그 / 활동 로그 | `actor_id`, `resource_id` | 이벤트 유실 방지와 장애 후 재처리가 중요 |
| IoT / 센서 수집 | `device_id` | 같은 장비의 시계열 이벤트 순서와 backlog 관측이 중요 |

## 사용자와 관심사

| 사용자 | 관심사 | 시스템 기준 |
| --- | --- | --- |
| generic event producer | typed JSON event 전달 | API `202 Accepted`, request/stream id, persistence 상태 분리 |
| 서비스 운영자 | event 처리 상태와 metadata 확인 | request status, generic envelope, DB snapshot materialized cache |
| reference scenario viewer | 결제 / 배송 / 환불 / 문의 sample 확인 | `reference.*` event type과 metadata classification 예시 |
| 장애 운영자 | 장애 위치와 영향 범위를 빠르게 구분 | readiness, Prometheus alert, Grafana dashboard, runbook |
| 복구 담당자 | 실패 event를 안전하게 재처리 | Kafka DLQ topic, DLQ summary API, replay count guard |
| 플랫폼 담당자 | 배포 상태와 runtime 상태를 분리해서 확인 | Argo CD `Synced / Healthy`, workload readiness, kafka-exporter |

## 기능 요구

- API: 정상 generic event를 Kafka ingress topic에 append, `202 Accepted` 반환
- generic input: `event_type` 1~50자와 `^[A-Za-z0-9][A-Za-z0-9._:-]*$` pattern, 필수 JSON object `payload` 최대 65,536 UTF-8 JSON bytes, JSON object `metadata` 기본 `{}`·최대 16,384 bytes
- HTTP transport: `REQUEST_BODY_MAX_BYTES` 기본 `1,048,576` bytes; declared/chunked body 모두 JSON parsing 전에 초과 요청을 `413`으로 거부
- JSON safety: `payload`와 `metadata`는 finite JSON, valid Unicode scalar, string object key, NUL/circular reference 제외, 최대 64단계 container 중첩
- request status visibility: Kafka append 직후 Worker가 status row를 만들기 전 GET은 잠시 `404`; 동일 `request_id` polling으로 persisted/failed terminal 상태 확인
- event producer 응답: `202 Accepted`, request/stream/actor metadata와 accepted envelope
- request persistence 상태: `accepted`, `persisted`, `failed`, `failed_dlq`
- notification 처리 증거: 별도 `notification_attempts` record. 외부 채널 실제 발송 성공 상태는 현재 범위에서 제외
- generic metadata: domain-specific classification과 external reference를 선택적으로 전달
- order reference adapter input: `event_type`, `body`, optional `payment_id`; metadata envelope로 변환
- legacy compatibility: body-only stream request와 기존 `category` / `payment_id` / `body` alias 유지
- read fallback: Kafka ingress event 제외, DB commit 이후 snapshot 기반 local materialized cache 사용
- cache hydration: 각 API pod가 snapshot 세 topic의 모든 partition을 beginning부터 replay하고 captured initial end offset에 도달한 뒤에만 `hydrated=true`
- message read: initial hydration 전 cache 사용 제외; PostgreSQL 정상 시 DB membership authorization 뒤 cached page가 latest stream sequence watermark와 연속으로 일치할 때만 fresh snapshot 사용
- degraded message read: PostgreSQL 장애 시 hydrated membership cache와 message snapshot이 함께 있을 때만 `source=cache`, `degraded=true`; 불충분하면 `503`
- 같은 업무 stream event: 같은 Kafka partition boundary 안에 유지
- Worker: Kafka consumer group event 처리, PostgreSQL 최종 영속화
- transient DB failure: 같은 offset에서 inline retry, 뒤 event 추월 방지
- retry 한도 초과 event: Kafka DLQ topic 격리
- DLQ Replayer: replay count guard 유지, 복구 가능한 event만 ingress topic 재주입
- DLQ summary API: reason, replayable, blocked, stream 분포 확인
- 운영 확인: Prometheus / Grafana / status check script 기반 intake, persistence, lag, DLQ, replica 상태 확인
- readiness: schema startup, Kafka, PostgreSQL primary/HA guardrail, non-local auth secret 상태 구분
- post-commit publish: 현재 best-effort, transactional outbox 후속 과제
- v2 deployment gate: GitOps base Secret gate `false`/wave `-3` → 일반 Sync migration `-2` → dual-read/dual-write Worker `-1` → overlay API gate `true`/wave `0`; 수동 local gate `false` → Worker ready → API env `true`
- 비대칭 호환: 구 Worker가 남아 있는 동안 v2 traffic 차단; legacy preview만 저장되어 `payload`/`metadata`가 유실되는 조합 제외

## 비기능 요구

아래 `31,676` request와 latency 수치는 legacy/order contract의 historical baseline입니다. generic v2 요구 충족 증거로 재사용하지 않으며 v2 rollout 뒤 동일 조건으로 재측정합니다.

`Reliable`의 요구 범위:

- per-stream partition ordering과 inline retry
- record 단위 explicit offset commit
- PostgreSQL transaction/idempotency state 기반 중복 persistence 방어
- DLQ 격리, bounded replay, status/snapshot 관측

요구·증명 범위 제외:

- exactly-once delivery
- partition 간 global ordering
- 모든 장애에서의 무손실
- 장기 production SLA
- DB commit과 post-commit Kafka publish의 원자성

| 영역 | 기준 | 확인 방법 |
| --- | --- | --- |
| Request intake | Kafka append 중심 경로에서 100 VU / 30초 기준 error `0.00%` | `scripts/run_kafka_performance_suite.ps1` |
| API latency | 100 VU / 30초 기준 p95 `80.65ms`, p99 `103.57ms` baseline | k6 `http_req_duration` |
| Persistence lag | Worker-observed accepted-to-commit p95 warning `> 5s`, critical `> 15s` | `messaging_event_persist_lag_seconds`; PowerShell status-observed와 역사적 row-visible proxy에서 분리 |
| Kafka backlog | topic wait p95 warning `> 10s`, critical `> 30s` | `messaging_queue_wait_seconds` |
| Consumer lag | `message-worker` lag이 낮은 값으로 회복되어야 함 | `kafka_consumergroup_lag` |
| Read cache hit ratio | 정상 read traffic에서 fresh snapshot cache 응답 비율을 추적 | 현재는 `source=cache` 응답 샘플 / 향후 Prometheus counter 후보 |
| Snapshot age | cached read의 snapshot age가 stale 기준을 넘지 않아야 함 | `snapshot_age_seconds`, warning `> 30s`, critical `> 120s` |
| Cache rebuild time | API pod 재시작 후 snapshot topic replay로 read cache가 복구되는 시간 | pod restart 후 첫 `source=cache` 응답까지의 시간 |
| Stale response count | DB failure 중 stale snapshot fallback이 얼마나 발생하는지 추적 | `degraded=true`, `source=cache` 응답 count |
| Degraded read count | read path가 DB fallback 실패 또는 stale cache fallback에 의존하는 빈도 | `degraded=true` 응답 count |
| Per-pod snapshot replay progress | initial end offset까지 남은 record와 hydration 시간을 추적해야 함 | 현재 미구현 custom metric; readiness `ready` / `hydrated` / `last_error`로 제한 확인 |
| DLQ sample age | 조회 표본의 시간 범위 확인 | `GET /v1/dlq/ingress/summary`의 `oldest_sample_age_seconds`; unresolved SLO 제외 |
| Availability topology | 로컬 Kafka 3 broker, PostgreSQL 3 replica, Pgpool 2 replica | `scripts/check_portfolio_status.ps1` |
| Recovery | poison event가 DLQ에 도달하고 replay guard가 동작 | `scripts/test_dlq_flow.ps1`, `scripts/test_dlq_replay_guard.ps1` |
| Deployment consistency | GitOps desired state와 live state 일치 | Argo CD `Synced / Healthy` |

Snapshot cache consumer는 consumer group을 사용하지 않으므로 `snapshot consumer group lag`는 현재 요구 충족 여부를 판정하는 지표가 아닙니다. Pod별 replay progress metric을 구현하기 전에는 startup 후 first hydrated time과 readiness payload를 수동 증거로 남깁니다.

## SLO 가드레일

아래 값의 성격:

- 장기 운영 SLA 제외
- 운영형 데모에서 정상 / 이상 구분
- 1차 SLO guardrail

용어 호환 메모:

- 과거 `accepted-to-persisted p95`: 2026-06 row-visible proxy 명칭. 현재 Worker accepted-to-commit-observed 지표와 분리
- `DLQ oldest age`: 현재 unresolved 상태 지표로 제공하지 않음. `oldest_sample_age_seconds`는 recent log sample의 시간 범위

| 신호 | Warning | Critical |
| --- | ---: | ---: |
| API 5xx ratio | 5분 동안 `> 1%` | 5분 동안 `> 5%` |
| API p95 latency | 10분 동안 `> 2s` | 5분 동안 `> 4s` |
| Worker-observed accepted-to-commit p95 | 5분 동안 `> 5s` | 5분 동안 `> 15s` |
| Kafka topic wait p95 | 5분 동안 `> 10s` | 5분 동안 `> 30s` |
| Worker failure ratio | 5분 동안 `> 10%` | - |
| Read cache snapshot age | `snapshot_age_seconds > 30s` | `snapshot_age_seconds > 120s` |
| Degraded read ratio | 5분 동안 `> 1%` | 5분 동안 `> 5%` |
| Per-pod snapshot replay remaining | custom metric 구현 뒤 기준 설정 | hydration 목표 시간 초과와 함께 critical 기준 설정 |
| DLQ event 증가 | 5분 안에 1건 이상 증가 | `skipped_max_replay > 0` |
| DLQ sample age | context only | unresolved state model 구현 뒤 SLO 정의 |
| PostgreSQL replication | standby 부족, non-streaming, 1MiB 초과 lag | primary down |
| Deployment availability | 2분 이상 unavailable replica `> 0` | - |

## 운영 판단 기준

- Kafka unavailable: request intake path 중단, 즉시 critical
- PostgreSQL primary 불안정 + Kafka append 가능: API intake degraded
- Worker lag 증가: Worker replica, KEDA desired replica, PostgreSQL write latency 동시 확인
- read cache hit ratio 급락 또는 `snapshot_age_seconds` 증가: API pod `ready` / `hydrated`, restart 시각, compacted topic consume 상태 확인
- `degraded=true`, `source=cache` 증가: DB primary / Pgpool / membership snapshot 상태 확인
- 같은 업무 stream 순서 이상: Kafka key, Worker retry, offset commit 경계 확인
- DLQ 증가: reason 분포 기준 poison data, schema mismatch, DB transient failure 분리
- `oldest_sample_age_seconds` 증가: 조회 sample의 역사 범위 확인. unresolved backlog 증가로 단정 제외
- GitOps `Synced / Healthy` 불일치: manifest와 live state 차이 먼저 확인

## 구조 연결

- 빠른 event 수락: PostgreSQL write보다 Kafka append 우선
- 같은 업무 stream ordering: `stream_id` key와 Worker inline retry로 ordering boundary 유지
- 장애 격리: Worker retry 한도 초과 event Kafka DLQ topic 이동
- 복구 가능성: DLQ Replayer replay guard 안에서 event 재주입
- 운영 가시성: Prometheus alert, Grafana dashboard, kafka-exporter, status check script 신호 공유
- 배포 일관성: Argo CD GitOps로 runtime manifest 선언형 유지
