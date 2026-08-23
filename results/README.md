# Validation Evidence

이 디렉터리는 문서에 인용하는 로컬 검증 원본을 보관합니다. `latest` 파일은 마지막으로 완료된 실행의 원본이며, 새 측정이 기존 안정 기준선을 자동으로 대체한다는 뜻은 아닙니다.

## Tracked Evidence

- `kafka-performance/latest.txt`: Kafka 성능 suite의 마지막 완료 출력
- `kafka-performance/worker-ab-fixed.txt`: 2026-07-21 historical 64-stream Worker fixed `2` arm 전체 출력
- `kafka-performance/worker-ab-keda.txt`: 2026-07-21 historical 64-stream Worker KEDA `2→8` arm 전체 출력
- `kafka-performance/notification-batch-fixed-run*.txt`: 2026-08-10 notification batch candidate의 fixed `2` clean 반복 원본
- `kafka-performance/notification-batch-keda-run*.txt`: 2026-08-10 notification batch candidate의 KEDA `2→4` clean 반복 원본
- `kafka-performance/failed-YYYYMMDD-HHMMSS.txt`: 실패한 suite의 부분 출력. `latest.txt`를 덮어쓰지 않으며 기본 Git 추적 제외
- `ordering-failure/latest.json`: ordering / failure injection suite의 마지막 완료 결과
- `postgres-restore/latest.json`: host logical dump를 disposable database에 복원한 마지막 정합성 검증 원본
- `postgres-recovery/latest.json`: 2026-07-21 PostgreSQL 전체 재시작 뒤 sync 설정, 당시 cache fallback, outage recovery의 마지막 tracked structured summary
- `ops-agent/live-baseline/no-backlog-20260812.json`: 실제 `local-ha`에서 수집한 Phase 1 no-backlog Evidence Bundle
- `ops-agent/live-baseline/no-backlog-20260812.conditions.json`: 위 bundle의 deterministic Phase 2 condition evaluation
- `ops-agent/raw/ac4d45ac-6280-4eb7-9d35-ea735243f00b/`: 위 bundle이 참조하는 named Application·Prometheus·Kubernetes·Argo CD redacted projection 4개
- `ops-agent/calibration/20260816T032411Z/`: Phase 2.5 multi-stream backlog manifest, analysis, sanitized run summary 3개
- `ops-agent/negative-control/20260816T040746Z/`: frozen pressure candidate negative-control manifest, analysis, sanitized summary 3개
- `ops-agent/sequence-validation/20260816T044352Z/summary.json`: actual positive/negative bundle sequence의 v2 replay ID, activation window, local output hash 요약
- `ops-agent/diagnosis/golden-eval-v1.json`: scripted offline Phase 3 grounding/tool/stop evaluation 요약
- `ops-agent/recovery-calibration/20260816T100600Z/`: Phase 4 A/B/C/E/F load-aware calibration manifest, analysis, compact summaries
- `ops-agent/recovered-calibration/20260816T194023Z/`: Phase 4.2 E-04~06 supplemental manifest, combined E-01~06 analysis, compact summaries
- `ops-agent/incident-e2e/`와 `ops-agent/incidents/`: Phase 5 workload bundle/raw, diagnosis, recovery, canonical incident; 현재 local-only, Git 추적 제외
- 그 밖의 날짜별·중간 산출물: 로컬 보관, 기본 Git 추적 제외

fixed Worker/KEDA A/B처럼 두 원본을 함께 보존해야 하는 실행은 `run_kafka_performance_suite.ps1 -ResultFileName <name>.txt`를 사용합니다. 각 파일에 `k6_stream_count`, `worker_scaling_mode`, `fixed_worker_replicas`, source revision과 dirty 여부를 남기고, 조건이 다른 파일을 하나의 baseline으로 합치지 않습니다.

PostgreSQL restore 원본은 dump 파일 자체를 Git에 넣지 않습니다. `latest.json`에 dump size/hash, 검증 script hash, source/restore 비교값과 한계를 남깁니다.

PostgreSQL recovery JSON은 실행 시각, source/script hash, 관측값과 한계를 구조화한 추적 요약입니다. 전체 raw terminal transcript는 보관하지 않았으므로 JSON 자체를 원시 출력으로 해석하지 않습니다.

## Ops Agent No-backlog Live Capture - 2026-08-12

