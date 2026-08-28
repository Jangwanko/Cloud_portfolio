# Project Context for Codex

이 파일은 새 Codex 세션이 프로젝트를 다시 처음부터 분석하지 않도록 현재 기준 사실을 고정하는 문서입니다. 작업을 시작하기 전에 이 파일을 먼저 읽고, 세부 수치가 필요할 때만 README와 docs를 확인합니다.

## Collaboration Preferences

- 사용자가 commit, push, merge, GitHub Actions 확인을 명시적으로 요청하면 해당 비파괴 작업은 단계마다 다시 묻지 않고 검증까지 완료합니다.
- 승인된 Git·네트워크 명령 형식을 재사용하고 관련 확인을 가능한 범위에서 묶어 불필요한 sandbox 승인 창을 최소화합니다.
- destructive operation, secret·데이터 손실 위험, 의미를 확정할 수 없는 merge conflict처럼 실제 사용자 판단이 필요한 경우에만 중단하고 보고합니다.
- 원격 CI가 진행 중이면 별도 확인을 요청하지 않고 완료될 때까지 기다린 뒤 결과와 최종 bot commit을 확인합니다.

## Current Project Identity

- 현재 최종 포트폴리오 정체성은 **이벤트 처리 워크로드를 위한 Kubernetes·GitOps 운영 플랫폼**입니다. 직접 만든 **Kafka 기반 고신뢰 이벤트 처리 시스템**(`Reliable Event Processing System`)은 배포, lag 기반 확장, 관측, 장애 복구, backup/restore를 검증하는 workload입니다.
- 현재 공개 핵심 계약은 `POST /v2/streams/{stream_id}/events`입니다. client는 `event_type`, JSON `payload`, JSON `metadata`를 보내고 API가 accepted/Kafka envelope에 `schema_version=2`를 부여합니다. v2 request status/event list GET alias를 제공하며 인증과 stream 생성은 공유 `/v1` resource API를 사용합니다.
- 주문·결제 lifecycle은 범용 처리 경계를 보여주는 reference scenario입니다. `/v1/orders/{order_id}/events`, `category`, `payment_id`, body-only stream route는 기존 client와 과거 증거를 위한 compatibility adapter/alias로 유지하며 핵심 정체성으로 설명하지 않습니다.
- 데모는 주문 lifecycle을 reference scenario로 사용하되 Kafka append와 DB persistence를 서로 다른 운영 증거로 보여줍니다.
- Kafka / Worker / DLQ / PostgreSQL read model / observability는 event domain과 무관하게 수락 이후 처리와 장애 대응을 담당합니다.
- 마지막 승격 기준 브랜치는 `master`입니다. 2026-08-10 notification batch 최적화는 `master` merge `7035cda`, CI image `7035cdab4050`으로 승격했습니다. Runtime log·backup retention·README 최적화는 `dev-kafka` source `a2b157f`, CI image `a2b157f1283f`까지 검증했습니다.
- 브랜치 운영 기준: `master`는 최종 병합 / 보관 장소이며, 일반 개발과 문서 개편의 기본 작업 브랜치는 `dev-kafka`입니다. 저사양 데모 관련 개발 작업은 `demo-dev`에서 진행합니다. 작업 시작 전 현재 브랜치를 확인하고, 대상 역할에 맞는 브랜치에서 진행합니다.
- API는 PostgreSQL에 먼저 쓰지 않고 Kafka `message-ingress` topic에 append한 뒤 `202 Accepted`를 반환합니다.
- Worker consumer group `message-worker`가 Kafka partition을 consume하고 PostgreSQL HA에 비동기로 persistence합니다.
- 범용 event의 `schema_version`, `event_type`, `payload`, `metadata`는 Kafka envelope와 PostgreSQL persistence 경계에서 구조화된 필드로 유지합니다. 과거 `body`, `category`, `payment_id`는 호환 필드입니다.
- 실패 event는 retry 후 `message-ingress-dlq`에 격리하며, replay guard와 DLQ API가 있습니다.
- 같은 stream ordering은 global ordering이 아니라 `stream_id` 기준 Kafka partition boundary와 Worker inline retry로 보장합니다.
- PostgreSQL은 request status와 event read의 최종 durable source of truth입니다. DB read 장애 시 API는 stale data 대신 `503`을 반환합니다.
- Worker는 DB commit 뒤 notification job만 `message-notifications`에 publish합니다. 현재 transactional outbox가 없어 commit 이후 process crash에 따른 notification publish gap은 개선 과제입니다.
- API pod별 materialized cache와 `message-request-status`, `message-snapshots`, `stream-snapshots` topic은 2026-08-05 source candidate에서 제거했습니다. 운영 본체와 직접 연결되지 않은 replay·freshness·watermark 경계를 줄인 결정입니다.
- `/health/ready`는 schema, Kafka, PostgreSQL HA, auth secret만 판정합니다. Worker replica는 readiness에서 분리한 `/ops/summary`와 Grafana에서 확인합니다.
- `X-Idempotency-Key`는 API hot path에서 PostgreSQL claim을 만들지 않고 Kafka payload에 포함됩니다. 최종 idempotency / deduplication은 Worker persistence 단계의 PostgreSQL state에서 처리합니다.
- Worker autoscaling은 KEDA Kafka scaler의 consumer lag를 사용합니다. API autoscaling은 CPU HPA를 사용합니다.
- DLQ list / summary는 append-only Kafka DLQ log의 최근 표본입니다. unresolved depth나 현재 backlog가 아니며, `oldest_sample_age_seconds`를 미해결 event SLO로 해석하지 않습니다.
- 여기서 고신뢰는 per-stream partition ordering boundary, record 단위 explicit offset commit, PostgreSQL idempotent persistence, retry/DLQ/replay, 상태 분리, 관측·장애 복구 검증을 뜻합니다. exactly-once, global ordering, 무손실, production SLA를 증명했다는 의미가 아닙니다.
- `message-*` Kafka topic, `message-worker` consumer group, `messaging-app` namespace, `rooms`/`messages` table처럼 배포와 저장 상태에 연결된 물리 식별자는 호환성을 위해 유지합니다. 문서 정체성 변경을 이유로 이름만 바꾸지 않습니다.
- generic v2 rollout은 대칭 호환이 아닙니다. GitOps는 gate `false`인 `messaging-env` Secret wave `-3` → 일반 Sync migration Job wave `-2` → dual-read/dual-write Worker wave `-1` → API wave `0` 순서이며, `local-ha` overlay가 API container에만 gate `true`를 명시합니다. 수동 local manifest도 `false`로 시작하고 quick start가 Worker rollout 뒤 API env를 `true`로 전환합니다. v2 traffic을 구 Worker보다 먼저 열면 legacy body preview만 저장되고 구조화 `payload`/`metadata`가 유실됩니다.
- 기존 Kafka 성능 baseline은 legacy/order contract로 측정한 역사적 intake 증거입니다. v2 generic envelope 성능으로 재표현하지 않습니다. 2026-07-21 generic v2 회복 후보는 같은 클린 조건 3회에서 첫 v2 후보보다 모든 intake 지표가 개선됐지만 historical stable legacy baseline에는 미달하므로 stable baseline으로 승격하지 않습니다.

