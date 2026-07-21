# Validation Results

이 문서는 포트폴리오의 현재 검증 상태와 역사적 측정 원본을 분리합니다. 문서 정합성 감사 기준은 `2026-07-14`, 최신 local cluster generic v2 성능 실행은 `2026-07-21`입니다. 판정 기준은 [SERVICE_REQUIREMENTS.md](SERVICE_REQUIREMENTS.md), 전체 점검 순서는 [SERVICE_PROCESS_CHECKLIST.md](SERVICE_PROCESS_CHECKLIST.md)를 사용합니다.

## Current Evidence Status

| Area | Current statement | Evidence status |
| --- | --- | --- |
| Generic event contract | `/v2/streams/{stream_id}/events`, versioned JSON envelope | tracked local image `9349ba9`에서 OpenAPI `2.0.0`, `202`, persistence/API contract 재검증; 성능 원본은 첫 후보 `d31ac14`와 recovery local image `perf-v16` 분리 |
| Generic rollout order | GitOps gate-false Secret `-3` → migration `-2` → Worker `-1` → overlay API-true `0`; manual false → Worker ready → API true | local staged rollout 완료; 대칭 호환 아님 |
| HTTP intake contract | Kafka append 성공 시 `202 Accepted` | 최신 2026-07-21 recovery suite에서 event `202` 응답 `29,608`, 다른 event status `0` |
| Generic v2 recovery candidate | 100 VU / 30s, 클린 조건 3회 평균 event `29,168`, error `0.00%`, p95 `101.27ms` | 첫 v2 후보보다 전 지표 개선; dirty local image/API min 6 조건, 안정 기준선 미채택 |
| First generic v2 candidate | 100 VU / 30s, event `25,378`, error `0.00%`, p95 `123.96ms` | 성능 저하 원인 분석의 비교 기준 |
| Kafka intake baseline | 100 VU / 30s, `31,676`, error `0.00%`, p95 `80.65ms` | 안정 기준선 유지 |
| Last legacy raw performance suite | 2026-06-18, `27,795`, p95 `119.28ms` | notification 경계 검증, 기준선 미채택 |
| Same-stream ordering | `stream_seq 1..100` | performance suite와 failure injection에서 통과 |
| Ordering / DB outage | 네 시나리오 accepted = persisted | 2026-06-08 원본 추적 |
| Materialized cache fallback | fresh cache와 DB-down stale cache | 2026-07-21 tracked rerun: fresh age `0.112s`, DB-down degraded age `11.462s`, recovery exit `0` |
| Worker scaling | Kafka consumer lag 기반 KEDA | lag / persistence proxy / drain 관찰 |
| Fixed Worker 대 KEDA | 64-stream 동일 조건 1회 A/B: fixed all drain `301.42s`, KEDA `261.17s`, KEDA final `8` | drain `13.35%` 감소; KEDA intake event `7.35%` 감소·p95 `25.62%` 증가, 반복 전 candidate |
| Worker offset / replay safety | explicit per-record commit, failed partition seek-back, DLQ batch DB recheck | local source/tests 구현; deployed crash/rebalance injection 대기 |
| Master GitOps supply chain | test gate → GHCR 12-char SHA → overlay bot commit | CI run `#55` validate/publish success; image `8f5d78c6963a`, bot commit `717e0ca`; master-targeted runtime rollout은 미검증 |
| Dev GitOps validation gate | `publish-dev-kafka-image` requires `validate`, exact SHA candidate digest verify, branch advance guard | local source와 contract test 반영; commit/push 및 remote Actions 실행 전이므로 배포 증거 제외 |
| Terraform blueprint | private EKS default, immutable ECR, RDS secret consistency | Terraform `1.15.8` SHA256 검증; fmt/init/validate 통과, plan/apply/AWS 배포 미실행 |
| PostgreSQL backup / restore | in-cluster Job `Completed`, PVC `Bound`; host dump `39,433,414` bytes를 disposable DB에 복원 | 10개 table count, Alembic `0008`, generic v2 row `33,840`, max id/sequence 일치; object storage/cluster-loss 복구는 미검증 |
| PostgreSQL restart sync recovery | StatefulSet `3→0→3`, 모든 pod persisted `ANY 1`, current primary sync/quorum `2` | tracked rerun: cache fallback `45.390s`, DB outage `43.008s`, recovery exit `0`; primary promotion은 별도 미검증 |
| Master source Demo UI `2.0.0` | generic v2 intake, order reference scenario, envelope evidence | source contract; local `dev-kafka` API 배포와 구분, UI render/flow 별도 확인 |
| Public demo-lite UI `1.4.1` | branch/deployment-specific | live GET: title `Post-Order Event Console`, API `1.0.0`, generic v2 없음, order event success `200`; master `2.0.0` 미배포 |
| Unit / contract / infrastructure suite | `363 passed` (2026-07-21) | cluster rollout·v2 performance와 별도 판정 |
| Local live cluster | Argo `Synced / Healthy`, deployment-bearing image-tag revision `b84c379`, API/Worker image `9349ba9`, API `2.0.0`, generic v2 enabled | core ready, cache `ready=true` / `hydrated=true`, API contract pass, normalized message/notification lag `0`; 이후 docs-only revision은 workload 변경 없음 |

원본 위치:

- [results/kafka-performance/latest.txt](../results/kafka-performance/latest.txt)
- [results/ordering-failure/latest.json](../results/ordering-failure/latest.json)
- [results/postgres-restore/latest.json](../results/postgres-restore/latest.json)
- [results/postgres-recovery/latest.json](../results/postgres-recovery/latest.json)
- [results evidence guide](../results/README.md)

## Evidence Vocabulary

### Generic v2 evidence