- profile/context: `local-ha` / `kind-messaging-ha`
- topic/group: `message-ingress` / `message-worker`
- Kafka: end/committed/lag partition `8/8`, lag 전부 `0`, missing·extra·`-1`·offset decrease 없음
- Worker: desired/current/ready/available `2/2/2/2`, Pod coverage `2/2`, KEDA Ready `true`
- PostgreSQL: primary reachable, standby/sync standby `2/2`, replication delay `0` bytes
- Argo CD: `Synced / Healthy`, revision `004f2e7791543de2d570c287cf8938410c61807c`
- collection: `PARTIAL`, 114 evidence; label-on-use Worker metric 2개 `MISSING/UNKNOWN`
- provenance: collector worktree HEAD `004f2e7`, dirty state, collector tree SHA-256 기록, raw projection 4개 hash 일치
- 판정: stable release·성능 baseline 제외, captured no-backlog operations reference
- Phase 2: source collection `PARTIAL`, condition evaluation `COMPLETE`; 네 condition `ABSENT`, no-backlog assessment `PRESENT`

`PARTIAL`은 runtime degraded 의미가 아닙니다. 두 Worker series는 재시작 뒤 해당 60초 window에 새 처리 event가 없어 생성되지 않았고, Kafka backlog required evidence는 complete/fresh입니다. 상세 해석은 [Ops Agent 문서](../docs/OPS_AGENT.md)와 [capture guide](ops-agent/live-baseline/README.md)를 사용합니다.

## Ops Agent Worker Backlog Calibration - 2026-08-16

- profile/context: `local-ha` / `kind-messaging-ha`
- workload: 64 streams, 100 VU, 30초, 3 runs, k6 error `0.00%`
- sampling: 약 15초 간격 `ops.evidence.v1` 71개; source projection 284개
- bundle status: `COMPLETE` 63, `PARTIAL` 8; required Kafka evidence는 71개 모두 `OK/FRESH`
- scaling: 기존 KEDA min/max `2/4`, polling `5s`, cooldown `120s`; 수동 scale/patch 없음
- pressure: peak lag `17,537` / `25,256` / `24,096`; Worker desired/available `4/4` 도달
- drain: lag `0` 복귀 `196.781s` / `256.575s` / `256.543s`
- baseline return: Worker `2/2` 복귀 `316.779s` / `346.609s` / `361.580s`
- PostgreSQL: 전 구간 ready/HA, standby/sync standby `2/2`; max replication delay `10,696` bytes
- candidate only: lag `>=7,000`, 60초 slope `>=100/s`, produce>commit, 세 capture 지속과 두 번의 lag 증가

`PARTIAL` 8개 중 하나는 baseline stage series `MISSING`, 일곱 개는 scale-out 직후 이전/현재 Worker Pod label coverage 차이로 stage freshness가 `UNKNOWN`인 capture입니다. 결측과 freshness 불확실은 0으로 바꾸지 않습니다. 71개 bundle과 raw projection은 약 88 MB이고 runtime topology를 포함해 local-only로 보존합니다. Git은 sanitized [analysis](ops-agent/calibration/20260816T032411Z/analysis.md), manifest, 세 run summary만 추적합니다. V1의 single-bundle positive lag는 `UNKNOWN`이며 v2 ordered sequence replay에서는 세 positive run 모두 `PRESENT`입니다.

## CORE_BACKLOG_PRESSURE Negative Controls - 2026-08-16

- frozen candidate: lag `>=7,000`, 60초 slope `>=100/s`, 세 capture와 두 번의 lag 증가
- growth vote: slope 하나; `produce-committed`는 산술 일치 검증
- short burst: peak lag `3,997`, candidate `NOT_PRESENT`
- sustainable high: 180초·약 `123.6 events/s`, peak lag `3,111`, candidate `NOT_PRESENT`
- single transient: peak lag `8,854`, candidate sample 2개, rising window 0, `NOT_PRESENT`
- evidence: 51 bundles, Kafka required evidence 모두 `OK/FRESH`, raw projection 204개 hash 일치
- final state: 모든 control lag `0`, KEDA inactive, Worker `2/2`; scaling contract unchanged

Runtime bundle/raw 약 62 MB는 local-only로 유지합니다. Git은 sanitized [analysis](ops-agent/negative-control/20260816T040746Z/analysis.md), manifest, 세 summary만 추적합니다. `local-ha.conditions.v2` actual replay에서 세 control 모두 activation window가 없었고 `PRESENT`가 발생하지 않았습니다. V1 evaluator와 Phase 3는 변경하지 않았습니다.

## Sequence Evaluator Replay - 2026-08-16

