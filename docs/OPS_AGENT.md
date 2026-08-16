# Read-only Operations Evidence Agent

`ops_agent`는 Worker backlog 시나리오의 운영 신호를 `ops.evidence.v1` Evidence Bundle로 정규화합니다. 고정된 단일 bundle은 `ops.conditions.v1`, ordered bundle sequence는 calibrated `ops.conditions.v2` 결과로 평가합니다. `CORE_BACKLOG_PRESSURE=PRESENT` 이후에는 단일 Evidence-grounded Diagnosis Agent가 고정 read-only tool을 선택하고 `ops.diagnosis.v1`을 생성합니다. 복구 판정과 실행은 구현하지 않습니다.

## Phase 경계

| Phase | 책임 | 현재 상태 |
| --- | --- | --- |
| Phase 1: Evidence Collection | Application, Prometheus, Kubernetes, Argo CD의 read-only 수집, freshness·coverage·provenance 보존 | 완료 |
| Phase 2: Deterministic Condition Evaluation | versioned rule로 condition의 `PRESENT` / `ABSENT` / `UNKNOWN` 판정 | v1 single-bundle baseline과 v2 sequence activation 구현 완료 |
| Phase 2.5/2.6: Controlled Calibration | 실제 multi-stream backlog와 negative control로 positive rule 보정 | positive 3회와 negative control 3종 완료; v2 replay 통과 |
| Phase 3: Evidence-guided Investigation | 확정된 backlog에 필요한 추가 read-only 조사와 grounded hypothesis | 구현 완료; positive run-01 live VALID와 bounded output repair 검증 |

Phase 2는 LLM이나 runtime source를 호출하지 않습니다. Phase 3도 Phase 2의 판정을 덮어쓰지 않으며, 증거가 부족한 원인을 확정하지 않습니다. write, restart, scale, rollout, reset, restore는 세 Phase의 권한에 포함하지 않습니다.

## Phase 1 계약

수집 흐름:

```text
versioned local-ha policy
  -> fixed read-only collectors
  -> source-specific safe projection and redaction
  -> normalized EvidenceItem[]
  -> ops.evidence.v1 Evidence Bundle
```

| Source | Read-only 경로 | 주요 evidence |
| --- | --- | --- |
| Application | `GET /health/ready`, `GET /ops/summary` | Kafka reachability, PostgreSQL HA, Worker summary |
| Prometheus | 고정된 query/query_range 목록 | partition end offset, committed offset, lag, Worker counter·histogram |
| Kubernetes | 고정된 `kubectl get` | Worker Deployment, Pod runtime image/imageID, KEDA ScaledObject |
| Argo CD | Application CR `get` | sync, health, revision, `reconciledAt` |

외부에서 임의 PromQL이나 임의 kubectl command를 입력받지 않습니다. HTTP redirect와 ambient proxy를 사용하지 않으며 Windows kubectl override는 `.exe`만 허용합니다. 현재 host kubeconfig가 최소 권한 credential이라는 주장은 하지 않습니다.

### 상태 의미

Evidence status:

- `OK`: source observation과 정규화 성공
- `MISSING`: 유효한 source 응답에서 요청한 series 또는 resource 부재
- `ERROR`: source 접근, parsing, command 실행 실패
- `NOT_APPLICABLE`: policy에서 명시적으로 적용 제외
- `UNAVAILABLE`: 현재 instrumentation으로 관측 불가능

Freshness status:

- `FRESH`: source별 정책 age 이내
- `STALE`: source timestamp가 정책 age 초과
- `UNKNOWN`: source timestamp 또는 필요한 freshness evidence 부재
- `NOT_APPLICABLE`: 적용 제외 evidence

Bundle collection status:

- `COMPLETE`: applicable collector가 모두 `OK`, partial source와 raw artifact 오류 없음
- `PARTIAL`: 하나 이상의 실제 observation은 성공했으나 일부 source 또는 series가 불완전
- `FAILED`: 실제로 성공한 observation 없음