- 첫 v2 contract/performance 실행: 2026-07-21 local `dev-kafka`, source revision `1439be1`, API/Worker image `d31ac14`
- recovery 반복: dirty `dev-kafka` worktree, local image `perf-v16`, clean DB/topic hot-stream 조건 3회
- 검증 범위: OpenAPI `2.0.0`, generic v2 gate, event `202`, persistence status, same-stream ordering, Kafka lag drain
- 성능 판정: recovery 3회는 첫 후보보다 전 지표 개선; historical stable legacy와 registry image 검증에는 미달해 stable baseline 미채택
- 2026-04/06 Kafka baseline: legacy/order request shape와 historical response `200`으로 수집
- 사용 가능 범위: Kafka append-first architecture와 당시 intake baseline
- 사용 제외: generic JSON envelope의 serialization/validation 비용, v2 route 성능, v2 `202` 배포 증거
- rollout 검증 순서: GitOps gate-false Secret wave `-3` → 일반 Sync migration Job wave `-2` → Worker `-1` → overlay API-true wave `0`; 수동 local은 gate `false` → Worker ready → API env `true`
- 대칭 rolling compatibility: 제공하지 않음; 구 Worker는 v2 `payload`/`metadata`를 보존하지 못함

### HTTP status

2026-06 performance output의 `Event status 200`은 route decorator에 `202 Accepted`를 명시하기 전 수집한 역사적 증거입니다. 요청 성공률과 latency 원본은 보존하되 현재 HTTP 계약의 증거로 사용하지 않습니다.

현재 build 검증 조건:

- event intake endpoints 응답 `202`
- OpenAPI success response `202`
- Kafka append 실패 시 `503`
- 최신 2026-07-21 performance 원본의 event status `202`: `29,608`, 다른 event status `0`
- 첫 v2 비교 원본의 event status `202`: `25,378`, 다른 event status `0`

### Row-visible latency proxy

기존 PowerShell suite의 `accepted-to-persisted` 값은 실제 DB commit timestamp 측정값이 아닙니다.

- 시작점: API request accepted 시각
- 종료점: PostgreSQL row의 `created_at` 또는 polling에서 row가 보인 시점
- 의미: API 수락부터 DB row 생성/가시성까지의 근사치
- 제한: transaction commit 완료 순간, clock skew, polling 간격을 직접 분리하지 못함

따라서 이 문서에서는 과거 수치를 `row-visible latency proxy`로 표기합니다. 현재 source의 commit-observed 계측과 같은 수치로 연결하지 않습니다.

현재 source의 측정 정의는 과거 원본과 다릅니다.

- Worker `messaging_event_persist_lag_seconds`: API payload `queued_at`부터 PostgreSQL `commit()` 반환 직후 기록한 `persisted_at`까지
- current PowerShell `accepted_to_status_observed_ms`: API `queued_at`부터 client가 `persisted` status를 관측할 때까지
- PowerShell 측정 포함 범위: 200ms status polling interval, API/network 응답 지연
- 2026-07-21 status-observed sample: 50/50 persisted, avg `79.96ms`, p95 `81.28ms`, max `2384.10ms`
- 2026-07-21 Worker histogram query: `60s`; histogram의 최대 finite bucket 경계와 같아 exact p95 수치로 해석 제외

### DLQ sample

`GET /v1/dlq/ingress`와 `/summary`는 append-only Kafka DLQ log의 조회 범위에서 최근 event를 표본화합니다.

- `count`, `by_reason`, `replayable`, `blocked`: 조회 표본 통계
- `oldest_sample_age_seconds`: 표본 안에서 가장 오래된 log event의 age
- unresolved depth / current incident backlog: 제공하지 않음
- oldest unresolved SLO: 제공하지 않음

## Multi-stream Fixed Worker / KEDA A/B Candidate — 2026-07-21

동일한 dirty `dev-kafka` source image `perf-v17`, API `6`, clean DB/topic, fresh lag `0`, 100 VU / 30s, 64 streams 조건으로 Worker fixed `2`와 KEDA `2→8`을 각각 1회 실행했습니다. 각 arm은 DB event state와 6개 Kafka topic을 재생성하고 Kafka deletion quiet period `75s` 뒤 시작했습니다.

| Worker mode | Event `202` | Error | Avg | p95 | p99 | Peak message lag | Peak notification lag | All-pipeline drain |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed `2` | `22,125` | `0.00%` | `82.65ms` | `169.24ms` | `224.76ms` | `21,170` | `45` | `301.42s` |
| KEDA `2→8` | `20,499` | `0.00%` | `93.14ms` | `212.60ms` | `297.62ms` | `18,950` | `11,536` | `261.17s` |

판정:

- KEDA arm의 all-pipeline drain `13.35%` 감소와 final Worker `8` 확인
- main Worker 처리 가속 중 notification-worker backlog 최대 `11,536`으로 이동
- KEDA arm의 event 수 `7.35%` 감소, avg/p95/p99 `12.69%`/`25.62%`/`32.42%` 증가
- single-node kind에서 Worker scale-out과 API가 CPU·DB·network 자원을 공유한 결과로 해석
- fixed/KEDA 각 1회, accepted event 수 차이, dirty local image 포함; stable baseline과 인과관계 확정에서 제외
- 원본: [fixed Worker](../results/kafka-performance/worker-ab-fixed.txt), [KEDA](../results/kafka-performance/worker-ab-keda.txt)

## Generic v2 Performance Recovery Candidate — 2026-07-21

첫 v2 후보의 저하를 조사한 뒤 dirty local worktree image `perf-v16`으로 같은 클린 DB/topic 조건을 3회 반복했습니다. 각 실행은 API/Worker image 일치, API 6/6, Worker 2/2, 모든 API cache hydration 완료, 시작 consumer lag `0`, Kafka 지연 삭제 I/O 대기 75초를 확인한 뒤 부하를 시작했습니다.

| Run | Event `202` | Error | Avg | p95 | p99 | Peak Worker lag | Main drain |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `29,146` | `0.00%` | `51.88ms` | `105.34ms` | `139.47ms` | `28,064` | `501.89s` |
| 2 | `28,749` | `0.00%` | `53.33ms` | `107.96ms` | `147.51ms` | `27,635` | `511.96s` |
| 3 | `29,608` | `0.00%` | `50.21ms` | `90.51ms` | `134.78ms` | `28,488` | `511.88s` |
| 평균 | `29,168` | `0.00%` | `51.81ms` | `101.27ms` | `140.59ms` | `28,062` | `508.58s` |