- schema/policy: `ops.conditions.v2` / `local-ha.conditions.v2`
- positive: run 1/2/3 모두 `CORE_BACKLOG_PRESSURE=PRESENT`, matched capture indexes `[1,2,3]`
- negative: short burst, sustainable high, single transient spike 모두 `PRESENT` 없음
- provenance: ordered canonical bundle digest, collection/Kafka source timing, policy/evaluator/ruleset을 evaluation ID에 결합
- local-only: 여섯 full condition output과 원본 bundle/raw
- tracked: [sanitized replay summary](ops-agent/sequence-validation/20260816T044352Z/summary.json)

이 replay는 activation만 검증합니다. Recovery/clearing hysteresis는 구현하지 않았습니다. Phase 3 diagnosis는 이 결과를 입력으로 사용하는 별도 단계입니다.

## Ops Agent Phase 4 Recovery Calibration - 2026-08-16

- tracked root: `ops-agent/recovery-calibration/20260816T100600Z/`
- tracked files: manifest, analysis JSON/Markdown, A/B/C/E/F compact summaries 7개
- local-only: 349 normalized bundles, 1,396 raw projections, conditions outputs, k6 logs, interrupted attempts
- hash validation: bundle canonical digest `349/349`, raw SHA-256 `1,396/1,396`, errors `0`
- workload: k6 `constant-arrival-rate`, 64 streams, rates `0/30/75/110/330 records/s`
- actual runs: A/B/C 1회, E continuous-ingress 3회, F zero-ingress 1회
- pressure: E peak lag `20,806 / 21,834 / 21,151`, F `20,998`; 모두 v2 activation `PRESENT`
- boundary at capture time: recovery policy candidate only; 이후 Phase 4.1에서 immutable artifact replay용 `ops.recovery.v1` ACTIVE/RECOVERING/UNKNOWN을 구현했으며 원본 calibration artifact는 변경하지 않음

Kafka exporter negative-lag capture는 원본과 compact quality exclusion 목록에 남깁니다.
값을 `0`으로 치환하거나 healthy capture로 재분류하지 않습니다. rolling activation
artifact는 valid 3-bundle window의 과거 incident fact이고, full-sequence `UNKNOWN`과
후속 anomaly는 별도 보존합니다. tracked summary를 raw evidence 대체물로 해석하지
않습니다. Phase 4.1 full recovery evaluation outputs는
`results/ops-agent/recovery-evaluation/` 아래 local-only artifact이며, 기존
calibration bundle/raw 또는 compact summary를 덮어쓰지 않습니다.

## Ops Agent Phase 4.2 Recovered Calibration - 2026-08-17

- tracked root: `ops-agent/recovered-calibration/20260816T194023Z/`
- tracked files: manifest, combined analysis JSON/Markdown, E-04/E-05/E-06 compact summaries
- local-only: 219 new bundles, 876 raw projections, k6 logs, full recovery replay outputs
- combined audit: E-01~06 438 bundles와 1,752 raw projections canonical/SHA-256 `PASS`
- workload: 기존 E와 동일한 64 streams, `75→330→75 records/s`, current KEDA `2→4`, 약 15초 cadence
- new runs: peak lag `20,261 / 22,632 / 18,948`, failed/dropped iteration `0/0`
- stable MEDIUM re-entry count: E-01~06 `3/1/3/4/5/14`
- promoted policy: `worker-backlog-local-ha.recovery.v2`, consecutive usable capture `3`
- actual replay: E-01/E-03/E-04/E-05/E-06 `RECOVERED`, E-02 `UNKNOWN`

RECOVERED는 activation 뒤 RECOVERING을 관측한 동일 incident scope에서 measured
MEDIUM ingress `74.9833~77.0833/s`, lag `<=22`, slope `<=0`, fresh/usable Kafka
evidence와 PostgreSQL ready/HA/primary가 세 capture 연속 유지된 경우만 뜻합니다.
Lag `0`, Worker `2`, KEDA inactive, zero ingress는 요구하지 않습니다. Unknown이나
quality anomaly는 count를 reset합니다. Full bundle/raw/replay는 runtime topology를
포함하므로 local-only이며 tracked compact summary를 raw evidence 대체물로
해석하지 않습니다. Clearing, 새 incident 분리, global health, remediation은 이
artifact가 증명하지 않습니다.

## Ops Agent Phase 5 Verified Incident - 2026-08-23

- local source run: `ops-agent/incident-e2e/20260823T152359Z/`
- canonical local incident: `ops-agent/incidents/inc-88a1eeaa17897f6a8a929bba/`
- workload: 64 streams, `75→330→75 records/s`, accepted `6,750 / 29,697 / 135,000`, failed/dropped `0/0`
- detection: `CORE_BACKLOG_PRESSURE=PRESENT`, lag `7,205→10,497→13,936`, slope `120.067→174.467→230.767/s`
- diagnosis: `gpt-5.6-luna`, normalized tool 4개, `WORKER_PATH_PRESSURE_SUSPECTED=SUPPORTED`
- recovery/lifecycle: ACTIVE → RECOVERING → RECOVERED → CLOSED, detection-to-closure `809.557s`
- validation: normalized bundle `133/133`, raw projection `532/532`, errors `0`
- later observation: closed history는 유지하고 `WORKER_BACKLOG_ACTIVE`를 `current_observation`에 분리