Bundle status는 수집 완전성입니다. 시스템 health 판정이나 모든 condition의 공통 gate가 아닙니다. `PARTIAL` bundle에서도 특정 condition의 required evidence가 완전하고 fresh하면 해당 condition을 판정할 수 있습니다.

### 값 보존 규칙

- missing, `0`, Kafka offset `-1` 구분
- partition별 raw value와 coverage 보존
- offset decrease와 counter reset flag 보존
- Prometheus query evaluation time과 실제 sample timestamp 분리
- `/ops/summary`의 15초 upstream cache bound를 freshness age에 포함
- Argo CD freshness는 Application `reconciledAt` 기준
- runtime desired image와 Pod `imageID` 분리
- source `HEAD`, dirty state, collector tree SHA-256, raw artifact SHA-256 기록

## 2026-08-12 local-ha Live Validation

실행 명령:

```powershell
.venv\Scripts\python.exe -m ops_agent collect `
  --profile local-ha `
  --incident-id phase1-live-validation `
  --context kind-messaging-ha `
  --output results/ops-agent/evidence-live.json
```

Scope:

- context: `kind-messaging-ha`
- namespace: `messaging-app`
- topic: `message-ingress`
- consumer group: `message-worker`
- GitOps target revision: `dev-kafka`

첫 preflight의 네 source 동시 실패 원인은 Docker Desktop Linux backend 중단이었습니다. 기존 kind control-plane을 재개했으며 endpoint/resource mapping은 runtime과 일치했습니다. Live 검증 중 Prometheus actual sample timestamp 보존과 Argo `reconciledAt` freshness 기준을 수정했고, Argo reconciliation max age를 `300s`로 명시한 뒤 다시 수집했습니다. mock, 정상값 fallback, cluster 재생성은 사용하지 않았습니다.

### 정상 기준점

| Evidence | Actual value | Freshness | Coverage/status |
| --- | --- | --- | --- |
| `/health/ready` | HTTP `200`, `ready`, Kafka reachable | `FRESH`, age `1.519s` | `OK` |
| PostgreSQL HA | primary reachable, standby `2`, sync standby `2`, replication delay `0` bytes | `FRESH`, age `1.519s` | `OK` |
| `/ops/summary` | Worker desired/available/HPA desired/max `2/2/2/4` | `FRESH`, effective age `16.519s` | `OK` |
| Kafka end offset | 실제 partition series | `FRESH`, age `2.739s` | `8/8` |
| Kafka committed offset | `message-worker` partition series | `FRESH`, age `2.739s` | `8/8` |
| Kafka lag | 모든 partition `0` | `FRESH`, age `2.739s` | `8/8` |
| Worker Deployment | desired/current/ready/available `2/2/2/2` | `FRESH`, age `1.108s` | `OK` |
| Worker Pods | `2` Running/Ready, desired image와 runtime digest 일치 | `FRESH`, age `1.108s` | `2/2` |
| KEDA `worker-keda` | Ready `true`, Active `false`, min/max `2/4` | `FRESH`, age `1.108s` | `OK` |
| Argo CD | `Synced`, `Healthy`, revision `004f2e7791543de2d570c287cf8938410c61807c` | `FRESH`, reconciled age `127.626s` | `OK` |

Kafka source timestamp는 `2026-08-12T00:39:06.887Z`입니다.

| Partition | End offset | Committed offset | Lag |
| ---: | ---: | ---: | ---: |
| `0` | `4,590` | `4,590` | `0` |
| `1` | `15,348` | `15,348` | `0` |
| `2` | `4,601` | `4,601` | `0` |
| `3` | `4,113` | `4,113` | `0` |
| `4` | `2,749` | `2,749` | `0` |
| `5` | `2,391` | `2,391` | `0` |
| `6` | `4,605` | `4,605` | `0` |
| `7` | `2,293` | `2,293` | `0` |

Partition missing/extra, `-1`, offset decrease, 세 Kafka query 사이 coverage mismatch는 모두 없습니다.