변경과 원인:

- state/status/snapshot/notification producer `linger_ms=0`: Worker commit 뒤 직렬 발행 대기 축소
- materialized cache consumer frame `64MiB`, aggregate fetch `50MiB`: compacted topic replay의 기본 1MiB frame 초과 reconnect loop 제거
- cache poll `200→1000`, 중복 구조 검증 제거: API startup full replay CPU 감소
- API HPA min `3→6`, scale-up stabilization `60s`, 최대 `2 pods/60s`: 부하 중 새 pod의 full replay가 겹치는 시작 부하 억제
- clean benchmark reset: DB event state와 6개 Kafka topic 초기화, topic 재생성 뒤 75초 quiet period
- steady-state gate: API/Worker 최소 replica, CPU target 이내, 모든 API cache hydration, fresh lag `0` 연속 확인

판정:

- 첫 v2 후보 대비 3회 평균 event `14.93%` 증가
- 첫 v2 후보 대비 avg `23.62%`, p95 `18.30%`, p99 `8.17%` 감소
- main drain 처리율 약 `32.6→55.2 events/s`, `69.3%` 증가
- 세 실행의 worst run도 첫 v2 후보보다 event, avg, p95, p99 개선
- historical stable legacy baseline 대비 평균 event `7.92%` 감소, avg/p95/p99 `17.39%`/`25.57%`/`35.74%` 증가
- API floor `3→6` 변경 포함, 코드 효율만의 개선값으로 귀속 제외
- dirty worktree/local-only image이므로 registry/GitOps 배포 증거 제외
- stable generic v2 baseline 승격 제외; multi-stream 반복과 registry image 재검증 대기

최신 원본 [results/kafka-performance/latest.txt](../results/kafka-performance/latest.txt)은 3회차 전체 출력입니다. 1·2회차 수치는 같은 터미널 세션에서 관측한 완료 출력이며 별도 전체 raw 파일로 보존하지 않았습니다.

## First Generic v2 Performance Candidate — 2026-07-21

Generic v2 envelope와 HTTP `202 Accepted` 계약을 실제 local `dev-kafka` cluster에 배포한 뒤 실행한 첫 성능 후보입니다.

| Item | Result |
| --- | ---: |
| Timestamp | `2026-07-21T03:37:34+09:00` |
| Source / image | `dev-kafka` `1439be1` / API·Worker `d31ac14` |
| Workload | 100 VU / 30s, `single500`, 한 hot stream |
| Total HTTP requests | `25,382` |
| Event `202` responses | `25,378` |
| Other event status | `0` |
| Error rate | `0.00%` |
| Average | `67.83ms` |
| p95 / p99 | `123.96ms` / `153.10ms` |
| Same-stream ordering | 100 events, `stream_seq 1..100`, pass in `7.93s` |
| Persistence status sample | `50/50` persisted |
| Status-observed avg / p95 / max | `79.96ms` / `81.28ms` / `2384.10ms` |
| Peak message-worker lag | `24,504` |
| Main all-consumer drain | `751.76s`, final message/notification lag `0` |
| Peak notification-worker lag during drain | `6` |
| HPA probe follow-up | 추가 message-worker lag `4,962`, 수동 관측 약 `160s` 내 `0` |

측정 경계:

- `25,382` total HTTP requests: k6 setup 요청 4개 포함; 실제 event `202`는 `25,378`
- `single500`: 모든 event가 같은 stream key를 사용해 Kafka 한 partition으로 집중되는 hot-stream 조건
- cluster state: Namespace prune 사고 뒤 같은 kind cluster에 PostgreSQL/Pgpool을 clean reinstall한 새 DB; 삭제된 local demo row와 in-cluster backup PVC는 복구 불가
- status-observed latency: client polling과 network delay 포함; 2026-06 row-visible proxy와 직접 비교 제외
- Worker histogram query `60s`: `messaging_event_persist_lag_seconds`의 최대 finite bucket이 `60s`이므로 상단 bucket 포화 신호로만 사용
- HPA sanity probe: main drain 뒤 별도 부하 생성; raw suite 종료 뒤 추가 lag `4,962`를 약 `160s` 동안 수동 관측해 `0` 확인

비교:

| Reference | Requests | Average | p95 | p99 |
| --- | ---: | ---: | ---: | ---: |
| Stable legacy baseline 대비 | `-19.87%` | `+53.70%` | `+53.70%` | `+47.82%` |
| 2026-06-18 last legacy raw 대비 | `-8.68%` | `+17.68%` | `+3.92%` | `+1.66%` |

판정:

- generic v2의 현재 후보 성능과 `202` 계약 확인
- stable legacy baseline보다 intake request 수 감소와 latency 증가 확인
- 2026-06-18 마지막 legacy raw suite와는 tail latency가 근접하지만 request 수와 average latency 악화
- fresh cluster 재설치, 현재 resource 상태, generic envelope 변경이 함께 포함된 단일 실행이므로 원인을 v2에만 귀속하지 않음
- main drain `751.76s`는 2026-06-18 약 16분보다 짧지만 accepted load와 peak lag가 달라 직접적인 처리 성능 개선 주장 제외
- stable baseline 승격 제외; 같은 조건 반복 실행과 multi-stream/partition 분산 실험 필요

## Stable Kafka Intake Baseline

현재 대표 기준선은 2차 Kafka baseline입니다.

| Item | Result |
| --- | ---: |
| Workload | 100 VU / 30s |
| Total HTTP requests | `31,676` |
| Event success responses | `31,672` historical status `200` |
| Error rate | `0.00%` |
| Average | `44.13ms` |
| p95 | `80.65ms` |
| p99 | `103.57ms` |
| Same-stream ordering | 100 events, `stream_seq 1..100`, pass |
| Row-visible proxy avg / p95 / max | `7.29ms` / `7.67ms` / `8.14ms` |
| Worker KEDA end snapshot | `4` replicas |