위 두 directory는 runtime topology, model result, local path provenance를 포함해
기본 Git 추적 대상이 아닙니다. 현재 repository에는 sanitized public replay artifact가
없으며 public demo-lite에도 Phase 5 replay route가 없습니다. `summary.json` 수치만으로
raw evidence 또는 production SLA를 대체하지 않습니다. 실패한 `20260816T214911Z`
identity mismatch와 `20260816T223837Z` dropped-iteration run도 local history로 보존하고
성공 incident로 재분류하지 않습니다.

## Phase 3 Diagnosis Evaluation - 2026-08-16

- schema: `ops.diagnosis.v1`
- entry gate: `ops.conditions.v2` / `CORE_BACKLOG_PRESSURE=PRESENT`
- normalized read-only tools: `9`
- golden fixtures: `9`, scripted offline model, live API call 없음
- output-repair fixtures: `5`, tool-free one-turn repair budget 검증
- offline result: schema/citation/tool selection/abstention/budget/stop compliance `1.0`
- invalid result counts: fabricated citation `0`, unnecessary tool `0`, forbidden tool `0`
- live run: positive run-01 + `gpt-5.6-luna`, 4 tool calls + output repair 1회, VALID
- live stop/usage: `insufficient_evidence`, API turns `6`, total tokens `44,264`
- live artifact: local-only `ops-agent/diagnosis/20260816-positive-run-01-luna.json`

Golden summary는 Agent harness regression이며 live model 정확도 주장이 아닙니다.
Live Diagnosis Run은 runtime topology를 포함하므로 이 디렉터리에 local-only로
보존하고 Git에서 무시합니다. 최초 invalid output은 completed artifact로 쓰지
않았고 tool 없는 repair 결과가 validator를 통과한 뒤에만 저장했습니다.

## Latest Completed Kafka Performance Suite — 2026-08-10

- 실행 범위: `2026-08-10T11:46:27~12:32:34+09:00`, `dev-kafka` HEAD `e378164`, dirty worktree
- image: API·core Worker·notification Worker 모두 `messaging-portfolio:notification-batch`, API `2.1.0`
- 조건: generic v2, 100 VU / 30s, 64 streams, core KEDA `2→4`, notification KEDA `1→2`
- 초기화: 각 실행 전 local DB event state와 active Kafka topic 재생성, deletion quiet period `75s`, 시작 message/notification lag `0`
- fixed `2` 3회 평균: event `30,289.67`, avg `48.25ms`, p95 `88.53ms`, p99 `131.30ms`, peak message lag `27,013.33`, drain `222.49s`, backlog 처리율 `121.42 events/s`
- KEDA `2→4` 3회 평균: event `30,351.33`, avg `47.82ms`, p95 `94.28ms`, p99 `135.11ms`, peak message lag `26,714`, drain `194.05s`, backlog 처리율 `137.67 events/s`
- KEDA 판정: fixed 대비 backlog 처리율 `13.38%` 증가, drain `12.78%` 감소. KEDA drain `190.71~195.75s`, fixed drain `215.81~230.86s`
- trade-off: KEDA p95 평균 `6.49%`, p99 평균 `2.90%` 증가. API latency 개선 근거에서 제외
- 공통 결과: 오류 `0.00%`, same-stream ordering `100/100`, main·notification final lag `0/0`; KEDA 세 실행 모두 core Worker `4` 도달
- 마지막 실행: event `30,307`, p95 `95.42ms`, peak message/notification lag `26,726`/`16`, drain `195.75s`
- 판정: local dirty image의 current A/B candidate. 반복 일관성을 확인했지만 CI publication 전이므로 stable release baseline 승격 제외
- 원본 범위: `latest.txt`는 마지막 KEDA 실행 전체 출력. fixed/KEDA 각 3회 원본을 함께 추적

2026-08-05 단순화 source의 hot single-stream 3회 평균 event `33,201`, p95 `76.57ms`, main drain `364.62s`는 historical candidate로 유지합니다. stream 분포가 달라 현재 64-stream A/B와 직접 비교하지 않습니다.

## Multi-stream Worker A/B Candidate — 2026-07-21