세 offset 합계는 end/committed 모두 `40,690`이지만 PostgreSQL row count가 아닙니다. committed offset은 success뿐 아니라 rejected/DLQ 같은 terminal outcome도 전진시킬 수 있습니다. 이 capture가 증명하는 범위는 60초 idle window에서 `message-worker`가 topic end를 따라잡았다는 사실입니다.

### `PARTIAL` 판정

최종 bundle은 `114` evidence와 collection status `PARTIAL`을 기록했습니다.

- Application `OK` 4건
- Prometheus `OK` 100건, `MISSING` 2건
- Kubernetes `OK` 3건
- Argo CD `OK` 1건
- 현재 계측으로 제공하지 않는 instrumentation `UNAVAILABLE` 4건

`MISSING`인 series:

- `messaging_worker_processed_total`
- `messaging_worker_stage_latency_seconds{stage="db_persist"}`

두 metric은 Worker가 실제 record를 처리할 때 label child가 생성됩니다. Docker Desktop 재개 뒤 현재 Worker process가 최근 60초 동안 새 record를 처리하지 않아 series가 없었습니다. backlog 판단에 필요한 Kafka 3종과 partition coverage는 완전하므로 이 누락을 `0`으로 바꾸지 않고 `MISSING/UNKNOWN`으로 유지했습니다.

`messaging_queue_wait_seconds`와 `messaging_event_persist_lag_seconds` histogram은 scrape 기준 `FRESH`이지만 두 Worker instance의 `_count`가 모두 `0`입니다. 이는 latency가 `0`이라는 뜻이 아니라 해당 process에서 관측 표본이 없다는 뜻입니다.

현재 직접 계측하지 않는 exact PostgreSQL insert rate, isolated transaction commit latency, consumer rebalance event, CPU throttling은 `UNAVAILABLE`입니다. 이 네 catalogued limitation은 collection `PARTIAL`의 원인이 아닙니다.

### 보존된 capture

- normalized bundle: [no-backlog-20260812.json](../results/ops-agent/live-baseline/no-backlog-20260812.json)
- capture guide: [results/ops-agent/live-baseline/README.md](../results/ops-agent/live-baseline/README.md)
- raw source projections: bundle ID `ac4d45ac-6280-4eb7-9d35-ea735243f00b` 아래 Application, Prometheus, Kubernetes, Argo CD JSON
- validation: Pydantic `ops.evidence.v1`, 네 raw SHA-256 일치, full suite `420 passed`

이 capture는 dirty Phase 1 worktree에서 수집됐으며 collector tree SHA-256 `5c7055011d474ba94ef15f9a21e49b09062e4f96bda0aa79c8ce426f9609c72f`를 기록합니다. stable release 또는 성능 baseline이 아니라 no-backlog 운영 reference입니다.

Bundle의 `raw_ref`는 repository root 기준입니다. 이후 capture는 dated output과 전용 artifact directory를 함께 지정해 immutable path를 먼저 확정하는 방식을 사용합니다.

## Fixture 분리

| Kind | Location | Purpose |
| --- | --- | --- |
| Synthetic fixture | `ops_agent/fixtures/` | collector/controller unit test와 missing, `-1`, partial coverage, stale/error 분기 검증 |
| Captured live evidence | `results/ops-agent/live-baseline/` | 실제 local-ha 정상 기준과 evaluator regression/reference |

Synthetic fixture를 live 검증 증거로 사용하지 않습니다. Captured live evidence는 새 실행으로 덮어쓰지 않고 baseline, incident, recovery 이름을 분리합니다.

## Phase 2 판정 계약

Phase 2 입력은 immutable `ops.evidence.v1` bundle입니다. evaluator는 source에 다시 연결하거나 freshness를 현재 시각으로 재계산하지 않습니다. 출력 `ops.conditions.v1`은 condition별 상태, 사용한 evidence ID, 판정 이유, 누락 또는 stale required evidence를 구조화합니다.

```powershell
.venv\Scripts\python.exe -m ops_agent evaluate `
  --input results/ops-agent/live-baseline/no-backlog-20260812.json `
  --output results/ops-agent/live-baseline/no-backlog-20260812.conditions.json
```