이 결과의 역할:

- Kafka append-first API intake 기준선
- Pgpool HA, pool tuning, Worker inline retry, ordering 보강 뒤의 회귀 확인
- Worker fixed replica 대 KEDA 효과 비교에서 제외

## Last Legacy Raw Suite — 2026-06-18

Notification 처리 경계를 별도 `message-notifications` topic과 `notification-worker`로 분리한 뒤 실행한 suite입니다.

| Item | Result |
| --- | ---: |
| Timestamp | `2026-06-18T03:29:47+09:00` |
| Workload | 100 VU / 30s |
| Total HTTP requests | `27,795` |
| Event success responses | `27,791` historical status `200` |
| Error rate | `0.00%` |
| Average | `57.64ms` |
| p95 / p99 | `119.28ms` / `150.60ms` |
| Ordering | stream `38`, 100 events, pass |
| Async sample | stream `39`, 50 events |
| Row-visible proxy p95 / max | `22.13ms` / `2228.67ms` |
| message-worker lag drain | 약 16분 뒤 `0` |
| notification-worker lag | `0` |

판정:

- 알림 기록 실패가 core message persistence transaction을 rollback시키지 않는 장애 범위 분리
- stable intake baseline보다 request count 감소와 p95/p99 악화
- row-visible proxy p95와 drain time도 직전 suite보다 악화
- 성능 개선 결과로 채택하지 않음
- DB commit 이후 notification publish는 best-effort이며 transactional outbox 보장 없음

## Historical Legacy Kafka Performance Sequence

| Suite | Requests | Error | Avg | p95 | p99 | Row-visible proxy p95 | Worker signal | Interpretation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 1st baseline | `31,710` | `0.00%` | `44.04ms` | `86.95ms` | `113.78ms` | `8.04ms` | KEDA end `8` | 최초 Kafka intake 기준 |
| 2nd baseline | `31,676` | `0.00%` | `44.13ms` | `80.65ms` | `103.57ms` | `7.67ms` | KEDA end `4` | 현재 stable baseline |
| 2026-06-09 capacity rerun | `34,284` | `0.00%` | `36.86ms` | `66.06ms` | `104.99ms` | `73.50ms` | lag `36394`, drain 약 14분 | intake와 persistence capacity 분리 신호 |
| transaction tuning | `28,839` | `0.00%` | `53.47ms` | `108.68ms` | `134.53ms` | `8.08ms` | lag `29204`, drain 약 10분 | persistence path 개선, intake 악화 |
| notification split | `27,795` | `0.00%` | `57.64ms` | `119.28ms` | `150.60ms` | `22.13ms` | drain 약 16분 | 장애 경계 개선, 성능 개선 아님 |

이 표의 모든 suite는 legacy/order request shape와 historical event success status `200`을 사용했습니다. 2026-07-21 generic v2 후보는 계약과 latency 정의가 달라 위의 별도 섹션에 유지합니다.

### 2026-06-09 capacity rerun 상세

- same-stream ordering: stream `30`, 100 events, `1..100`, pass
- async persistence sample: stream `31`, 50 events
- Worker lag: `36394 -> 33274 -> 23563 -> 11971 -> 0`
- KEDA: max `8`
- drain: 약 14분

이 실행은 짧은 API intake burst가 Worker의 지속 가능 DB write throughput보다 빠를 수 있음을 보여줍니다. `34,284` request 수를 KEDA의 처리량 개선으로 해석하지 않습니다.

### Transaction 통합 상세

Worker success path에서 message persistence와 request status update를 하나의 PostgreSQL transaction으로 묶은 뒤 실행했습니다.

- same-stream ordering: stream `34`, 100 events, pass
- async persistence sample: stream `35`
- row-visible proxy p95: `8.08ms`
- peak message-worker lag: `29204`
- drain: 약 10분

직전 rerun과 workload 상태가 완전히 같은 A/B 실험은 아닙니다. 결과는 개선 신호이며 fixed-vs-KEDA 또는 인과관계 증명으로 사용하지 않습니다.

## Ordering / Failure Injection 검증 — 2026-06-08

검증은 Kafka accept 수만 보지 않고 API pod 내부에서 PostgreSQL `messages` row를 조회해 최종 persistence를 판정했습니다.

PostgreSQL row evidence를 pass/fail 기준으로 사용했습니다.

| Scenario | Expected | Accepted | Persisted | Missing | Duplicate | Mixed payload | DLQ | Ordering | Total | DB outage | Recovery to completion |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| `single_no_failure` | 100 | 100 | 100 | 0 | 0 | 0 | 0 | PASS | `6.125s` | — | — |
| `multi_no_failure` | 300 | 300 | 300 | 0 | 0 | 0 | 0 | PASS | `8.438s` | — | — |
| `single_db_failure` | 100 | 100 | 100 | 0 | 0 | 0 | 0 | PASS | `22.969s` | `20.828s` | `1.375s` |
| `multi_db_failure` | 300 | 300 | 300 | 0 | 0 | 0 | 0 | PASS | `23.703s` | `21.610s` | `1.218s` |

최종 판정: missing `0`, duplicate `0`, mixed payload `0`, DLQ `0`. 각 scenario의 accepted 수와 PostgreSQL persisted row 수가 일치했습니다.

Payload 규칙:

- stream A: `A001..A100`
- stream B: `B001..B100`
- stream C: `C001..C100`
- expected sequence: 각 stream `1..100`

Windows client skew 수정:

- 이전 `http://localhost` + Python `urllib` 조합에서 request당 약 2초 client-side 지연 발생
- 현재 script default: `http://127.0.0.1`, `Host: localhost`
- 과거 약 `210s` duration: performance evidence에서 제외

### 검증 한계