## Do Not Mix Redis and Kafka Results

Redis queue-first 결과와 Kafka event stream 결과를 섞지 않습니다.

Redis queue-first 성능 개선 수치:

| 단계 | 요청 수 | 평균 응답 | p95 |
| --- | ---: | ---: | ---: |
| 초기 기준 | `5,434` | `3,660ms` | `8,175ms` |
| pgpool / DB pool 조정 후 | `11,314` | `1,519ms` | `3,333ms` |
| KEDA queue depth 적용 후 | `19,528` | `811ms` | `1,954ms` |

이 수치는 Redis 기반 구조에서 CPU HPA / queue depth KEDA / hot path 튜닝 효과를 설명할 때만 사용합니다.

Kafka event stream 성능 기준선:

| 실험 | 요청 수 | 오류율 | 평균 | p95 | p99 | Worker |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1차 Kafka baseline | `31,710` | `0.00%` | `44.04ms` | `86.95ms` | `113.78ms` | KEDA 최종 `8` |
| 2차 Kafka baseline | `31,676` | `0.00%` | `44.13ms` | `80.65ms` | `103.57ms` | KEDA 최종 `4` |

Kafka 1차/2차 비교는 Worker scaling ON/OFF 비교가 아닙니다. Pgpool HA, pool 튜닝, Worker inline retry, stream ordering 보강 후에도 Kafka append-first intake baseline이 유지되는지 확인한 결과입니다.

2026-08-10 동일 notification batch candidate에서 Worker fixed `2`와 KEDA `2→4`를 clean 조건으로 각각 3회 비교했습니다. KEDA 효과는 API request count보다 consumer lag, backlog drain time, backlog 처리율을 중심으로 해석하며 API latency trade-off를 함께 기록합니다.

## Current Kafka Validation Summary