`evaluation_status=COMPLETE`는 모든 condition과 assessment가 `PRESENT` 또는 `ABSENT`로 결정됐다는 뜻입니다. 하나라도 `UNKNOWN`이면 `PARTIAL`입니다. source bundle의 collection status는 별도 필드로 보존하며, evaluation status는 runtime health나 원인 확정 상태가 아닙니다.

Condition 상태:

- `PRESENT`: versioned rule의 직접 조건 충족
- `ABSENT`: required evidence가 완전하고 fresh하며 직접 조건 불충족
- `UNKNOWN`: required evidence의 missing, error, stale/unknown freshness, coverage 불완전, semantic anomaly로 판정 불가

Optional evidence의 부재만으로 condition을 `UNKNOWN`으로 만들지 않습니다. Optional evidence는 설명의 정밀도와 후속 condition 판정에만 사용합니다.

### 첫 condition dependency

| Condition | Required evidence | Optional evidence | no-backlog capture 실제값 |
| --- | --- | --- | --- |
| `CORE_BACKLOG_PRESSURE` | end/committed/lag exact selector, 8/8 coverage, aligned 60초 sample grid, 전체 시점 산술 일치 | terminal throughput, queue wait, Worker stage latency, replica | `ABSENT` |
| `PARTITION_LAG_CONCENTRATION_OBSERVED` | partition별 lag exact selector, 8/8 coverage, aligned 60초 sample grid | partition별 offset slope, Worker throughput | `ABSENT` |
| `DB_DEGRADED` | 같은 `/health/ready` 응답의 parent status/reason과 PostgreSQL component | persistence lag, DB stage latency | `ABSENT` |
| `WORKER_REPLICA_UNAVAILABLE` | current Deployment generation과 desired/current/available | `/ops/summary`, Pod readiness/restarts, KEDA condition | `ABSENT` |

`CORE_BACKLOG_PRESSURE`는 complete/fresh한 세 Kafka series의 source/tool/semantic/unit/window/labels를 고정하고, 같은 raw projection과 exporter identity, source timestamp를 요구합니다. 13개 5초 sample 전체에서 partition별 `lag == end - committed`를 확인하고 range end와 collector/source timestamp가 실제 scrape 간격 안에서 일치하는지도 검증합니다. 60초 전 구간 lag `0`만 `ABSENT`로 확정합니다. 양수 lag는 backlog 관측 사실만 남기며 pressure floor·slope·sustain policy가 아직 calibration되지 않아 `UNKNOWN`입니다. partition 누락, committed offset `-1`, grid/산술·시간축 불일치도 `UNKNOWN`입니다.

`PARTITION_LAG_CONCENTRATION_OBSERVED`도 전 구간 lag `0`일 때만 `ABSENT`입니다. 양수 lag의 concentration은 absolute floor·share·sustain 정책이 없으므로 `UNKNOWN`입니다. `DB_DEGRADED`는 실제 `/health/ready.reason`의 네 PostgreSQL reason을 직접 판정 근거로 사용합니다. versioned `local-ha.conditions.v1`은 배포 계약의 HA mode, ready standby `2`, sync standby `1`, replication delay `1 MiB` guardrail과 component/reason이 충돌하면 `ABSENT` 대신 `UNKNOWN`을 반환합니다. Worker replica shortfall 한 snapshot은 2분 unavailable grace를 증명하지 못하므로 `UNKNOWN`이며, fixed Worker resource identity와 generation이 관측된 full availability만 `ABSENT`입니다.

Derived assessment의 첫 기대값:

```yaml
conditions:
  CORE_BACKLOG_PRESSURE: ABSENT
  PARTITION_LAG_CONCENTRATION_OBSERVED: ABSENT
  DB_DEGRADED: ABSENT
  WORKER_REPLICA_UNAVAILABLE: ABSENT
assessment:
  NO_BACKLOG_PRESSURE_DETECTED: PRESENT
```