- Pgpool Deployment scale-down 기반 짧은 DB outage
- kind single-node 환경
- PostgreSQL StatefulSet 전체 outage/recovery와 primary promotion/failover는 다른 시나리오이며, 기존 결과는 promotion 성공 증거에서 제외
- 2026-07-21 전체 StatefulSet 재기동에서 chart의 first-boot sync 설정이 persisted volume restart에 재적용되지 않는 결함 발견; 모든 pod에 지속 설정하는 recovery helper 적용 뒤 `3/3 ready`, current primary sync/quorum standby `2`, readiness 복귀 재검증
- Worker process가 poll batch 중간에 종료되는 crash/offset boundary 실험 미포함
- long outage 뒤 DLQ/replay terminal path 별도 검증 필요

## Cache Read Fallback

Materialized cache 검증의 원본은 Kafka ingress event가 아닙니다. Worker가 PostgreSQL commit 뒤 발행한 DB snapshot을 사용합니다.

| Scenario | Expected result |
| --- | --- |
| DB available, initial hydration complete, fresh snapshot | DB membership authorization과 latest sequence watermark 연속성 확인 뒤 `source=cache`, `degraded=false` |
| DB unavailable, hydrated membership/message snapshot available | `source=cache`, `degraded=true`, `snapshot_age_seconds` 포함 |
| DB unavailable, cache miss | service unavailable response |

검증 흐름:

1. event를 Kafka에 append
2. Worker PostgreSQL commit 확인
3. Worker snapshot publish
4. 각 API pod가 세 compacted topic의 모든 partition을 beginning부터 독립 replay
5. startup 시점에 잡은 initial end offset까지 도달한 뒤 fresh cache read 성공으로 hydration gate 동작을 간접 확인
6. DB 정상 시 membership authorization과 latest sequence watermark 연속성 확인 뒤 fresh cache read 확인
7. DB 장애 시 hydrated membership/message snapshot fallback과 cache miss 비교

이 consumer에는 consumer group이 없습니다. 따라서 이 검증을 `snapshot consumer group lag` 증거로 해석하지 않습니다. pod별 current position, captured end offset, remaining records, hydration duration은 아직 미구현 custom metric입니다.

## Readiness Contract

`GET /health/ready`가 직접 결정하는 상태:

| Condition | HTTP / state |
| --- | --- |
| schema migration 완료, Kafka reachable, PostgreSQL primary/HA guardrail 충족, non-local secret 안전 | `200 ready` |
| Kafka reachable, schema/secret hard failure 없음, PostgreSQL primary 또는 standby/replication guardrail 이탈 | `200 degraded` |
| schema 미준비, Kafka unreachable, non-local unsafe auth secret | `503 not_ready` |

응답의 Worker와 materialized cache 정보는 운영 문맥이며 readiness state 결정 조건이 아닙니다. 아래 신호는 Prometheus, alerts, `check_portfolio_status.ps1`에서 별도로 확인합니다.

응답에는 실행 중인 API build를 식별하는 `app_version`이 포함됩니다. 현재 `master` source 기준 값은 `2.0.0`이며 Demo UI `2.0.0` badge와 함께 확인합니다. Public demo-lite UI `1.4.1`은 별도 branch/image 상태입니다.

- Kafka broker count
- PostgreSQL standby count와 replication delay는 API readiness의 degraded reason에도 반영
- Pgpool replica readiness
- Worker / notification-worker replica와 consumer lag
- backup job / PVC 상태

## Kafka and Local HA Topology Evidence

마지막으로 문서화된 full local profile:

- Kafka KRaft brokers: `3`
- Kafka topic partitions: `8`
- replication factor: `3`
- `min.insync.replicas`: `2`
- compacted topics: `message-request-status`, `message-snapshots`, `stream-snapshots`
- PostgreSQL pods: `3`
- Pgpool replicas: `2`, PDB `minAvailable=1`
- API HPA: min `6`, max `8`, CPU target `65%`, scale-up stabilization `60s`, scale-down stabilization `120s`
- Worker KEDA: min `2`, max `8`, lag threshold `100`
- DLQ replayer: `1`
- notification-worker: separate consumer group

이 구성은 single-node kind 위의 local demonstration입니다. node-level HA 증거로 사용하지 않습니다. Argo CD `Synced / Healthy`도 마지막 기록 시점의 historical snapshot이며 현재 상태는 재조회해야 합니다.

## DLQ Contract Validation

`GET /v1/dlq/ingress/summary` schema 확인 항목:

- `topic`
- sampled `count`
- `by_reason`
- `replayable`
- `blocked`
- `oldest_sample_age_seconds`
- `recent_samples`

이 endpoint는 global unresolved queue view가 아닙니다. incident 판단 시 Worker failure metrics, replay metrics, consumer state, 원본 event를 함께 확인합니다.

## Redis Historical Context

다음 수치는 Kafka 구조 이전 Redis queue-first 실험입니다.

| Stage | Requests | Average | p95 |
| --- | ---: | ---: | ---: |
| Initial | `5,434` | `3,660ms` | `8,175ms` |
| Pgpool / DB pool tuning | `11,314` | `1,519ms` | `3,333ms` |
| KEDA queue-depth phase | `19,528` | `811ms` | `1,954ms` |

용도:

- Redis queue depth scaling과 hot-path tuning의 역사 설명
- Kafka append-first baseline, Worker Kafka KEDA 효과와 분리

## Reproduction Commands

Unit / contract suite:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Kafka performance suite:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_kafka_performance_suite.ps1
```

Ordering / failure injection:

```powershell
.venv\Scripts\python.exe scripts\ordering_failure_injection.py --scenario all --event-count 100
```

Cache fallback:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test_cache_read_fallback.ps1 -SkipReset
```

Local portfolio status without Argo CD bootstrap:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_portfolio_status.ps1 -SkipArgoCd
```

GitOps profile에서는 Argo CD 설치와 application bootstrap을 확인한 뒤 `-SkipArgoCd`를 제거합니다.

## Measurement Validity

2026-06-08 ordering / failure injection 실험에서 Windows / Docker Desktop의 Python `urllib`가 `http://localhost`를 호출할 때 request당 약 2초 client-side 연결 지연이 발생했습니다.