- Kafka broker: 3-broker KRaft StatefulSet, `3/3 ready`.
- Topics: `message-ingress`, `message-ingress-dlq`, `message-notifications`.
- Topic settings: partitions `8`, replication factor `3`, `min.insync.replicas=2`.
- Same stream ordering: 100 events persisted as `stream_seq 1..100`.
- Stable Kafka intake baseline: 100 VU / 30s, `31,676` requests, error `0.00%`, p95 `80.65ms`, p99 `103.57ms`.
- 2026-07-21 first generic v2 performance candidate: local `dev-kafka` revision `1439be1`, API/Worker image `d31ac14`, 100 VU / 30s hot single stream, total HTTP `25,382`, actual event `202` `25,378`, error `0.00%`, avg `67.83ms`, p95 `123.96ms`, p99 `153.10ms`; stable baseline으로 승격하지 않습니다.
- 2026-07-21 generic v2 recovery candidate: dirty `dev-kafka` worktree의 local image `perf-v16`, API min `6`, ingress producer `1`/pod, 100 VU / 30s hot single stream을 clean DB/topic 조건으로 3회 반복. Event `202` 평균 `29,168`(범위 `28,749~29,608`), error 모두 `0.00%`, avg `51.81ms`, p95 `101.27ms`, p99 `140.59ms`, main drain 평균 `508.58s`. 첫 v2 후보 대비 event `14.93%` 증가, avg/p95/p99 `23.62%`/`18.30%`/`8.17%` 감소, drain 처리율 약 `69.3%` 증가. API floor 변경과 uncommitted image 조건을 포함하며 stable legacy baseline 승격 제외.
- 2026-07-21 64-stream Worker A/B candidate: dirty image `perf-v17`, API `6`, clean DB/topic, 100 VU / 30s. Fixed `2`는 event `22,125`, p95 `169.24ms`, message/notification peak lag `21,170`/`45`, all drain `301.42s`. KEDA `2→8`은 event `20,499`, p95 `212.60ms`, peak `18,950`/`11,536`, all drain `261.17s`. Drain `13.35%` 감소와 notification backlog 이동을 확인했으나 intake event `7.35%` 감소·p95 `25.62%` 증가, 각 1회와 dirty image 조건으로 stable baseline 제외.
- 2026-08-05 current simplified v2 hot-stream candidate: local image `messaging-portfolio:v2-core-cleanup`, API `6`, clean DB/topic, 100 VU / 30s, 3회 평균 event `33,201`, error `0.00%`, avg `39.61ms`, p95 `76.57ms`, p99 `111.49ms`, peak lag `31,422`, main drain `364.62s`. 제거 전 v2 recovery 후보 대비 event `13.83%` 증가, p95 `24.39%` 감소, drain `28.31%` 감소. dirty local image 조건으로 stable baseline 제외.
- 2026-08-05 current 64-stream record-commit candidate: fixed core `2` 1회는 event `30,566`, p95 `93.55ms`, drain `295.99s`. core KEDA `2→4`·notification KEDA `1→2` 3회는 event `31,644`/`31,853`/`28,605`, p95 `88.06`/`87.64`/`107.41ms`, drain `295.90`/`305.97`/`321.29s`. 최신 tracked run peak message/notification lag `25,905`/`1,141`, final `0/0`, ordering `100/100`. 편차로 KEDA 성능 우위와 stable baseline 제외.
- 2026-08-10 notification batch 64-stream A/B candidate: local image `messaging-portfolio:notification-batch`, API `6`, clean DB/topic, 100 VU / 30s, fixed `2`와 KEDA `2→4` 각 3회. Fixed 평균 event `30,289.67`, p95 `88.53ms`, drain `222.49s`, backlog 처리율 `121.42 events/s`. KEDA 평균 event `30,351.33`, p95 `94.28ms`, drain `194.05s`, backlog 처리율 `137.67 events/s`. KEDA 처리율 `13.38%` 증가, drain `12.78%` 감소, p95 `6.49%` 증가. 모든 실행 error `0.00%`, ordering `100/100`, final lag `0/0`; dirty local image라 stable release baseline 제외.
- poll-batch offset commit 실험은 paired KEDA arm의 drain이 fixed보다 `9.24%` 길어 폐기했습니다. current source는 성공/terminal record 단위 explicit offset commit을 유지합니다.
- 이 v2 후보는 stable legacy baseline 대비 total requests `19.87%` 감소, avg/p95 `53.70%` 증가, p99 `47.82%` 증가입니다. 2026-06-18 last legacy raw 대비 total requests `8.68%` 감소, avg `17.68%`, p95 `3.92%`, p99 `1.66%` 증가입니다. Fresh cluster clean state와 현재 resource 조건이 함께 달라 원인을 v2에만 귀속하지 않습니다.
- 같은 실행의 ordering은 100 events, `stream_seq 1..100`, `7.93s`, pass입니다. Persistence sample은 50/50, status-observed avg `79.96ms`, p95 `81.28ms`, max `2384.10ms`이며 client polling/network를 포함해 과거 row-visible proxy와 비교하지 않습니다.
- 같은 실행의 message-worker peak lag는 `24504`, notification-worker peak lag는 `6`, main drain은 `751.76s` 뒤 모두 `0`입니다. HPA probe 뒤 추가 lag `4962`도 수동 관측 약 `160s` 내 `0`으로 drain했습니다. Worker histogram query `60s`는 최대 finite bucket 경계 포화로 exact p95 해석에서 제외합니다.
- Stable baseline row-visible latency proxy p95: `7.67ms`. 이 수치는 API accepted 시각과 PostgreSQL row의 `created_at` 또는 조회 가능 시점을 비교한 값이며 DB commit timestamp 직접 측정값이 아닙니다.
- 2026-06 성능 원본의 event response status는 `200`입니다. route에 `202 Accepted` 계약을 명시하기 전 수집한 historical pre-contract-fix evidence로 유지합니다. 현재 build의 `202`는 2026-07-21 v2 suite에서 별도로 검증했습니다.
- 2026-06-09 k6 rerun: 100 VU / 30s, `34,284` requests, error `0.00%`, avg `36.86ms`, p95 `66.06ms`, p99 `104.99ms`; same-stream ordering revalidated with `stream_id=30`, 100 events, `stream_seq 1..100`, ordering `pass`; async persistence sample used `stream_id=31`, 50 events persisted; Worker consumer lag reached `36394` and drained to `0` after about 14 minutes. Treat this as an intake-vs-persistence capacity signal, not a direct replacement for the stable 2nd baseline. 이 실행의 persistence latency도 row-visible proxy입니다.
- Worker success path transaction tuning is applied after that rerun: message persistence and request status update share one PostgreSQL transaction; notification enqueue happens after DB commit.
- Notification attempts are decoupled from core persistence through Kafka `message-notifications` and a separate `notification-worker`.
- Notification Worker는 poll당 최대 20건을 한 PostgreSQL statement·transaction으로 저장합니다. DB commit 뒤 offset은 record 단위로 commit하고, DB 오류 시 partition 첫 record rewind, DataError 시 record 처리 fallback을 사용합니다.
- 현재 notification 경계는 `notification_attempts` 기록까지입니다. 이메일/SMS/push 같은 외부 채널의 실제 발송 성공을 구현하거나 증명한 것으로 쓰지 않습니다.
- Post-tuning performance suite at `2026-06-09T02:17:11+09:00`: `28,839` requests, error `0.00%`, avg `53.47ms`, p95 `108.68ms`, p99 `134.53ms`; same-stream ordering revalidated with `stream_id=34`, 100 events, `stream_seq 1..100`, ordering `pass`; async persistence sample used `stream_id=35`, row-visible proxy p95 `8.08ms`; Worker consumer lag reached `29204` and drained to `0` after about 10 minutes. Treat this as persistence-path improvement but not a stable intake baseline replacement because API intake throughput and p95 worsened.
- Notification path split suite at `2026-06-18T03:29:47+09:00`: `27,795` requests, error `0.00%`, avg `57.64ms`, p95 `119.28ms`, p99 `150.60ms`; same-stream ordering revalidated with `stream_id=38`, 100 events, `stream_seq 1..100`, ordering `pass`; async persistence sample used `stream_id=39`, row-visible proxy p95 `22.13ms`; Worker consumer lag drained to `0` after about 16 minutes; notification-worker consumer lag was `0`. Treat this as operational-boundary improvement, not a performance improvement.
- Ordering / failure injection validation: `single_no_failure`, `multi_no_failure`, `single_db_failure`, `multi_db_failure` all passed on 2026-06-08 with accepted=persisted, missing `0`, duplicate `0`, mixed payload `0`, DLQ `0`.
- Ordering / failure injection payloads: stream A uses `A001..A100`, stream B uses `B001..B100`, stream C uses `C001..C100`.
- Ordering / failure injection verifies final persistence by querying PostgreSQL `messages` rows from inside the API pod, not by trusting Kafka accept alone.
- 2026-07-21 cache fallback 검증은 historical evidence입니다. 해당 cache 경로는 2026-08-05 source candidate에서 제거했습니다.
- Local HA topology: Kafka `3`, PostgreSQL `3`, Pgpool `2`, API min `6`, core Worker `2→4`, notification Worker `1→2`, DLQ replayer `1`.
- Core Worker KEDA trigger: topic `message-ingress`, consumer group `message-worker`, lag threshold `100`, min replicas `2`, max replicas `4`.
- Notification Worker KEDA trigger: topic `message-notifications`, consumer group `notification-worker`, lag threshold `100`, min replicas `1`, max replicas `2`.
- API HPA: CPU target `65%`, min replicas `6`, max replicas `8`; scale-up stabilization `60s`, 최대 `2 pods/60s`, scale-down stabilization `120s`.
- API readiness hard failures: schema startup 미완료, Kafka unreachable, non-local unsafe auth secret(기본값·known placeholder·빈 값·32-byte 미만). PostgreSQL primary/standby/sync/replication guardrail 이탈은 `degraded`; Worker 정보는 state 결정 제외.
- HTTP request body는 기본 `1 MiB` transport 상한을 declared/chunked body 모두에 적용합니다. generic payload/metadata 상한은 각각 `65,536`/`16,384` UTF-8 JSON bytes이며 transport 상한과 구분합니다.
- Pgpool SPOF is reduced, not fully eliminated: Pgpool has `2` replicas and PDB `minAvailable=1`, but local kind remains a single-node demo environment.
- Ops Agent Phase 1은 Application, Prometheus, Kubernetes, Argo CD를 fixed read-only collector로 읽어 `ops.evidence.v1` bundle을 생성합니다. 2026-08-12 actual `local-ha`에서 Kafka partition `8/8`, lag `0`, Worker `2/2`, PostgreSQL HA, Argo `Synced / Healthy`를 확인했습니다.
- Phase 2 deterministic evaluator는 immutable 단일 `ops.evidence.v1`을 `ops.conditions.v1`, ordered sequence를 `ops.conditions.v2`로 평가합니다. condition별 required/optional evidence를 분리하며 `PARTIAL` bundle을 공통 `UNKNOWN` gate로 사용하지 않습니다.
- Phase 2 v1은 full 60초 Kafka lag `0`, PostgreSQL readiness reason 부재, observed generation의 Worker full availability만 `ABSENT`로 확정합니다. 양수 lag pressure/concentration과 단일 replica shortfall은 sustain/grace policy가 부족하므로 `UNKNOWN`입니다.
- Phase 2.5는 2026-08-16 actual `local-ha`에서 64-stream, 100 VU, 30초 부하를 현재 KEDA `2→4` 정책 그대로 3회 실행하고 15초 간격 Evidence Bundle 71개를 보존했습니다. 세 run peak lag는 `17,537` / `25,256` / `24,096`, lag `0` 복귀는 `196.781s` / `256.575s` / `256.543s`입니다. lag `>=7,000`, 60초 slope `>=100/s`, 세 capture와 두 번의 lag 증가는 `local-ha.conditions.v2` activation rule로 구현했습니다. `produce-committed`는 산술 검증일 뿐 독립 vote가 아닙니다.
- Phase 2.6 negative controls는 같은 candidate를 변경하지 않고 short burst, 180초 sustainable high, single transient spike에 적용했습니다. Peak lag `3,997` / `3,111` / `8,854`, candidate는 모두 `NOT_PRESENT`입니다. Actual v2 replay에서도 세 control은 `PRESENT`가 아니며 positive 세 run은 모두 `[1,2,3]` window에서 `PRESENT`입니다. V1과 recovery/clearing rule은 변경하지 않았습니다.
- Phase 3 single Diagnosis Agent는 `CORE_BACKLOG_PRESSURE=PRESENT`와 ordered digest를 다시 검증한 뒤 fixed normalized read-only tool 9개만 선택합니다. 출력은 `ops.diagnosis.v1`이며 evidence ID citation, hypothesis gap, structured stop reason을 보존합니다. 기본 모델은 `gpt-5.6-luna`, live call은 `--live` opt-in이며 normal CI는 API를 호출하지 않습니다. Condition 재판정, recovery, remediation, arbitrary shell/kubectl/PromQL/URL은 금지됩니다. Phase 3.1은 validator semantic failure에 tool 없는 output repair를 최대 1회 허용하며 transport retry와 별도 budget으로 관리합니다.
- Phase 4 recovery calibration은 host-local k6 arrival-rate workload와 measurement orchestrator입니다. 최종 `20260816T100600Z`는 rates `0/30/75/110/330 records/s`, 64 streams, A/B/C 각 1회, continuous-ingress E 3회, zero-ingress F 1회를 current KEDA `2→4` 그대로 실행했습니다. E peak lag `20,806 / 21,834 / 21,151`, F `20,998`이며 모두 기존 v2 activation을 재현했습니다. 349 bundle과 1,396 raw projection hash 검증은 PASS입니다.
- Phase 4.1 deterministic recovery v1은 기존 `CORE_BACKLOG_PRESSURE=PRESENT` activation과 ordered post-activation bundle digest를 입력으로 ACTIVE/RECOVERING/UNKNOWN을 평가합니다. RECOVERING은 fresh usable 3 capture, 각 60초 slope `<0`, committed rate `>=` produce rate, PostgreSQL ready를 요구합니다. Kafka exporter negative lag는 `INVALID_ONLY`로 보존하며 clamp/derived replacement를 하지 않습니다.
- Phase 4.2 `worker-backlog-local-ha.recovery.v2`는 기존 v1을 기본 호환 policy로 유지하면서 MEDIUM measured ingress `74.9833~77.0833/s`, lag `<=22`, slope `<=0`, fresh usable PostgreSQL-ready capture 3개가 RECOVERING 뒤 연속된 경우에만 incident-scope `WORKER_BACKLOG_RECOVERED`를 반환합니다. E1~E6 stable count는 `3/1/3/4/5/14`, v2 replay는 E1·E3·E4·E5·E6 RECOVERED, E2 UNKNOWN입니다. Worker replica/KEDA 상태/lag==0은 필수 조건이 아니며 clearing, post-recovery regression manager, Recovery LLM, remediation은 미구현입니다.
- Unit / contract test count는 코드 변경에 따라 달라지므로 현재 작업에서 `.venv\Scripts\python.exe -m pytest -q`를 실행하고 실제 출력을 보고합니다. 과거 문서의 서로 다른 pass count를 현재 상태로 재사용하지 않습니다.
- 2026-07-14 전체 정합성 감사의 identity refactor 이전 local suite는 `115 passed`입니다.
- 2026-07-14 generic v2 전환 작업 중간 checkpoint의 local suite는 `195 passed`입니다. 같은 날 이후 reliability 보강 결과나 현재 pass count로 해석하지 않으며, 이후 변경에서는 이 수치를 복사하지 말고 suite를 다시 실행합니다.
- 2026-07-21 benchmark/tooling, PostgreSQL restart recovery, backup/restore, cache readiness 보강 뒤 local suite는 `359 passed`입니다.
- 2026-07-21 performance recovery, cache replay, clean benchmark reset, HPA 안정화 보강 뒤 local suite는 `363 passed`입니다.
- 2026-07-27 local Demo UI 동시 진행률과 Worker peak contract 보강 뒤 local suite는 `364 passed`입니다.
- 2026-07-27 local rollout: Argo CD revision `ddb888a`, `Synced / Healthy`, API/Worker image `1cd84d4df742`, API `6/6`, Worker `2/2`, readiness `ready`, UI `2.2.0`, concurrent persistence polling과 Worker peak asset 반영 확인.
- 2026-07-27 Demo UI `2.3.0` 정보 구조·Advisor 판정 정리 뒤 local suite는 `364 passed`입니다.
- 현재 `dev-kafka` source candidate는 Demo UI `2.4.1`, API `2.1.0`입니다. Worker 현재/최대 수는 `/ops/summary`에서 읽고, Kafka append와 DB persistence를 병렬 갱신합니다. UI `2.4.1`은 actual Phase 5.1 diagnosis의 sanitized recorded tool→evidence→hypothesis replay, validator/read-only boundary와 첫 화면 replay 진입부를 포함하며 OpenAI API를 재호출하지 않습니다. CI validation을 통과한 dev-kafka image는 `1aca8155092a`입니다.
- 2026-07-27 master 문서·container 최적화 기준 local suite는 `365 passed`입니다.
- 2026-07-27 demo-lite generic v2 동기화와 Demo UI `2.3.0` 후보의 `demo-dev` suite는 `368 passed`입니다.
- 2026-08-05 v2 운영 본체 단순화는 Demo UI `2.3.1`, API `2.1.0`, local suite `345 passed`, master merge `cab7647`로 승격했습니다. dev-kafka CI `#76`, master CI `#77`의 validate·publish를 통과했습니다.
- 2026-08-10 notification batch 최적화는 local suite `354 passed`, clean 64-stream fixed/KEDA 각 3회, source `8d334b8`, dev image `8d334b8abeaf`, master image `7035cdab4050`까지 게시했습니다.
- 2026-08-11 runtime log·backup retention·README 최적화는 local suite `357 passed`, image `messaging-portfolio:runtime-log-opt` `59,777,816` bytes, user `10001:10001`, live·readiness·metric·logger smoke를 통과했습니다. 64-stream clean KEDA candidate 2회와 published image paired control 1회는 처리량·p95·drain이 엇갈려 throughput 개선 근거와 stable baseline에서 제외합니다. `dev-kafka` source `a2b157f`, CI `#83`, image `a2b157f1283f`, overlay commit `004f2e7`까지 게시했습니다.
- 2026-08-16 Phase 3.1 candidate 기준 full suite `515 passed`, Ops Agent suite `158 passed`입니다. 9개 offline golden fixture와 5개 output repair fixture는 schema/citation/tool/abstention/budget/stop/repair 계약을 통과했습니다. Actual positive run-01 Luna dry-run은 4개 normalized tool 호출 뒤 최초 stop consistency INVALID, tool-free repair 1회 후 VALID로 완료됐습니다. 최종 stop은 `insufficient_evidence`, API turns `6`, total tokens `44,264`이며 artifact는 local-only입니다. Captured reference는 source/raw/canonical bundle provenance를 함께 기록합니다.
- 2026-08-16 Phase 4 calibration candidate 기준 full suite `544 tests passed`, Ops Agent suite `187 tests passed`입니다. compileall, k6 arrival-rate inspect, 349 bundle/1,396 raw hash audit, changed-file secret scan, git diff check를 통과했습니다. OpenAI API, recovery evaluator/state, remediation은 이 작업에서 사용하거나 구현하지 않았습니다.
- 2026-08-17 Phase 4.2 RECOVERED candidate 기준 full suite `585 passed`, Ops Agent suite `228 passed`입니다. 신규 E4~E6 219 bundles/876 raw, combined E1~E6 438 bundles/1,752 raw hash consistency와 workload attainment를 검증했습니다. OpenAI API 및 runtime control-plane write는 없었습니다.
- 2026-07-21 local live: Argo CD `Synced / Healthy`, deployment-bearing image-tag revision `b84c379`, API/Worker image `9349ba9`, API `2.0.0`, generic v2 `202`, materialized cache `ready=true` / `hydrated=true`, core workload ready, normalized message/notification consumer lag `0` 확인. 이후 docs-only revision advance는 workload 변경으로 해석하지 않습니다.
- 2026-07-21 master promotion: merge `8f5d78c`, GitHub Actions CI run `#55`의 validate/publish job success, GHCR image `8f5d78c6963a`, overlay bot commit `717e0ca`. Local Argo CD는 `dev-kafka`를 추적하므로 master image의 local runtime 배포 증거는 아닙니다.
- 2026-07-21 dev-kafka delivery gate remote 검증: source `041ab21` → image `041ab21cf795` → overlay bot commit `e3bf987`, direct-language source `043df1b` → image `043df1bd3f24` → overlay bot commit `9ded313` 확인. Local Argo runtime rollout은 별도 확인 대기입니다.
- Namespace prune 전환 결함으로 namespace-scoped PostgreSQL/Pgpool, local demo row와 in-cluster backup PVC가 삭제됐습니다. 같은 kind cluster에 PostgreSQL/Pgpool을 clean reinstall했고 삭제된 local demo data는 복구하지 못했습니다. 2026-07-21 v2 suite는 이 clean DB state에서 실행했습니다.
- Reinstall 뒤 manual backup Job 완료와 새 `postgres-backups` PVC `Bound`를 확인했습니다. 이어 host `backups/`에 `39,433,414` byte logical dump를 만들고 disposable database에 복원해 10개 table row count, Alembic `0008`, generic v2 row `33,840`, max id/sequence가 원본과 일치함을 확인한 뒤 임시 DB를 삭제했습니다. 같은 host 장애를 견디는 object storage 사본과 정기 restore drill/복구 orchestration 자동화는 아직 없습니다.
- PostgreSQL HA chart의 sync environment는 first boot에만 적용되어 persisted-volume 재시작 뒤 `synchronous_standby_names`가 사라질 수 있습니다. Install/DB recovery 경로는 모든 ready PostgreSQL pod에 `synchronous_commit=on`, `ANY 1`을 `ALTER SYSTEM`으로 지속 적용하고 현재 primary의 streaming sync/quorum standby `>=1`을 확인해야 완료입니다.
- Public demo-lite는 2026-08-28 release `2fc8649`, image `ece446d47370`, UI `2.4.1`, API `2.1.0`, replay `200`/`VALID`, readiness `ready`, Worker `1/1`, KEDA max `2`를 확인했습니다. 저사양 topology는 Kafka `1`, PostgreSQL `1`, API·core Worker `1→2`, notification Worker fixed `1`입니다.
- `demo-dev`는 public demo-lite의 저사양 자원 경계를 관리합니다. Kafka append·DB persistence 동시 진행률, 운영 상태 패널의 Worker 현재/최대 replica, compact DB 저장 증거, 진행 중 Advisor 판정, migration → Worker → API gate를 포함합니다.