이 결과는 [no-backlog-20260812.conditions.json](../results/ops-agent/live-baseline/no-backlog-20260812.conditions.json)에 보존했습니다. source collection은 `PARTIAL`이지만 evaluation은 `COMPLETE`입니다. `WORKER_BACKLOG_RECOVERING`과 `WORKER_BACKLOG_RECOVERED`에 필요한 slope window와 recovery floor는 아직 구현하지 않았습니다.

### 구현 범위와 검증

- condition별 required/optional evidence registry
- `PRESENT` / `ABSENT` / `UNKNOWN`의 deterministic rule과 reason code
- evidence ID와 freshness/coverage를 포함한 판정 trace
- synthetic fixture 기반 missing, `-1`, partial coverage, stale, offset decrease test
- captured no-backlog live evidence의 기대 condition regression
- canonical source bundle SHA-256, evaluator/ruleset version, condition payload를 결합한 deterministic evaluation ID와 atomic CLI JSON 출력
- 새 collection의 effective Application/Prometheus endpoint와 Host routing을 credential-free identity evidence로 기록
- positive lag pressure/concentration과 replica shortfall은 calibrated sustain/grace evidence 전까지 `UNKNOWN`
- LLM call, 원인 추정, remediation 실행 제외
- Phase 2 checkpoint local validation: Ops Agent `142 passed`, full suite `499 passed`

## Phase 2.5 Controlled Worker Backlog Calibration - 2026-08-16

GitOps `local-ha`에서 64 streams, 100 VU, 30초 부하를 세 번 실행했습니다. KEDA와 Worker replica를 수동 변경하지 않았고, 시작 lag `0`과 Worker `2/2`를 확인한 뒤 약 15초 간격으로 총 71개 `ops.evidence.v1` bundle을 수집했습니다. 세 실행 모두 lag 증가, KEDA `2→4`, drain, lag `0`, Worker `2/2` 복귀까지 `COMPLETE`였습니다.

개별 bundle은 `COMPLETE` 63개, `PARTIAL` 8개입니다. 첫 baseline의 label-on-use stage series 1개는 `MISSING`이었고, scale-out 직후 7개 capture는 60초 range의 이전 Pod label set과 instant freshness의 현재 Pod label set이 달라 Worker metric freshness를 `UNKNOWN`으로 보존했습니다. 71개 bundle의 Kafka end/committed/lag required evidence는 모두 `OK/FRESH`였습니다. run `COMPLETE`는 실험 lifecycle 완주 상태이며 모든 개별 bundle이 `COMPLETE`라는 뜻이 아닙니다.

| Signal | Run 1 | Run 2 | Run 3 |
| --- | ---: | ---: | ---: |
| Event `202` / error | `20,047` / `0.00%` | `28,862` / `0.00%` | `28,600` / `0.00%` |
| Peak total lag | `17,537` | `25,256` | `24,096` |
| Peak 60s produce rate | `334.117/s` | `481.033/s` | `476.667/s` |
| Peak 60s committed-offset rate | `127.017/s` | `139.467/s` | `137.500/s` |
| First positive lag | `7,490` | `8,599` | `10,578` |
| Lag first returned to `0` | `196.781s` | `256.575s` | `256.543s` |
| Worker returned to `2/2` | `316.779s` | `346.609s` | `361.580s` |

세 실행의 첫 세 positive-lag capture는 모두 lag가 두 번 연속 증가했습니다. 각 capture의 60초 창에서도 produce rate가 committed-offset rate보다 높고 lag slope가 양수였습니다. Run 1은 첫 pressure capture에서 Worker가 아직 `2/2`였으므로 replica `4`나 KEDA active state는 pressure condition의 required predicate로 사용하지 않습니다.

실측 뒤 제안한 activation 후보는 `total_lag >= 7,000`, 60초 `lag_slope >= 100 records/s`, `produce_rate > committed_offset_rate`를 약 15초 간격의 세 capture에서 연속 충족하고, 두 capture 전이에서 total lag가 계속 증가하는 조건입니다. 이는 약 30초의 지속 관측입니다. exact 임계값과 연속 횟수는 실험 전에 정하지 않았으며, 세 run의 공통 lower envelope를 반올림해 제안했습니다.