| Measurement | Validity | Reason |
| --- | --- | --- |
| Kafka intake k6 baseline | valid | cluster 내부 `http://api.messaging-app.svc.cluster.local:8000` 호출 |
| PowerShell functional POST | valid for functional timing | `Invoke-RestMethod` 기준 약 `10-15ms` 관측 |
| 2026-06 PowerShell accepted-to-persisted | valid as historical proxy | PostgreSQL row `created_at` / row-visible 시점 기반, commit timestamp 제외 |
| current PowerShell `accepted_to_status_observed_ms` | source updated, rerun pending | client의 persisted status 관측, polling/network 포함 |
| Worker accepted-to-commit-observed histogram | source updated, rerun pending | `commit()` 반환 직후 `persisted_at`; post-commit publish 제외 |
| old ordering / failure injection `~210s` duration | invalid | Python `urllib` + `http://localhost` client-side delay |
| latest ordering / failure injection duration | valid | `http://127.0.0.1` connection + `Host: localhost` |

## 측정 / 재현 환경

| 항목 | 값 |
| --- | --- |
| Host CPU | AMD Ryzen 5 5600, 6 cores / 12 threads, max 3.5GHz |
| Host memory | 약 32GiB |
| Docker Desktop 할당 | 12 CPU, 약 15.6GiB memory |
| Kubernetes cluster | kind single-node |
| Kubernetes node | `messaging-ha-control-plane` |
| Kubernetes version | `v1.32.2` |
| Node OS / kernel | Debian 12, WSL2 kernel `6.6.87.2-microsoft-standard-WSL2` |
| Container runtime | `containerd://2.0.2` |
| Kubernetes allocatable | 12 CPU, `16338128Ki` memory |
| Historical pod requests / limits | 5.1 CPU / `6768Mi`, 13.725 CPU / `14782Mi` |

재현 기준:

- 100 VU / 30s baseline: 12 threads / 16GiB 이상 권장
- 권장 사양보다 낮은 환경: full HA stack과 성능 baseline 재현 어려움
- timeout / latency / restart: 기능 오류 확정 전 리소스 부족 가능성 확인

| 구간 | 흔한 실패 형태 | 해석 |
| --- | --- | --- |
| install / rollout | timeout, `CrashLoopBackOff`, `OOMKilled` | CPU/RAM/scheduling 압력 |
| readiness | timeout, `degraded`, `not_ready` | schema/Kafka/PostgreSQL/HA guardrail 확인 |
| Kafka intake | `503`, produce timeout | broker/ack 지연 또는 unavailable |
| Worker | persisted timeout, lag 증가 | Worker/DB throughput 부족 |
| DLQ | `Poison event did not reach Kafka DLQ in time` | Worker가 제한 시간 안에 terminal 처리 미완료 |
| k6 | error/p95/p99 threshold failure | capacity 또는 resource contention |

## Historical Detailed Evidence

아래 기록은 당시 image와 cluster 상태에서 수집한 실험 결과입니다. 모든 event success status `200`은 HTTP `202` contract 명시 전 historical evidence이며, `Accepted-to-persisted` 표기값은 PowerShell row-visible proxy입니다.

### 1차 실험: Kafka 이벤트 스트림 기준선

목적:

- ingress topic 중심 intake
- Worker consumer group → PostgreSQL HA persistence
- `stream_id` ordering boundary
- DLQ, readiness, autoscaling, performance 기준

조건:

- profile `single500`
- 100 VU / 30s
- idempotency header 비활성
- ordering 100 events
- latency sample 50 events

| Metric | Result |
| --- | ---: |
| Ordering | `stream_seq 1..100` |
| Total HTTP | `31710` |
| Event status 200 | `31706` |
| Event status 503 | `0` |
| Error | `0.00%` |
| Avg / p95 / p99 | `44.04ms` / `86.95ms` / `113.78ms` |
| Async accept avg / p95 / max | `55.68ms` / `65.83ms` / `86.55ms` |
| Row-visible proxy avg / p95 / max | `7.51ms` / `8.04ms` / `10.92ms` |
| API / Worker end snapshot | `8` / `8` |

당시 확인한 한계:

- Pgpool `1 replica` 단일 경계
- API idempotency state-store hot path로 인한 Pgpool pressure
- Kafka tail retry가 same-stream 추월 가능성을 만들던 초기 failure handling

### 2차 실험: Pgpool HA와 엄격한 stream 순서 보장

보강:

- Pgpool replicas `1 → 2`, PDB `minAvailable=1`
- PostgreSQL PDB `minAvailable=2`
- local memory 안정성을 위한 Pgpool pool 축소
- Worker tail retry → same-offset inline retry
- k6 p99 출력

| Metric | Result |
| --- | ---: |
| Ordering | `stream_seq 1..100`, body order match |
| Pgpool / PostgreSQL | `2/2`, `3/3`, standby `2` |
| Total HTTP | `31676` |
| Event status 200 / 503 | `31672` / `0` |
| Error | `0.00%` |
| Avg / p95 / p99 | `44.13ms` / `80.65ms` / `103.57ms` |
| Async accept avg / p95 / max | `53.34ms` / `63.59ms` / `75.22ms` |
| Row-visible proxy avg / p95 / max | `7.29ms` / `7.67ms` / `8.14ms` |
| API / Worker end snapshot | `6` / `4` |

이 결과를 stable intake baseline으로 유지합니다. Inline retry는 ordering을 지키는 동안 뒤 record에 backpressure를 전달합니다.