## Important Docs

- `README.md`: 국내 Cloud/DevOps 지원용 interview-facing overview.
- `README_EN.md`: Canada/해외 지원용 English overview.
- `docs/TEST_RESULTS.md`: current validation results and measurement conditions.
- `docs/ARCHITECTURE.md`: Kafka-centered architecture, ordering boundary, autoscaling design.
- `docs/RELIABILITY_POLICY.md`: degraded / critical interpretation.
- `docs/OBSERVABILITY.md`: Grafana / Prometheus operating signals.
- `docs/OPS_AGENT.md`: Phase 1 evidence, Phase 2 condition, Phase 3 grounded diagnosis, Phase 4 recovery calibration contract.
- `ops_agent/README.md`: collector/evaluator local execution and security boundary.
- `docs/RUNBOOK.md`: incident response and operational checks.
- `docs/SERVICE_REQUIREMENTS.md`: service assumptions, SLO guardrails, operational purpose.
- `docs/KAFKA_EXPERIMENT.md`: Kafka migration experiment notes.
- `docs/PATCH_NOTES.md`: change history.
- `docs/IMPROVEMENT_ROADMAP.md`: prioritized improvements with measurable completion criteria.
- `results/README.md`: validation evidence provenance and interpretation rules.
- `results/kafka-performance/latest.txt`: most recent local Kafka performance suite output, when present.
- `results/ordering-failure/latest.json`: most recent ordering / failure injection result, when present.
- `results/postgres-restore/latest.json`: most recent logical backup / disposable restore consistency result, when present.
- `results/postgres-recovery/latest.json`: most recent tracked structured summary of PostgreSQL restart/sync/outage recovery, when present.
- `k8s/gitops/base/kafka-ha.yaml`: local Kafka KRaft StatefulSet and topic bootstrap.
- `k8s/gitops/base/manifests-ha.yaml`: generated local HA application, observability, HPA/KEDA, alerting manifest.
- `k8s/gitops/base/migration-job.yaml`: Argo 일반 Sync wave `-2` schema migration Job; Worker/API wave보다 먼저 완료합니다.
- `portfolio/api.py`: FastAPI intake, PostgreSQL status/read, DLQ API.
- `portfolio/order_events.py`: `/v1/orders/...` reference adapter용 legacy classification helper; generic core 규칙으로 확대하지 않습니다.
- `worker/main.py`: Kafka consumer, PostgreSQL persistence, inline retry, DLQ movement, notification enqueue.
- `portfolio/kafka_client.py`: Kafka producer/consumer helpers.