이 후보는 `ops.conditions.rules.v1`에 반영하지 않았습니다. v1 single-bundle evaluator는 양수 lag를 계속 `UNKNOWN`으로 유지하며, full 60초 lag `0`만 `ABSENT`로 판정합니다. 후속 negative controls를 통과한 계약은 ordered bundle sequence와 timing/provenance gate를 갖춘 `ops.conditions.rules.v2`로 별도 구현했습니다.

peak 시 최대 partition lag share는 Run 1/2/3에서 각각 `18.90%`, `16.46%`, `20.90%`였습니다. recovery tail의 작은 total lag에서 share `1.0`이 나타났으므로 concentration rule은 absolute lag floor 없이 share만 사용하면 안 됩니다. Worker stage mean은 관측 window 기준 `2.565~15.917ms`였고 finite p95 bucket 상한은 `100ms`였습니다. 이 stage는 `_persist_message_with_cursor`만 측정하며 transaction commit latency가 아닙니다. PostgreSQL은 전 구간 ready/HA를 유지했고 최대 replication delay는 `10,696` bytes였습니다.

- experiment summary: [analysis.md](../results/ops-agent/calibration/20260816T032411Z/analysis.md)
- sanitized manifest와 세 run summary: `results/ops-agent/calibration/20260816T032411Z/`
- local-only raw evidence: 71 normalized bundles, 284 source projections, 약 88 MB

### Negative-control calibration

고정 후보를 수정하지 않고 short burst, sustainable high load, single transient lag spike에 적용했습니다. 세 control은 기존 KEDA min/max `2/4`, polling `5s`, cooldown `120s`와 Worker 설정을 유지했으며, 각 run은 baseline `2/2`에서 시작해 자동 scale-out과 lag drain 뒤 `2/2`로 복귀했습니다.

| Control | Workload | Peak lag | Candidate samples | Result |
| --- | --- | ---: | ---: | --- |
| Short burst | 64 streams, 100 VU, 5s | `3,997` | `0` | `NOT_PRESENT` |
| Sustainable high | 64 streams, 8 VU, 180s | `3,111` | `0` | `NOT_PRESENT` |
| Single transient spike | 64 streams, 100 VU, 10s | `8,854` | `2` | `NOT_PRESENT` |

Sustainable high는 event `22,256`, error `0.00%`, 실제 평균 약 `123.6/s`였습니다. Lag는 `3,111` 부근에서 plateau를 만들었고 마지막 load capture에서 `3,111→3,106`으로 감소했습니다. 이는 180초 local calibration 결과이며 production sustainable throughput 주장이 아닙니다.

Single transient spike는 floor를 넘었지만 lag가 `8,854→7,510→5,514→3,338→0`으로 감소했습니다. Overlapping 60초 window 때문에 처음 두 sample의 slope는 양수였으나 capture 간 lag 증가가 없어 3-capture window가 성립하지 않았습니다. `produce_rate-committed_offset_rate`는 slope와 같은 offset 변화이므로 산술 일치 검증일 뿐 별도 vote가 아닙니다.

Negative evidence 51개 중 `COMPLETE` 40, `PARTIAL` 11이며 Kafka required evidence는 전부 `OK/FRESH`, partition `8/8`입니다. `PARTIAL`은 scale transition의 Worker range/instant label coverage 차이이며 pressure 판정에는 사용되지 않았습니다.

Negative controls를 통과한 계약을 `local-ha.conditions.v2`로 구현했습니다. v2 evaluator는 ordered immutable bundle digest sequence, 동일 scope/source identity, capture timing, 각 bundle의 기존 Kafka gate, 세 capture lag/slope와 두 lag 증가를 하나의 evaluation ID에 결합합니다. 이 구현은 activation만 다루며 recovery/clearing hysteresis는 포함하지 않습니다. v1 코드와 policy는 변경하지 않았습니다.