### 2026-06-09 재실행: 정합성 재확인과 backlog drain 관측

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_kafka_performance_suite.ps1
```

| Metric | 2nd baseline | 2026-06-09 |
| --- | ---: | ---: |
| Total HTTP | `31676` | `34284` |
| Event status 200 | `31672` | `34280` |
| Error | `0.00%` | `0.00%` |
| Avg / p95 / p99 | `44.13` / `80.65` / `103.57ms` | `36.86` / `66.06` / `104.99ms` |
| Row-visible proxy p95 | `7.67ms` | `73.50ms` |
| Peak Worker lag | 미기록 | `36394` |
| KEDA max | end `4` | `8` |
| Drain | `0` snapshot | 약 14분 |

#### 2026-06-09 Kafka 정합성 검증

| Check | Method | Result |
| --- | --- | --- |
| Same-stream ordering | `stream_id` `30`, `ordering-event-0001..0100` | first `ordering-event-0001`, last `ordering-event-0100`, ordering `pass` |
| Async completion | `stream_id` `31`, 50 events status polling | 모두 persisted |
| Row-visible proxy | accept와 row-visible 분리 측정 | p95 `73.50ms` |
| Drain | kafka-exporter 반복 조회 | `36394 -> 33274 -> 23563 -> 11971 -> 0` |

PostgreSQL 장애 주입이 없는 capacity/정합성 rerun입니다. Worker persistence capacity 신호로 기록하며 stable baseline을 대체하지 않습니다.

### Worker success path transaction 통합

- `persist_ingress_job()`에서 message persistence와 request status update를 한 DB transaction으로 처리
- commit 뒤 request status와 snapshot publish
- notification job은 별도 topic/worker로 전달
- current limitation: post-commit publish transactional outbox 없음

| Metric | Before | After |
| --- | ---: | ---: |
| Total HTTP | `34284` | `28839` |
| Avg / p95 / p99 | `36.86` / `66.06` / `104.99ms` | `53.47` / `108.68` / `134.53ms` |
| Row-visible proxy p95 | `73.50ms` | `8.08ms` |
| Peak lag | `36394` | `29204` |
| Drain path | `36394 -> 33274 -> 23563 -> 11971 -> 0` | `29204 -> 23597 -> 15111 -> 6893 -> 0` |
| Drain | 약 14분 | 약 10분 |

Persistence path improvement signal입니다. 전체 intake 기준선 대체 수치로는 사용하지 않습니다.

### Notification path 분리 후 재실행

- `message-notifications`: partitions `8`, RF `3`, min ISR `2`
- separate `notification-worker` Deployment/Service/Prometheus scrape
- core message transaction에서 notification attempt insert 제거

| Metric | Transaction tuning | Notification split |
| --- | ---: | ---: |
| Total HTTP | `28839` | `27795` |
| Event status 200 | `28835` | `27791` |
| Avg / p95 / p99 | `53.47` / `108.68` / `134.53ms` | `57.64` / `119.28` / `150.60ms` |
| Ordering | stream `34`, pass | stream `38`, pass |
| Sample | stream `35`, 50 | stream `39`, 50 |
| Row-visible proxy p95 | `8.08ms` | `22.13ms` |
| message-worker drain | 약 10분 | 약 16분 |
| notification-worker lag | N/A | `0` |

장애 범위 분리 결과이며 성능 개선 수치에서 제외합니다.

## DB Snapshot Cache / Degraded Read 검증 절차

Cache 원본은 Kafka ingress event가 아닙니다. Worker가 PostgreSQL commit 뒤 발행한 snapshot만 사용합니다.

검증 script: `scripts/test_cache_read_fallback.ps1`

1. stream을 생성
2. event request 전송
3. API Kafka append와 `202 Accepted` 확인
4. Worker가 PostgreSQL commit
5. Worker가 `message-snapshots` publish
6. stream 생성 commit 뒤 `stream-snapshots` membership snapshot publish
7. 각 API pod가 `message-request-status`, `message-snapshots`, `stream-snapshots`의 모든 partition을 beginning부터 독립 replay
8. startup 시점에 잡은 initial end offset까지 도달한 뒤 fresh cache read 성공으로 hydration gate 동작을 간접 확인
9. PostgreSQL 정상 상태에서 DB membership authorization과 latest sequence watermark 연속성 확인 뒤 fresh `GET /streams/{stream_id}/events`: `source=cache`, `degraded=false`, `snapshot_age_seconds`
10. PostgreSQL read path 중단
11. hydrated membership/message snapshot이 있으면 stale read: `source=cache`, `degraded=true`; hydration 미완료 또는 cache/membership miss: `503`

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test_cache_read_fallback.ps1 -SkipReset
```

| Check | Expected / historical result |
| --- | --- |
| Cache source | DB commit 이후 `message-snapshots` / `stream-snapshots` |
| Initial hydration gate | captured initial end offsets 도달 전 cache read 제외 |
| Fresh read | DB membership authorization과 latest sequence watermark 연속성 확인 뒤 `source=cache`, `degraded=false` |
| DB failure + stale snapshot | hydrated membership/message snapshot이 모두 있을 때 `source=cache`, `degraded=true`, `snapshot_age_seconds` |
| DB failure + cache miss | `503 Stream read unavailable` |
| Membership guard | membership snapshot 없으면 cached message만으로 read 허용 제외 |
| 2026-04-30 fresh sample | `snapshot_age_seconds=0.507` |
| 2026-04-30 stale sample | `snapshot_age_seconds=11.798` |

이 실행은 cache read/fallback 기능 검증입니다. consumer group lag 또는 pod별 replay 진행률을 측정한 결과가 아닙니다.

2026-07-21 tracked rerun은 `45.390s`, exit `0`으로 완료했습니다. Fresh read는 `source=cache`, `degraded=false`, age `0.112s`, DB-down read는 `source=cache`, `degraded=true`, age `11.462s`였고 scale `3→0→3` 뒤 PostgreSQL `3/3`, sync/quorum standby `2`, readiness `ready`로 복귀했습니다. 별도 DB outage suite도 `43.008s`, exit `0`으로 accepted-during-outage와 recovery persistence를 재확인했습니다. 이후 image `9349ba9` 배포에서 readiness materialized cache `ready=true`, `hydrated=true`와 API contract pass를 직접 확인했습니다. 실행 시각·source/script hash·관측값을 담은 tracked structured summary는 [results/postgres-recovery/latest.json](../results/postgres-recovery/latest.json)에 보관합니다. 전체 raw terminal transcript는 보관하지 않았습니다.