## Common Commands

Run tests:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Check portfolio status:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_portfolio_status.ps1 -SkipArgoCd
```

Collect GitOps local-ha read-only evidence:

```powershell
.venv\Scripts\python.exe -m ops_agent collect --profile local-ha --incident-id phase1-live-validation --context kind-messaging-ha --output results\ops-agent\evidence-live.json
```

Evaluate a frozen Evidence Bundle:

```powershell
.venv\Scripts\python.exe -m ops_agent evaluate --input results\ops-agent\live-baseline\no-backlog-20260812.json --output results\ops-agent\live-baseline\no-backlog-20260812.conditions.json
```

Evaluate an ordered Evidence Bundle sequence:

```powershell
$inputs = Get-ChildItem results\ops-agent\calibration\20260816T032411Z\run-01\bundles\sample-*.json | Sort-Object Name | ForEach-Object FullName
.venv\Scripts\python.exe -m ops_agent evaluate-sequence --input $inputs --output results\ops-agent\conditions-v2.json
```

Run Phase 4 recovery calibration without changing KEDA/Worker settings:

```powershell
.venv\Scripts\python.exe scripts\worker_recovery_calibration.py --mode calibrate --context kind-messaging-ha --low-rate 30 --medium-rate 75 --high-rate 110 --overload-rate 330 --overload-seconds 90 --recovery-phase-seconds 900 --e-repeats 3
```

Run the supplemental continuous-ingress RECOVERED calibration:

```powershell
.venv\Scripts\python.exe scripts\worker_recovered_calibration.py --context kind-messaging-ha
```

Replay a recovery sequence with the explicit recovered policy:

```powershell
.venv\Scripts\python.exe -m ops_agent evaluate-recovery --policy-version v2 --activation <conditions.v2.json> --input <ordered-bundle.json> --output <recovery.json>
```

Run the controlled multi-stream Worker backlog calibration:

```powershell
.venv\Scripts\python.exe scripts\worker_backlog_calibration.py --runs 3 --streams 64 --vus 100 --duration 30s --think-time 0.05 --sample-interval-seconds 15 --context kind-messaging-ha
```

Run the frozen pressure-candidate negative controls:

```powershell
.venv\Scripts\python.exe scripts\worker_backlog_negative_controls.py --sample-interval-seconds 15 --context kind-messaging-ha
```

Run Kafka performance suite:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_kafka_performance_suite.ps1
```