- negative-control analysis: [analysis.md](../results/ops-agent/negative-control/20260816T040746Z/analysis.md)
- local-only evidence: 51 normalized bundles, 204 raw source projections, 약 62 MB

### `local-ha.conditions.v2` sequence evaluator

```powershell
$inputs = Get-ChildItem results/ops-agent/calibration/20260816T032411Z/run-01/bundles/sample-*.json | Sort-Object Name | ForEach-Object FullName
.venv\Scripts\python.exe -m ops_agent evaluate-sequence `
  --input $inputs `
  --output results/ops-agent/conditions-v2.json
```

`CORE_BACKLOG_PRESSURE=PRESENT`는 인접한 세 capture가 각각 total lag `>=7,000`, 60초 lag slope `>=100 records/s`를 충족하고 두 transition에서 total lag가 모두 증가할 때만 성립합니다. `produce_rate-committed_offset_rate`는 slope와 독립적인 vote가 아니라 같은 offset delta의 산술 일치 검사입니다. KEDA replica, Worker replica, Worker stage latency는 optional context입니다.

모든 입력 bundle에 v1의 exact selector, freshness, 8/8 coverage, committed offset `-1`, offset decrease, 13-sample grid, `lag=end-committed`, range/source/collection timestamp gate를 그대로 적용합니다. profile, context, namespace, topic, consumer group, partition set, collector/endpoint configuration identity, Kafka exporter identity도 sequence 전체에서 같아야 합니다. 하나라도 불완전하면 `PRESENT`를 추론하지 않고 `UNKNOWN`을 반환합니다.

실제 replay에서 positive run 3개는 모두 capture index `[1,2,3]`에서 `PRESENT`였습니다. Short burst, sustainable high, single transient spike는 activation window가 없고 `PRESENT`가 발생하지 않았습니다. Decreasing lag, qualifying capture 2개, stale middle bundle, partial partition coverage, changed consumer group, reordered timestamps fixture도 모두 `UNKNOWN`으로 차단합니다. 상세 ID와 output hash는 [sequence validation summary](../results/ops-agent/sequence-validation/20260816T044352Z/summary.json)에 기록했습니다.

## Phase 3 Evidence-grounded Diagnosis Agent

Phase 3 진입 조건은 동일 ordered bundle로 재계산한
`ops.conditions.v2`의 `CORE_BACKLOG_PRESSURE=PRESENT`입니다. 전달된
evaluation ID와 canonical source bundle digest가 다르거나 condition이
`ABSENT/UNKNOWN`이면 모델 호출 전에 거부합니다. LLM은 incident 존재를
재판정하거나 recovery를 선언할 수 없습니다.

```powershell
$inputs = Get-ChildItem results/ops-agent/calibration/20260816T032411Z/run-01/bundles/sample-*.json | Sort-Object Name | ForEach-Object FullName
.venv\Scripts\python.exe -m ops_agent diagnose `
  --conditions results/ops-agent/sequence-replay/20260816/positive-run-01.conditions.json `
  --input $inputs `
  --output results/ops-agent/diagnosis/positive-run-01.json `
  --live
```

`--live`는 명시적 비용 opt-in입니다. 기본 pytest와 GitHub Actions는
OpenAI API를 호출하지 않습니다. 모델은 `.env.local` 또는 process env의
`OPENAI_MODEL`로 설정하며 현재 기본값은 비용 민감형
`gpt-5.6-luna`입니다. API key는 prompt, log, fixture, artifact,
diagnosis ID에 포함하지 않습니다.

Tool registry `ops.diagnosis.tools.v1`:

| Tool | Normalized evidence | 상태 |
| --- | --- | --- |
| `get_partition_lag` | activation window의 partition lag, total, slope, max share | AVAILABLE |
| `get_worker_stage_latency` | `_persist_message_with_cursor` stage context, commit 제외 | AVAILABLE |
| `get_worker_replica_status` | desired/current/ready/available | AVAILABLE |
| `get_keda_status` | ScaledObject와 Ready/Active/Fallback/Paused | AVAILABLE |
| `get_postgres_health` | `/health/ready` PostgreSQL HA component | AVAILABLE |
| `get_application_readiness` | parent readiness status/reason | AVAILABLE |
| `get_runtime_image` | desired tag와 pod imageID | AVAILABLE |
| `get_pod_restart_status` | pod phase/readiness/restart/last termination | AVAILABLE |
| `get_argocd_status` | sync/health/revision/reconciled observation | AVAILABLE |

도구는 raw source와 arbitrary argument를 받지 않습니다. 선택된 activation
capture의 normalized Evidence를 줄여 새 evidence ID를 발급합니다. arbitrary
PromQL, URL, shell, kubectl, filesystem access와 모든 write 동작은 registry에
없습니다. exact PostgreSQL commit/insert rate, transaction commit latency,
consumer rebalance event, CPU throttling은 계속 `UNAVAILABLE`입니다.

`ops.diagnosis.v1`은 condition evaluation ID, ordered source digest, diagnosis
policy, tool registry/model/budget, initial/additional evidence ID, tool step,
hypothesis, stop reason, token usage를 보존합니다. Hypothesis는
`HOT_KEY_SUSPECTED`, `WORKER_PATH_PRESSURE_SUSPECTED`,
`POISON_RECORD_RETRY_SUSPECTED`, `SEQUENCE_CONTENTION_SUSPECTED`,
`CONSUMER_REBALANCE_SUSPECTED`, `INSUFFICIENT_EVIDENCE`로 제한합니다.
각 결과는 supporting/conflicting evidence ID와 evidence gap을 가지며
self-reported confidence percentage는 없습니다.

Deterministic validator는 citation 존재, tool allowlist, 중복 호출, step/tool
budget, stop consistency, rebalance `UNAVAILABLE` 보존, condition 재판정,
recovery/action code를 검사합니다. 최대값은 tool step `4`, tool call `4`,
output token `1,600`, request timeout `30s`, transport retry `1`입니다.
`max_output_repairs=1`은 transport retry와 분리된 semantic repair budget입니다.
최초 structured result가 validator를 통과하지 못한 경우 machine-readable
error와 기존 result만 전달하고 tool이 없는 repair turn을 한 번 허용합니다.
새 evidence와 fabricated citation은 금지되며 두 번째 결과도 invalid이면
`validation_failure`로 종료하고 completed artifact를 쓰지 않습니다.

9개 golden fixture와 5개 output repair fixture의 scripted offline evaluation은
schema/citation/abstention/tool selection/step/stop과 repair budget을 통과했으며
API 비용이 없습니다. 2026-08-16 actual positive run-01 Luna live dry-run은
Worker stage latency, PostgreSQL health, pod restart, partition lag 도구를 순서대로
호출했습니다. 최초 output은 stop consistency validation에 실패했고 tool 없는
repair 1회 뒤 VALID가 됐습니다. 최종 결과는 모든 causal hypothesis를
`INSUFFICIENT`로 유지하고 `insufficient_evidence`로 중단했습니다. local-only
artifact는 `results/ops-agent/diagnosis/20260816-positive-run-01-luna.json`입니다.

## 다음 calibration 흐름

```text
baseline: lag 0, Worker 2
  -> local-ha.conditions.v2에서 CORE_BACKLOG_PRESSURE=PRESENT activation 관측
  -> committed가 end offset을 추격, lag slope 음수
  -> WORKER_BACKLOG_RECOVERING
  -> lag가 recovery floor 이하
  -> WORKER_BACKLOG_RECOVERED
```

실험은 baseline, incident, recovery 세 capture를 분리하고 같은 topic/group/partition policy와 source provenance를 기록해야 합니다.
위 흐름에서 activation까지만 구현했습니다. `WORKER_BACKLOG_RECOVERING`과 `WORKER_BACKLOG_RECOVERED`, clearing hysteresis는 별도 calibration 전까지 미구현입니다.