## 운영 메트릭 변화 확인 — 2026-04-29

| Measurement | Historical result |
| --- | ---: |
| Prometheus `api:8000` / `worker:9101` / `dlq-replayer:9102` / kube-state-metrics | `up=1` |
| API request counter | `3974 -> 4008` |
| Ordering | 20 events, `stream_seq 1..20` |
| DLQ metric | poison/gap event 뒤 증가 |
| Pod restart increase | `0` |
| Unavailable replicas | `0` |

## 운영 Alert Probe 결과 — 2026-04-29

`scripts/test_operational_alerts.ps1`로 Prometheus rule load와 상태 변화를 확인했습니다.

| Scenario | Alert | Historical state |
| --- | --- | --- |
| DLQ publish | `MessagingDlqEventsIncreasing` | firing |
| replay guard | `MessagingDlqReplayBlocked` | firing |
| bad image rollout | `MessagingDeploymentUnavailableReplicas` | pending까지 확인 |

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test_operational_alerts.ps1 -SkipReset
```

현재 DLQ sample age는 alert SLO가 아닙니다. unresolved depth/age 상태 모델이 추가될 때 별도 alert를 정의합니다.

## DLQ Summary API 계약

`GET /v1/dlq/ingress/summary` contract:

| Field | Validation |
| --- | --- |
| `queue_backend` | `kafka` |
| `topic` | DLQ topic |
| `count` | sampled count |
| `replayable` / `blocked` | sampled replayability; blocked includes malformed/identity·counter violation and max replay |
| `oldest_sample_age_seconds` | oldest record age in sampled log window |
| `by_reason` / `by_stream` | sampled grouping |
| `recent_samples` | recent log sample |

이 계약은 unresolved backlog를 제공하지 않습니다.

## API Contract Test

API contract test는 route status, response model, OpenAPI를 같은 build에서 확인합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test_api_contracts.ps1 -SkipReset
```

확인 범위:

- auth/login and protected routes
- event intake HTTP `202`
- request status response
- stream/message response model
- readiness materialized cache `ready` / `hydrated` 실제 값
- DLQ list/summary fields
- `/openapi.json` response schema

Response models:

- `ReadinessResponse`
- `EventRequestStatusResponse`
- `DlqListResponse`
- `DlqSummaryResponse`

## Response Model / Incident Signal 계약

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test_incident_signals.ps1 -SkipDbOutage
```

이 wrapper는 DB outage signal, DLQ alert probe, Worker bad rollout 관찰을 묶습니다. destructive probe이므로 disposable local cluster에서 실행합니다.

## Kafka Exporter 관측성

| Item | Historical validation |
| --- | --- |
| scrape | `up{job="kafka-exporter"}=1` |
| brokers | `kafka_brokers=3` |
| message-worker | `sum(clamp_min(kafka_consumergroup_lag{consumergroup="message-worker"}, 0))=0` |
| notification-worker | lag `0` |
| panels | broker count, consumer lag, partition offsets |
| alerts | `MessagingKafkaExporterDown`, `MessagingKafkaConsumerLagHigh` |

## GitOps / Argo CD Historical Check

2026-04-29 local kind 기록:

- Application `messaging-portfolio-local-ha`
- source revision `master`, path `k8s/gitops/overlays/local-ha`
- Argo CD `Synced / Healthy`
- HPA/KEDA replica drift ignore
- `postgres-backups` `WaitForFirstConsumer` health customization

현재 master registry pipeline은 GHCR SHA image와 overlay tag bot commit을 사용합니다. 2026-07-21 merge `8f5d78c`의 [GitHub Actions run #55](https://github.com/Jangwanko/Cloud_portfolio/actions/runs/29776081853)에서 validate와 `publish-master-image` job이 모두 성공했고, registry candidate digest의 UID `10001` 검증 뒤 image `8f5d78c6963a` 승격과 bot commit `717e0ca`까지 완료됐습니다. Local Argo CD는 `dev-kafka`를 추적하므로 이 결과는 master artifact publication 증거이며 master-targeted runtime rollout 증거는 아닙니다. 위 2026-04-29 기록 역시 현재 배포 상태를 보장하지 않습니다.

## Portfolio Status Check

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_portfolio_status.ps1
```

2026-07-21 local live snapshot:

- API readiness `ready`
- Argo CD `Synced / Healthy`, deployment-bearing image-tag revision `b84c379`; 이후 docs-only revision은 workload 변경 없음
- API/core workload ready, API/Worker image `9349ba9`
- readiness materialized cache `ready=true`, `hydrated=true`; API contract suite pass
- Kafka `3/3`, PostgreSQL `3/3`, Pgpool `2/2`
- `worker-keda` Ready
- Prometheus scrape targets `up=1`
- `kafka_brokers=3`
- normalized `message-worker consumer_lag=0`
- normalized `notification-worker consumer_lag=0`
- `postgres-backups` PVC `Bound`, manual backup Job `Completed`
- host dump `39,433,414` bytes의 disposable DB restore 통과: 10개 table count, Alembic `0008_generic_event_envelope`, generic v2 row `33,840`, message max id `33,840`, max sequence `25,378` 원본 일치; 임시 DB 삭제

## Known Gaps and Next Acceptance Criteria

- 64-stream fixed/KEDA A/B 3회 반복과 notification-worker capacity 분리
- Worker histogram 상단 bucket 확장 뒤 commit-observed p95 재측정
- registry image 기준 hot-stream과 multi-stream 재검증
- poll batch 중간 crash와 partition offset recovery
- transactional outbox 또는 동등한 post-commit publish recovery
- unresolved DLQ 상태 모델
- object storage/cluster-loss backup recovery와 multi-node disruption drill

우선순위, 이유, 측정 가능한 완료 조건은 [IMPROVEMENT_ROADMAP.md](IMPROVEMENT_ROADMAP.md)에 있습니다.