Run ordering / failure injection validation:

```powershell
.venv\Scripts\python.exe scripts\ordering_failure_injection.py --scenario all --event-count 100
```

Latest ordering / failure injection result after fixing local client skew:
- `single_no_failure`: 100 accepted / 100 persisted, ordering PASS, `6.812s`.
- `multi_no_failure`: 300 accepted / 300 persisted across A/B/C streams, ordering PASS, `6.156s`.
- `single_db_failure`: 100 accepted / 100 persisted, Pgpool outage `20.406s`, recovery to completion `1.297s`.
- `multi_db_failure`: 300 accepted / 300 persisted, Pgpool outage `21.313s`, recovery to completion `1.344s`.
- Earlier `~210s` ordering/failure injection durations were invalid as performance evidence because Python `urllib` calling `http://localhost` on Windows / Docker Desktop added about 2 seconds of client-side delay per request. The script now defaults to `http://127.0.0.1` with `Host: localhost`.

## Documentation Rules

- git commit, merge, push, PR 생성은 항상 사용자에게 먼저 확인받습니다. 테스트가 통과했더라도 확인 없이 커밋하거나 병합하지 않습니다.
- README는 포트폴리오 첫 화면 역할로 유지합니다. 모든 세부 내용을 README에 넣지 말고, 핵심 요약 / 데모 진입 / 대표 검증 결과 / 문서 지도만 남깁니다.
- README 상단 순서는 Kubernetes 설계 → Pod 구성 → AWS 대응 관계 → 관측 지점 → STAR 운영 경험으로 유지합니다. STAR는 current와 historical을 분리하고 Redis·Kafka baseline을 섞지 않습니다. Kafka contract와 backend reliability 세부 내용은 workload 설명과 하위 문서에 둡니다.
- README의 기본 설명과 사용법은 외국인 리크루터도 볼 수 있게 한국어와 영어를 함께 사용합니다. 전체 문서를 완전 번역하지는 않더라도, project summary, demo usage, AWS migration blueprint는 영어 문장을 같이 둡니다.
- README에서 자세한 내용을 docs로 넘길 때는 링크만 던지지 않습니다. 각 주제마다 2~4줄 요약, 왜 중요한지 한 문장, 관련 docs 링크를 함께 제공합니다.
- 세부 구현, 실험 과정, 운영 절차, 장애 대응, Terraform AWS migration blueprint는 docs 문서로 분리합니다.
- changelog, patch notes, test results, migration plan처럼 시간 흐름이 중요한 문서는 최신 항목을 위에 둡니다. 과거 기록은 아래쪽 historical section으로 보냅니다.
- `docs/PATCH_NOTES.md`는 최신 변경이 맨 위에 오도록 관리합니다.
- `docs/TEST_RESULTS.md`는 최신 검증 결과를 먼저 보여주고, 과거 baseline은 historical results로 분리합니다.
- `docs/AWS_IAC_PLAN.md`는 현재 AWS migration blueprint를 먼저 설명하고, 구현 단계와 모듈 세부 설명은 뒤에 둡니다.
- `docs/ARCHITECTURE.md`는 현재 최종 Kafka-centered 구조를 먼저 설명하고, 과거 전환 배경은 뒤쪽 또는 별도 문서로 둡니다.
- Terraform 문서는 로컬 검증 구조를 AWS managed architecture로 이전하는 migration blueprint 관점으로 씁니다. AWS 배포 증거는 실제 실행 결과가 있을 때 기록합니다.
- 문서에서 Kafka 최종 구조를 Redis에서 이름만 바꾼 것처럼 쓰지 않습니다.
- Kafka를 Kafka-only라고 과장하지 않습니다. 이 프로젝트는 Kafka-centered 구조이며 PostgreSQL state/read model을 유지합니다.
- Kafka Worker KEDA 효과를 API throughput 증가로 단정하지 않습니다. Kafka에서 Worker scaling 효과는 consumer lag, persistence latency, drain time으로 봅니다. 2026-06 PowerShell 원본은 DB row `created_at` / row-visible proxy이며 실제 commit timestamp로 부르지 않습니다. 현재 script의 `accepted_to_status_observed_ms`는 client가 `persisted` status를 본 시각까지로 polling/network를 포함합니다. Worker histogram은 `commit()` 반환 뒤 기록한 `persisted_at` 기준이며 새 cluster 측정 전입니다.
- Redis 성능 수치는 Redis 프로젝트의 이전 scaling/tuning 성과로만 설명합니다.
- Kafka 성능 수치는 append-first intake baseline과 ordering/recovery validation으로 설명합니다.
- 2026-06 성능 결과의 event status `200`은 `202 Accepted` route contract 명시 전의 역사적 증거로 표시합니다. 현재 HTTP 계약과 성능은 새 build에서 다시 측정합니다.
- DLQ API의 `recent_samples`, `by_reason`, `oldest_sample_age_seconds`는 조회한 append-only log 표본의 통계로 설명합니다. unresolved queue depth, 현재 backlog, 미해결 event SLO로 표현하지 않습니다.
- `results/README.md`, `results/kafka-performance/latest.txt`, `results/ordering-failure/latest.json`, `results/postgres-restore/latest.json`, `results/postgres-recovery/latest.json`은 Git 추적 대상으로 유지합니다. 새 실행은 원본, 조건, stable baseline 채택 여부 또는 restore/recovery 검증 범위를 함께 기록합니다.
- `ops_agent/fixtures/`는 synthetic test input, `results/ops-agent/live-baseline/`은 sanitized captured runtime evidence와 deterministic derived condition result로 분리합니다. live capture는 bundle/raw hash, source dirty state, collector tree hash, freshness/coverage, `raw_ref` 기준 경로를 함께 기록합니다.
- `dev-kafka`를 현재 기본 배포 브랜치처럼 쓰지 않습니다. GitOps 기본 revision은 `master` 기준입니다.
- 문서와 답변에서 대비를 앞세운 상투적인 문장 구성을 피합니다. 서술의 중요도를 비교형 도입으로 만들지 않고 주제와 판단 기준을 바로 선언합니다. "A까지 포함한다", "B로 이어진다", "A를 바탕으로 B를 처리한다"처럼 의미를 직접 씁니다.
- 영어 문서와 답변도 상투적인 부정-대조 구문을 쓰지 않습니다. 같은 의미가 필요하면 짧고 직접적인 문장으로 나눕니다.
- 불렛 문서는 문장형 끝맺이를 피합니다. `~합니다`, `~했습니다`, `~하지 않음`보다 `~ 확인`, `~ 대기`, `~ 분리`, `~ 유지`, `~ 제외` 같은 항목형 표현을 우선합니다.