- 공통 조건: dirty source image `perf-v17`, API `6`, 100 VU / 30s, 64 streams, clean DB/topic, 시작 lag `0`
- fixed `2`: event `22,125`, p95 `169.24ms`, peak message/notification lag `21,170`/`45`, all-pipeline drain `301.42s`
- KEDA `2→8`: event `20,499`, p95 `212.60ms`, peak message/notification lag `18,950`/`11,536`, all-pipeline drain `261.17s`, final Worker `8`
- 판정: drain `13.35%` 감소와 notification backlog 이동 확인. KEDA arm intake 악화와 단일 실행 조건 때문에 stable baseline 미채택

## Latest PostgreSQL Restore Drill — 2026-07-21

- 실행: `2026-07-21T04:41:27+09:00`, `dev-kafka` base revision `1439be1`, 검증 script SHA256 기록
- 원본: host workspace의 Git-ignored logical dump `39,433,414` bytes, SHA256 `26019e88...93c4`
- 복원 대상: 같은 local PostgreSQL HA cluster의 disposable database
- 결과: Alembic `0008`, 10개 table count, generic v2 row `33,840`, max message id `33,840`, max stream sequence `25,378` 원본 일치
- 한계: 전체 host/cluster loss, object storage 사본, 정기 integrity check, 자동 RPO/RTO 측정 미검증

## Latest PostgreSQL Restart Recovery Drill — 2026-07-21

- 실행: `2026-07-21T05:01:59+09:00`, `dev-kafka` source revision `fde9a24`, 관련 script SHA256 기록
- sync recovery: 모든 StatefulSet ordinal에 `synchronous_commit=on`, `ANY 1` 지속 적용; primary `postgresql-0`, sync/quorum standby `2`
- cache fallback: `45.390s`, fresh cache age `0.112s`, DB-down degraded cache age `11.462s`, scale `3→0→3` 뒤 ready
- DB outage: `43.008s`, DB down 중 event 수락과 복구 뒤 persistence 확인
- 최종 상태: PostgreSQL `3/3`, Pgpool `2/2`, API ready, message/notification lag `0`, backup PVC `Bound`
- 한계: primary promotion과 node/AZ 장애 미검증; 상세값은 `postgres-recovery/latest.json`

## Interpretation Rules

- 2026-06 성능 출력의 event 응답 `200`: HTTP `202 Accepted` 계약을 코드에 명시하기 전 수집한 역사적 증거
- 2026-06 request shape는 legacy/order contract이며 generic v2 route·serialization·validation 성능 증거가 아님
- 현재 계약 검증: 2026-07-21 새 빌드에서 OpenAPI `2.0.0`과 event `202` 재확인
- current source 계약 검증: 2026-08-05 local image에서 OpenAPI/API `2.1.0`, event `202`, PostgreSQL read model 재확인
- 2026-06 `accepted-to-persisted`: API 수락 시각과 PostgreSQL row의 `created_at` 또는 row 조회 가능 시점을 비교한 historical proxy
- current PowerShell `accepted_to_status_observed_ms`: client가 `persisted` status를 본 시각까지, polling/network 포함
- current Worker histogram: `commit()` 반환 직후 `persisted_at`까지; 2026-07-21 query `60s`는 최대 finite bucket 경계 포화로 exact p95 해석 제외
- DB commit timestamp: tracked 2026-06 원본에서 직접 측정하지 않음
- DLQ API sample: append-only Kafka DLQ log의 최근 표본
- `oldest_sample_age_seconds`: 조회 표본의 가장 오래된 record age
- unresolved DLQ depth / current incident backlog: 별도 상태 모델 없이는 이 파일과 DLQ sample로 산출 불가
- Worker KEDA 효과: consumer lag peak, Worker commit-observed latency, backlog drain time으로 관찰. 2026-06 비교에서는 historical row-visible proxy만 사용 가능
- fixed replica 대 KEDA 직접 비교: 2026-08-10 동일 candidate·clean 조건 각 3회에서 KEDA drain 범위가 fixed보다 짧음. API p95 증가는 별도 trade-off로 유지

## Update Checklist

1. 실행 시각, commit, branch, cluster profile, workload 조건 기록
2. 원본 `latest` 파일 갱신
3. `docs/TEST_RESULTS.md`에 최신 결과와 안정 기준선 채택 여부 분리 기록
4. HTTP status, proxy 정의, 결측값과 제한 사항 명시
5. Redis queue-first 결과와 Kafka event-stream 결과 분리
6. Ops Agent capture는 bundle schema, source/dirty state, collector tree hash, raw refs/hash, freshness/coverage, synthetic/live 분류 기록