## Demo UI Rules

- 데모 화면은 포트폴리오 시연용입니다. 현업 운영자가 보는 모든 raw id를 전부 노출하기보다, 처음 보는 사람이 Kafka -> Worker -> DB 흐름을 이해할 수 있는 신호를 우선합니다.
- 현재 `dev-kafka` source candidate는 Demo UI `2.4.1`, API `2.1.0`입니다. 범용 시스템 정체성과 주문 reference scenario 표시, 운영 refresh 기본 `30초`/선택 `60초`, auth token memory 재사용, Kafka append와 동시에 시작하는 stream persistence summary `1초` polling, `/ops/summary` Worker replica, `send_failed`/일부 미확인 종료, Pipeline Evidence 내부의 범용 envelope panel, user-filtered DLQ recent log detail/manual replay, 첫 화면에서 진입하는 sanitized recorded AI Investigation trace가 기준입니다.
- 문서에 등록된 public demo-lite deployment는 UI `2.4.1`, API `2.1.0`, image `ece446d47370`입니다.
- `demo-dev` candidate의 source Demo UI는 `2.3.0`입니다. Kafka append와 동시에 시작하는 persistence summary `1초` polling, 운영 상태 패널의 Worker 현재/최대 replica, Pipeline Evidence 내부의 compact DB 저장 증거가 기준입니다.
- 샘플 예약 버튼의 현재 기준은 `10개`, `100개`, `1000개`입니다.
- `예약 건수`는 전송 시작 후 `남은 예약/전체 예약`으로 표시합니다. API가 Kafka append에 성공하면 줄어듭니다.
- `Kafka 적재`는 API가 `message-ingress` topic append를 성공시킨 수입니다.
- `DB 저장`은 Worker가 PostgreSQL commit까지 완료한 수입니다.
- `총 소요시간`은 전송 시작부터 현재 run의 DB 저장 완료까지 걸린 시간입니다.
- Worker 표시는 운영 상태 패널 한 곳에서 core Worker의 `현재 replica/최대 replica` 형식으로 둡니다. 예: demo-lite `1/2`, full profile `2/4`. 실행 중 시계열 확장 증거는 Grafana에서 확인합니다.
- Operations Advisor는 rule-based AX 보조 영역입니다. AI API를 호출하지 않고, 예약 / Kafka 적재 / DB 저장 / DLQ 신호를 정해진 규칙으로 해석합니다.
- AI 연동은 향후 별도 Worker나 operator summary 경로로 넣을 수 있습니다. 핵심 event persistence path에는 넣지 않습니다.
- `RESET DEMO DB`는 로컬 데모 이벤트 DB와 `message-ingress-dlq` topic을 초기화합니다. 실제 운영에서 DLQ 이력을 지우는 절차로 설명하지 않습니다.
- 데모 화면의 기능, 레이아웃, 운영 증거, 표시 문구가 바뀌면 `DEMO_UI_VERSION`과 초기 `ver.` 표시를 함께 올립니다. 버전 숫자는 화면 변경이 클러스터에 반영됐는지 확인하는 증거이므로 사소한 UI 변경이라도 누락하지 않습니다. 외형적 변경이 없는 내부 수정은 세 번째 숫자(patch), 사용자가 보는 화면이나 흐름에 변화가 있으면 두 번째 숫자(minor), 시스템이나 서비스 컨셉이 크게 바뀌는 수준이면 첫 번째 숫자(major)를 올립니다. 대부분의 일상 변경은 세 번째 숫자 변경으로 처리합니다.
- 데모 UI 변경 후에는 README, `docs/DEMO_GUIDE.md`, `docs/OPERATIONS.md`, `docs/PATCH_NOTES.md`의 설명을 함께 맞춥니다.

## GitOps / Deployment Rules

- Argo CD는 코드 변경 자체를 배포하지 않습니다. 컨테이너 안에 들어가는 Python code, HTML, static file 변경은 반드시 registry image build/push와 Git manifest의 image tag 변경으로 이어져야 클러스터에 반영됩니다.
- generic v2처럼 schema/consumer/API 순서가 필요한 release는 공통 image tag만 보고 안전하다고 가정하지 않습니다. GitOps Secret `-3` / migration `-2` / Worker `-1` / API `0` wave와 수동 local gate `false` → Worker ready → API gate `true` 경계를 확인합니다.
- `messaging-portfolio:local`은 local kind 또는 수동 bootstrap 전용 이미지입니다. Argo CD 자동 배포 경로에서는 GHCR/ECR 같은 registry image와 commit SHA 기반 tag를 사용합니다.
- GitOps 자동 반영은 `git push -> image build/push -> kustomize image tag commit -> Argo CD sync` 순서로 설명합니다. 이 순서를 생략하고 "push하면 바로 반영된다"고 쓰지 않습니다.
- 브랜치별 배포 역할을 섞지 않습니다.
  - `demo-dev`: 저사양 데모 기능과 문서 변경을 사람이 작업하는 개발 브랜치입니다.
  - `demo-lite`: 2코어 k3s 서버용 축소 데모 배포 브랜치입니다. 현재 GitOps / Actions image tag commit이 섞일 수 있으므로 일반 개발 작업 브랜치로 쓰지 않습니다.
  - `dev-kafka`: 실제 개발/검증용 Argo CD 브랜치입니다.
  - `master`: 최종 병합 및 배포 기준 브랜치입니다.
- 특정 브랜치에서 image tag workflow를 추가하거나 수정할 때는 먼저 해당 브랜치의 Argo CD `targetRevision`, overlay path, 실제 배포 클러스터를 확인합니다.
- `demo-lite`의 설정을 `dev-kafka`나 `master`에 그대로 복사하지 않습니다. 공통 원칙만 옮기고, overlay path와 배포 대상에 맞게 조정합니다.
- 배포 자동화 변경 후에는 `kubectl kustomize <overlay>`로 app workloads가 registry image tag로 렌더링되는지 확인합니다.
- GitHub Actions가 image tag commit을 다시 push하는 브랜치는 원격이 자동으로 앞서갈 수 있습니다. push rejected가 나면 먼저 `git pull --rebase origin <branch>`로 Actions commit을 통합하고 다시 push합니다.
- GHCR package가 private이면 클러스터가 image를 pull하지 못합니다. 데모 서버는 public GHCR package를 기본으로 보고, private registry를 쓰는 경우에는 imagePullSecret을 별도로 문서화합니다.

## Demo UI Operating Rules

- 데모 UI는 운영 증거를 과장하지 않습니다. Kafka append와 DB persistence는 다른 단계이므로 항상 별도 카운터로 표시합니다.
- `예약 건수`는 Kafka append 성공 시 감소합니다. `DB 저장`은 Worker가 PostgreSQL commit까지 완료했을 때 증가합니다.
- 일부 이벤트가 전송 실패하거나 DB 저장 확인이 끝나지 않았으면 결과 상태를 `완료`로 표시하지 않습니다. `일부 미확인` 또는 같은 의미의 상태로 닫습니다.
- Operations Advisor는 readiness, DLQ, 남은 예약, Kafka 적재 수, DB 저장 수의 불일치를 함께 확인해야 합니다. 전송·저장 추적 중에는 `처리 중`, run 종료 뒤 미확인 이벤트가 남아 있으면 `확인 필요`로 표시합니다.
- 운영 링크는 `localhost`를 하드코딩하지 않습니다. 현재 API Base URL 또는 접속 origin을 기준으로 생성합니다.
- batch 전송 로직은 일부 실패 때문에 전체 UI 상태가 무한 `처리 중`에 남지 않도록 종료 상태를 명시적으로 정리합니다.

## Demo Lite Boundary

- `demo-lite`는 저사양 서버에서 API -> Kafka -> Worker -> DB 흐름을 보여주는 profile입니다. HA/failover/성능 baseline 증명으로 설명하지 않습니다.
- `demo-lite` PostgreSQL은 단일 primary 기준입니다. primary 연결 실패는 standby failover를 의미하지 않으며, 단일 primary 복구 대기와 Kafka backlog / Worker retry 관점으로 설명합니다.
- full HA topology와 성능 baseline은 `local-ha` / full-ha 문서와 테스트 결과에서 설명합니다. 기존 DB outage/recovery 결과를 primary promotion/failover 성공 증거로 재표현하지 않습니다.
- demo-lite에서 발견한 운영 경험은 문서화하되, master로 옮길 때는 "일반 운영 원칙"과 "저사양 서버 전용 제약"을 분리합니다.

## Change Scope Rules

- `AGENTS.md`는 모든 주요 브랜치가 공유하는 운영 기준입니다. `master`, `dev-kafka`, `demo-dev`, `demo-lite` 중 한 브랜치에서 AGENTS.md를 바꾸면 같은 변경을 나머지 주요 브랜치에도 cherry-pick해 일관성을 유지합니다.
- README와 `docs/`는 포트폴리오 설명과 운영 기준을 공유하는 문서입니다. 어느 브랜치에서든 문서를 변경했다면 변경 의도, 적용 범위, demo-lite 전용 여부를 확인하고 `master`, `dev-kafka`, `demo-dev`, `demo-lite` 중 관련 브랜치에 cherry-pick 또는 동일 패치로 공유합니다.
- 문서 변경을 다른 브랜치에 공유할 때는 설정 값을 그대로 복사하지 않습니다. 공통 설명은 공통 문서에 반영하고, 브랜치 전용 제약은 `docs/DEMO_LITE.md`처럼 대상 문서에 분리해서 씁니다.
- AGENTS.md에 demo-lite 전용 제약을 추가하더라도 전체 운영 규칙과 demo-lite 전용 경계를 분리해서 씁니다. 특정 브랜치만의 임시 상태를 전체 규칙처럼 쓰지 않습니다.
- 사용자가 "demo-lite에서 한 것처럼"이라고 말해도 설정을 그대로 복사하지 않습니다. 먼저 대상 브랜치 역할, Argo CD targetRevision, overlay path, 실제 배포 클러스터를 확인합니다.
- 저사양 편의 방식, 수동 image import, local-only workaround를 GitOps 자동 배포의 기본 방식처럼 설명하지 않습니다.
- 문서화할 때 demo-lite 전용 현상과 전체 시스템 원칙을 분리합니다. 전체 원칙은 `docs/GITOPS.md`, `docs/OPERATIONS.md`, `docs/ARCHITECTURE.md`로 올리고, demo-lite 제약은 `docs/DEMO_LITE.md`에 둡니다.

## Rollback Rules

- 사용자가 "롤백", "실행취소", "이전 상태", "N번 전"이라고 말하면 새로 비슷하게 재코딩하지 않습니다. 먼저 현재 `git status`, 최근 commit, 작업 diff를 확인하고 어느 변경을 되돌릴지 특정합니다.
- uncommitted 변경은 해당 변경 범위만 되돌립니다. unrelated user change는 건드리지 않습니다.
- committed 변경은 대상 commit이 명확할 때 `git revert` 또는 명시된 baseline으로의 선택적 되돌리기를 우선 검토합니다. `git reset --hard`, 전체 `git checkout -- .`, `git clean` 같은 광범위한 삭제성 명령은 사용자가 명확히 승인한 경우에만 씁니다.
- 사용자가 "4번째 패널 전", "5번 전"처럼 UI 기준을 말하면, 최근 patch notes / commit log / diff에서 그 기준점을 먼저 찾아 설명한 뒤 되돌립니다.
- rollback 요청 중에는 기능 개선을 함께 섞지 않습니다. 요청한 상태로 되돌린 뒤 별도 수정이 필요하면 그 다음 단계에서 처리합니다.
