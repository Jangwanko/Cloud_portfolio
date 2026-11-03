# Ops Agent — 운영 조사와 런북 활용

**현재 사건을 조사하고 과거 사례·런북을 대조해 운영자의 다음 판단을 돕는 에이전트**다. 승인된 로컬 장애 주입부터 시점별 관측·Luna 조사·런북 대조·롤백·실제 복구 확인·검색 가능한 사건 기록까지 연결한 실험용 CLI MVP를 구현했다. 상시 자동 감시와 검토된 지식의 자동 승격은 후속 범위다. 모델 가중치 학습이나 fine-tuning은 수행하지 않았다.

이 문서는 현재 기능과 구현 경계의 기준이다. 결과와 한계는 [검증 기록](OPS_AGENT_EVALUATION.md), 실행 명령과 권한은 [CLI 안내](../ops_agent/README.md)에서 확인한다. 실험을 추가할 때 이 세 문서를 갱신하며, 회차별 설명 문서는 만들지 않는다.

<a id="design-direction"></a>
## 고정 목적과 설계 원칙

**2026-11-01 사용자 확정:** 사람이 모든 오류를 계속 감시하고 판단하기 어렵기 때문에, 경미한 오류부터 중대 장애까지 **현재 사건의 지속 조사 + 과거 사례·런북 검색과 적용 판단 + 검증된 경험 축적**을 함께 제공한다. 알려진 오류와 원인 불명 상황을 모두 다루며, 운영자가 믿고 활용할 수 있는 도움을 성공 기준으로 삼는다. 방향 변경은 사용자의 명시적 결정으로 기록한다.

- **정확성과 일관성:** 같은 증거·운영 문맥에서 심각도, 원인 확신 수준, 권장 조치가 근거 없이 뒤집히지 않아야 한다. 표현 차이와 운영 판단 충돌을 구분하며, 일관되게 틀린 답도 실패다.
- **시간에 따른 조사:** 동일 사건의 이전 판단과 현재까지 확보한 증거를 연결한다. 새 증거로 판단을 바꾸면 이전 판단·변경 내용·근거 ID를 남긴다. 과거 결론을 유지하려고 반대 증거를 무시하지 않는다.
- **불확실성 처리:** 증거 부족·수집 실패·판단 충돌을 표시하고, 무엇을 추가 확인해야 후보를 구분할 수 있는지 제안한다. 새 근거 없는 중요한 판단 충돌을 확정 조언으로 전달하지 않는다.
- **권한과 검증:** 감지·회복의 고정 조건과 증거 검사는 코드가 담당하고 LLM은 해석·조사를 보조한다. LLM 제안만으로 재시작·설정 변경·데이터 조작을 실행하지 않는다. 중요한 판단은 운영자에게 전달하며 실제 회복은 관측으로 확인한다.
- **사용 가치:** 원인 정확성뿐 아니라 놓친 이상, 불필요한 경보·권고, 다음 확인의 유용성, 사람이 다시 조사해야 하는 부담, 응답 시간·호출 비용을 평가한다. Runbook과 비교해 도움이 입증된 범위를 명시한다.
- **설명의 작성 주체:** 사용자에게 전달할 장애 설명은 Luna의 원문 또는 출처가 표시된 런북에서 나와야 한다. Codex는 구현·장애 주입·독립 검증을 담당하며 모델 설명의 빈틈을 대신 채워 성공으로 평가하지 않는다. 모델의 런북 적용 해석과 런북 원문도 구분한다.
- **변경 관리:** 모델·프롬프트·도구 변경은 진단 동작의 변경으로 취급한다. 고정 증거 반복 평가와 시간 순서 평가에서 기존 버전과 비교한 뒤 적용 범위를 결정한다. 모델 식별자·설정·원본 응답·최초 실패를 보존한다.

2026-11-03 PG 잠금과 Worker 기동 실패 두 실험에서 관측·조사·복구·기록 연결을 확인했다. Luna는 롤백 후 정상·장애 시점의 실제 증거만 받아 진단하며 복구 정보는 받지 않는다. 상시 감시, 중요한 판단 충돌의 자동 처리, 검토된 지식 승격과 일반적인 모델 교체 품질 보장은 후속 범위다. 현재 기능은 아래 표, 확인 기준은 [평가 문서](OPS_AGENT_EVALUATION.md#operational-assistance-evaluation)에 둔다.

<a id="investigation-and-runbooks"></a>
## 현재 조사와 런북 활용의 결합

목표 흐름은 **관측 → 사건 상태 갱신 → 관련 사례·런북 검색 → 현재 증거와 적용 조건 대조 → 추가 확인·대응 제안 → 결과 관측 → 지식 갱신**이다. 조사 중 확보한 증거로 런북 후보를 다시 찾거나 제외할 수 있다.

| 운영자에게 제공할 내용 | 필요한 근거 |
| --- | --- |
| 현재 상태·영향·이전과 달라진 점 | 현재 사건의 관측 시각·근거 ID와 이전 판단 |
| 유사한 과거 사례와 런북 | 원문 위치·버전, 유사한 점과 다른 점 |
| 적용 가능 / 추가 확인 필요 / 적용 제외 | 필수 조건·충족 증거·반대 증거·누락 정보. 유사도만으로 적용 확정 금지 |
| 다음 확인과 권장 대응 | 후보를 구분할 조회, 조치 전제·위험·실행 권한·결과 확인 방법 |
| 새로 얻은 경험 | 관측 사실·가설·수행 조치·회복 결과·남은 불확실성 |

런북이 없거나 조건이 맞지 않아도 현재 증거로 다음 확인을 제안한다. 실험 종료 시 수행 조치와 복구 증거를 연결한 `runbook-draft.json`을 만들며 catalog에 자동 게시하지 않는다. 사건 기록은 실제·합성 출처와 검토 상태를 표시하고 모델과 독립된 형식으로 검색한다. 운영자가 검토한 절차를 정식 지식으로 승격하는 단계는 후속 범위다.

현재 [런북 목록](../ops_agent/runbook_catalog.json)은 기존 [운영 Runbook](RUNBOOK.md)의 Kafka intake·PostgreSQL/Pgpool·Worker lag 항목을 연결한다. 원문과 조건 metadata를 함께 제공하고 각각 버전을 기록한다. 키워드 검색이 후보를 고르고 LLM이 조건별 근거를 대조한다. 과거 평가의 **고정 Runbook 비교군은 코드 분류 규칙**이다. 과거 실험 사건의 키워드 검색은 [사건 경험 저장 모듈](../ops_agent/incident_experience.py)이 제공하며 검색된 사례는 현재 원인 확정이나 승인된 런북으로 해석하지 않는다.

## 현재 가능한 기능

| 기능 | 가능하게 만든 방법 | 현재 검증 범위 |
| --- | --- | --- |
| 운영 상태를 증거로 수집 | Application·Prometheus·Kubernetes·Argo CD의 고정 조회, timestamp·coverage·출처 hash 보존 | 실제 local-ha 수집. 누락과 오래된 값은 정상으로 바꾸지 않음 |
| Worker backlog 발생·회복 판단 | 버전이 고정된 deterministic condition/recovery evaluator | local-ha 부하·회복 범위에서 검증. 이 판단은 LLM의 성과가 아님 |
| 증거를 조회해 원인 후보와 불확실성 설명 | 검증된 activation에 읽기 도구를 연결하고 근거 ID를 붙인 구조화 출력 요구 | 기존 진단 경로와 v3.5 snapshot 재생 경로 구현 |
| 부족한 증거에 대해 판단 보류 | 지원·충돌·문맥 인용 구분, 미관측 값·기준선 부재를 gap으로 유지 | 합성 사례와 과거 실제 snapshot에서 ABSTAIN 확인 |
| 저장된 장애를 따라 설명 재생 | condition·diagnosis·recovery를 incident identity로 연결, 별도 공개용 projection | 과거 실제 incident 재생. 공개 화면은 저장 기록 재생이며 현재 진단 호출이 아님 |
| 실제 장애 로그를 바탕으로 복구안 제안 | 별도 실험 runner에 Pod 상태·시작 로그·probe 설정·변경 이력 snapshot 제공 | Worker 시작 오류 식별, PG startup probe 제안. Codex 검토·수정 뒤 복구 관측 |
| 열린 incident의 후속 증거 처리 | `FollowupReplay`가 현재까지의 prefix만 읽고 UNKNOWN·closure 이후 연결 차단 | 오프라인 후보 구현. 상시 수집·LLM·운영 incident 저장소에는 미연결 |
| 현재 증거와 런북 대조·후속 갱신 | canonical bundle, 원문 검색, 조건별 근거 검증, 이전 결과 hash와 관측 시각 연결 | 합성 3상황·4시점×2회. 기대 적용 판단 8/8, 두 반복의 심각도·적용 여부 일치. 일반 안정성·권고 전체의 정확성은 미입증 |
| 실제 부하로 상태 변화 재현 | 요청 수·시간·동시성 제한, 수락 ID 기록, 부하 중·후 수집과 저장 표본 확인 | 20초 부하 2회, 최대 lag 3,620→0과 Worker 2→4 관측. 실제 관측의 LLM 재생은 적체 시점에서 실패; 회복 설명 갱신 미검증 |
| 소량 요청의 DB 잠금 원인 조사 | 새 stream 행의 한시적 잠금, 수락·저장 증거와 DB waiter/blocker 관계를 순차 제공 | 21건 중 15건 지연 후 21/21 저장. 모델이 잠금 원인 후보로 좁힘. 회복 후 과거 lag로 DEGRADED를 유지하는 한계 확인 |
| 모델이 조회·검색하고 직접 설명 | `--investigate`: 모델의 읽기 도구 선택과 런북 검색, 조회한 근거만 인용, 모델 문장 그대로 출력 | 비교용 실험 경로. 기존 잠금/회복 기록에서 6회 호출. DEGRADED→NORMAL. 전량 도구 조회·저장 시각 및 202 계약 해석 오류는 미해결; 실시간 조회 아님 |
| 고정 관측과 모델 해석 분리 | 기본 CLI에서 요청 ID별 수락·저장 조회, 최신/과거 lag, DB 대기 관계를 먼저 계산·저장. Luna에는 원인 후보·런북 판단·추가 확인만 요청 | 모델 실패·구조화 계약 충돌 시에도 관측 보존. 현재/과거 런북 조사 구분. 자유문장은 항상 미검토 해석이며 의미 정확성 보장 없음 |
| 통제된 실제 장애의 전체 흐름 | 기존 PG/Worker 주입기·수집기를 `ops_agent_mvp.py`로 연결. 승인·예산·시점별 Bundle·원본 응답·설정 비교 보존 | PG 21/21, Worker 장애 중 수락 20/20 저장 및 설정·Argo 정책 복원 확인. rollback 후 시점별 조사이며 실시간 추론은 아님 |
| 모델과 독립된 장애 경험 재사용 | `ops.incident-experience.v1`에 원인·관측·조치·복구·가설 검토·artifact hash 저장. CLI 검색과 미게시 초안 생성 | 두 실제 사건 저장·검색 확인. 복구 증거 검증과 운영자 의미 검토 상태 분리 |

**현재 한계:** 범용 장애 정확도, 고정 Runbook 대비 추가 가치, 상시 자동 진단, 자율 복구는 입증하지 않았다. 실험 runner의 [판정 결함은 반례 검증과 함께 보강](OPS_AGENT_EVALUATION.md#known-gaps)했지만 파일 생성이나 exit 0만으로 원인 정확성·복구 성공을 선언할 수 없다.

<a id="phase-3-evidence-grounded-diagnosis-agent"></a>
## 모델과 코드의 역할

```text
운영 소스 → 수집·정규화 → condition 판정 → 읽기 전용 진단 → 근거 검증
                                  ↓
                       recovery 판정 → incident 기록
```

- 수집기: 관측 값·시점·대상·완전성과 raw artifact hash 기록
- deterministic evaluator: `PRESENT / ABSENT / UNKNOWN`, 회복 상태, incident 연결 판단
- 모델: 허용된 도구 선택, 관측 설명, 원인 후보·반대 근거·추가 조사 제안
- validator: 인용·형식·조사·예산 제한 검사. 지원 범위는 실행 경로별로 다름
- 운영자 또는 개발 에이전트: 제안 검토, 변경 수행, 실제 복구 검증

모델은 shell·임의 PromQL·임의 URL·Kubernetes 쓰기 권한이 없고 condition이나 recovery를 덮어쓰지 않는다. 최근 로컬 복구는 Codex가 수행했다. 모델이 직접 서비스 설정을 고쳤다고 해석하지 않는다.

**2026-11-01 후속 합의 — 다음 구현의 역할 경계:** 필수 관측은 고정 수집하고, 이미 정의된 상태·회복 조건과 서비스 계약은 코드가 판정한다. Luna는 그 결과로 설명되지 않는 원인 후보, 런북의 적용 근거, 후보를 구분할 추가 확인을 제안한다. `202`가 Kafka append 이후 반환된다는 계약, 현재 lag와 과거 lag의 구분, 상태 조회 시각과 실제 저장 시각의 차이를 모델이 다시 추측하게 두지 않는다. 관측 사실과 코드 판정은 그대로 표시하고 모델 설명·런북 원문과 출처를 구분한다.

**2026-11-02 구현:** 기본 CLI는 [고정 관측](../ops_agent/observation_facts.py)과 [분리된 조사 응답](../ops_agent/grounded_assistant.py)을 사용한다. 코드의 요청 상태는 `ACCEPTED_REQUESTS_PENDING / SAMPLED_REQUESTS_PERSISTED / UNKNOWN`이며 관측한 ID에만 적용한다. 단일 snapshot으로 전체 서비스 심각도·지속 회복·incident 종료는 판정하지 않는다. 기존 condition/recovery evaluator의 계약도 변경하지 않는다. 모델 실패·형식 오류·구조화 상태/시간 계약 충돌은 `FAILED / WITHHELD`로 남기며 관측 결과는 계속 제공한다.

모델은 현재 상태·심각도를 작성하는 필드가 없고, 런북 조사는 `CURRENT / HISTORICAL`로 구분한다. 표본 전량 저장·최신 lag 0에서 과거 peak로 현재 지연 조건을 `MET`으로 만들면 보류한다. 검사에 통과한 자유문장도 `UNREVIEWED`이며 문장 전체의 모순·환각을 자동 검증한 것은 아니다. 이 경계는 기본 CLI에 적용되며 기존 runner와 `--legacy-assistance`, `--investigate`는 비교용 실험으로 유지한다. 모델의 도구 선택이 비용이나 조사 품질을 개선했다는 근거는 없다. 인용·형식 검증 통과와 설명의 사실 정확성을 별도로 평가하며, Codex의 해석 대신 주입 기록·요청 ID·DB 대기 관계·회복 관측으로 검증한다.

**v3의 요청별 사실·관측 이력:** 코드가 각 요청의 append 수락, 저장 확인 여부, 상태 조회 가시성을 별도 필드로 제공한다. `unknowns`는 코드가 만든 미확인 항목 ID만 선택할 수 있고 문장은 코드가 표시한다. 수락된 요청의 상태 조회가 403/503이어도 append 수락을 미확인으로 바꾸는 항목은 허용하지 않는다. 원인 후보·런북 설명·추가 조회의 자유문장까지 자동으로 참이라고 보증하지는 않는다.

**출력 계약 보강:** Responses API의 strict JSON Schema로 필수 필드와 이번 입력에 존재하는 인용 ID를 제한한다. 기존 코드 검증도 유지하며, 응답 거절·출력 중단·계약 위반은 원문을 보존하고 설명을 보류한다. 모델·상태 판정 권한은 그대로다. 반복 평가는 호출 수를 먼저 고정하고 동일 입력 hash를 확인하며, 형식 준수·런북 판단 변화·추가 조사의 유용성을 구분한다. [공식 구조화 출력 규칙](https://developers.openai.com/api/docs/guides/structured-outputs)을 따르지만 형식 통과를 의미 정확성으로 취급하지 않는다.

**v4의 조사 기준·누락 검사:** [조사 정책](../ops_agent/investigation_policy.py)이 현재 DB blocking edge 또는 명시적 primary 연결 실패를 PG 런북의 현재 조사 근거로 고정한다. 없으면 미확인으로 남기며, 과거 잠금 설명은 별도 이력에서 유지한다. 이는 요청별 원인 확정이나 모델 진단 성과가 아니다. 지원하는 증거 형식은 DB wait graph와 application readiness 관측으로 한정하며 임의 오류 로그를 자동 해석하지 않는다.

조회 불가 요청, 원하는 Worker 수가 양수인데 ready가 0인 관측, 현재 DB 잠금에는 필수 조사 대상 ID를 만든다. Luna는 `next_checks.target_ids`와 해당 관측 ID를 붙여 구체적인 읽기 전용 확인을 제안한다. 대상 누락·다른 근거 인용은 응답 보류로 처리한다. ID를 붙인 문장 자체가 관련 있고 유용한지는 별도 평가한다. 필수 항목을 표시한 코드의 성과를 모델이 스스로 중요한 관측을 발견한 성과로 계산하지 않는다. 새 기록은 `ops.grounded-assistance.v4`이며 이전 v3 원본은 보존하고 암묵적으로 변환하지 않는다.

[관측 이력](../ops_agent/observation_history.py)은 모델 요약과 독립적으로 이전 코드 관측 최대 8개를 보존한다. 각 항목은 관측 시각·incident/scope·원본 record hash·내용 hash를 포함하며, 과거 ID는 판단 변경 설명의 `history_ids`에서만 참조하고 현재 근거로 인용하지 못한다. 모델 응답이 보류돼도 관측 이력은 이어진다. 오래된 항목이 입력에서 생략되면 개수를 명시하고 원본 artifact는 보존한다. 모델 입력 전체의 기존 90 KB 제한은 유지하므로 큰 이력은 임의 요약하지 않고 명시적으로 거부한다.

<a id="execution-paths"></a>
## 실행 경로

현재는 verified backlog 진단, v3.5 adapter, 오프라인 후속 replay, Worker·PG 실험, `assist_incident.py` 경로가 존재한다. CLI 기본 경로는 backlog activation을 요구하지 않고 선택된 증거에서 코드 관측을 저장한 뒤 선택적으로 Luna 조사를 추가한다. 이전 모델 상태 작성 경로는 `--legacy-assistance`로 명시한다. 실행 경로 간 record 형식은 호환되지 않으며, 후속 입력은 같은 버전·incident·scope·profile·provenance와 엄격히 이후 시각을 요구한다. 상세 명령·예산·출력 계약은 [CLI 안내](../ops_agent/README.md)에 둔다. 통합된 상시 운영 보조자는 아직 없다.

<a id="evidence-contract"></a>
## 증거와 판정 계약

- 수집 값: `OK / MISSING / ERROR / NOT_APPLICABLE / UNAVAILABLE`; freshness: `FRESH / STALE / UNKNOWN`
- collection의 `PARTIAL`: 일부 증거 누락. 모든 condition을 일괄 실패시키거나 서비스 장애로 해석하지 않음
- Kafka offset `-1`, 음수 lag, 누락 partition, offset 감소를 그대로 보존. 0으로 보정하지 않음
- source timestamp와 query 평가 시각 구분, `/ops/summary`의 15초 cache 고려
- Worker processed counter는 terminal 처리 수. PostgreSQL commit 수와 동일시하지 않음
- `db_persist` stage는 request-status 갱신·commit 이전까지. 독립 DB commit latency나 총 저장 지연이 아님
- runtime stage 기준선이 없으면 “상승” 판정 보류. 단일 replica 부족 snapshot은 지속 용량 부족의 확정 증거가 아님
- hash는 로컬 원본·요약 일치 확인. 원격 서버 인증·수집 사실의 완전성 보장은 아님

기존 activation·recovery 수치는 특정 local-ha 부하에서 보정한 조건이다. 전역 서비스 health·production SLO로 해석하지 않는다. 정확한 값과 실행 경계는 [CLI 안내](../ops_agent/README.md), [recovery evaluator](../ops_agent/recovery_evaluator.py)에 둔다.

<a id="phase-3-evidence-grounded-diagnosis-agent"></a>
## 진단 권한과 검증

기존 진단 경로는 lag, stage, replica, KEDA, PostgreSQL, application readiness, runtime image, Pod restart, Argo CD 상태를 제한된 도구로 제공한다. 도구는 normalized evidence와 새 근거 ID를 반환한다. 확인할 수 없는 rebalance·commit 지표는 UNAVAILABLE로 유지한다.

v3.5는 지원·반대·문맥 근거의 역할과 최소 조사 조건을 검사한다. 최근 실험 runner도 빈 출력·미조사 종료·조회하지 않은 근거 인용을 거부하도록 보강했다. 다만 필드와 인용의 존재만으로 원인·복구안이 맞다고 판정하지 않으며, 두 경로의 보장 수준을 섞지 않는다.

<a id="public-replay-boundary"></a>
## Incident와 공개 재생

Lifecycle은 `DETECTED → ACTIVE → RECOVERING → RECOVERED → CLOSED`이며, 종료 기록은 이후 관측으로 다시 열거나 덮어쓰지 않는다. 새 incident 자동 연계·재개는 미구현이다.

공개 재생은 [sanitized replay 데이터](../demo/verified-incident-replay.json)를 사용한다. Canonical 원본은 로컬 보존하며 화면 재생 중 OpenAI API나 운영 소스를 조회하지 않는다. demo-lite의 기록 재생을 현재 local-ha 진단 또는 HA 성능으로 해석하지 않는다.

<a id="implementation-priorities"></a>
## 필요한 구성과 구현 순서

1–4의 최소 CLI 흐름은 저장된 증거 입력으로 구현·합성 검증했다. 실제 부하 재생의 출력·근거 계약 실패 이후, DB 행 잠금 실험에서 요청 수락·저장과 DB 대기 증거를 연결해 원인 후보 갱신을 확인했다. 다만 회복 뒤에도 과거 지연을 현재 DEGRADED로 해석해 현재 상태와 사건 이력의 구분이 먼저 필요하다. 운영자 유용성·상시 호출 제어는 미검증이다. [실패와 후속 작업](OPS_AGENT_EVALUATION.md)을 보존하고 아래 조건을 유지한다. 5–6은 다음 구현 범위다.

| 순서 | 필요한 구성 | 최소 범위·완료 조건 |
| --- | --- | --- |
| 1 | 사건 기록과 결과 형식 | 기존 수집·incident 기록을 재사용. 사건 ID, 시각, 사실/가설, 영향, 미확인 정보, 이전 판단과 변경 근거를 보존하고 두 시점의 결과를 연결 |
| 2 | 조회 가능한 런북 목록 | 기존 RUNBOOK의 관련 항목 3개부터 시작. ID·원문 위치·버전·대상 환경·증상·적용/제외 조건·확인 절차·조치 위험·복구 확인·검증 범위 기록. 기존 분류 규칙과 구분 |
| 3 | 검색과 적용 조건 대조 | 먼저 제목·증상·대상 기반의 작은 로컬 검색으로 연결. 현재 증거와 조건을 대응해 적용 가능/추가 확인/제외를 표시. 일치 자료가 없어도 현재 조사 지속 |
| 4 | 최소 흐름의 검증 | 맞는 런북·증상만 유사한 런북·자료 없음 사례를 통과하고, 같은 증거 반복과 반대 증거 추가에서 중요한 판단 충돌·변경 근거를 검사 |
| 5 | 지속 관측과 호출 제어 | 의미 있는 변화·새 증거·사용자 질문에 호출. 중복 억제·시간/호출 예산·수집 실패 표시·재개 조건을 정의. 관측 공백을 보존하며 감시와 진단 보류를 구분 |
| 6 | 결과 기반 지식 갱신 | 실제 조치·회복 증거와 연결한 신규/수정 초안 작성. 초안·검토됨·폐기/대체됨 상태와 버전 보존. 운영자 검토 후 검증 범위에 한해 재사용 |

1–4는 하나의 사용 흐름으로 구현하고 평가한다. 초기 출력은 CLI/JSON으로 충분하며, 새 UI·대규모 검색 인프라·모델 교체는 이 흐름에서 확인된 필요에 따라 결정한다. 실제 사용자 데이터가 없으므로 출처를 표시한 합성 사례와 기존 로컬 기록을 사용하고 사람의 시간 절감은 미측정으로 유지한다.

**현재 완료 범위:** 고정 관측·요청별 미확인 항목·관측 이력·strict 출력·필수 조사 대상 검사와 승인된 두 로컬 장애의 관측→조사→복구 확인→사건 기록 연결을 구현했다. 모델 과거 재서술은 기본 출력에서 제외하고 새 호출의 `updates`는 빈 목록으로 제한한다. 원본은 보존하며 원인 가설·반대 근거·다음 확인·승인 필요 복구 제안은 검토 대상으로 표시한다.

**이번 MVP의 종료:** 2026-11-03 사용자가 정한 완료 기준으로 개발을 종료한다. 실제 PG/Worker 실험, 기존 런북 세 상황 평가 재사용, 코드 이력 출력, 사건/초안 저장과 검색 경로를 [검증 기록](OPS_AGENT_EVALUATION.md)에 연결한다. 복구 제안 보류·과한 런북 적용·넓은 추가 조사는 후속 품질 과제다. 추가 모델 호출·프롬프트 튜닝·범용 의미 validator·새 UI를 이번 완료 조건에 추가하지 않는다. `master` 병합은 별도 Git 작업이며 현재 로컬 병합 예측의 문서 충돌과 별도 PG 배포 영향을 검토해야 한다. 안정 운영 승격은 별도 판단이다.

지속 원칙: [하네스 범위·종료 규칙](AI_ENGINEERING_WORKFLOW.md#harness-scope-and-stop)을 적용한다. 설계는 이 문서, 결과·실패는 평가 문서, 명령은 CLI 안내에 갱신하며 해결된 과제를 현재 대기 사항에서 제거한다. 관련 변경에 필요한 회귀만 확인하고 상세 회차는 원본으로 보존한다.

## 재사용할 구현

| 구성 | 코드 |
| --- | --- |
| 수집·증거 정규화 | [controller](../ops_agent/controller.py) |
| backlog activation | [sequence evaluator](../ops_agent/sequence_evaluator.py) |
| 기존 모델 진단·API client | [diagnosis agent](../ops_agent/diagnosis_agent.py) |
| 런북 조사와 후속 결과 | [assistance](../ops_agent/runbook_assistant.py), [CLI](../scripts/assist_incident.py), [catalog](../ops_agent/runbook_catalog.json) |
| v3.5 실제 증거 연결 | [runtime evidence](../ops_agent/runtime_evidence_v3.py), [runtime runner](../scripts/run_runtime_diagnosis_v3.py) |
| 후속 증거 후보 | [FollowupReplay](../ops_agent/followup_replay.py) |
| 최근 장애 조사 실험 | [Worker runner](../scripts/run_worker_fault_diagnosis.py), [PG collector](../scripts/diagnose_postgres_restarts.py) |
| 실험 판정 감사 | [offline audit](../scripts/audit_live_experiments.py) |
| 실험 성공 조건·PG 표본 검증 | [acceptance checks](../ops_agent/experiment_validation.py), [PG verifier](../scripts/verify_postgres_recovery.py) |
| Runbook과 순차 조사 비교 | [paired pilot](../scripts/evaluate_investigation.py) |

현재 적용 범위는 `dev-kafka` 작업 트리다. 공통 설계·작업 원칙은 주요 브랜치 공유 대상으로 두되 실제 전파는 별도 요청 범위다. 로컬 PG 부분 적용은 [운영 기록](RUNBOOK.md#ops-agent-local-pg-correction)을 따른다.

## 이전 검증과 master 통합 범위

기존 수집 baseline·calibration·recovery·Gate 2의 수치와 실패 실행은 [TEST_RESULTS](TEST_RESULTS.md)에 보존한다. 축약 때 빠진 진단 입력·예산·Scenario Lab provenance·incident identity·공개 projection 조건은 [기존 master 계약 보존](OPS_AGENT_EVALUATION.md#기존-master의-진단lifecycle-검증-조건-보존)에 보강했다. master의 [Public Controlled Scenario Replay candidate 기록](OPS_AGENT_EVALUATION.md#public-controlled-scenario-replay-candidate---2026-08-29)도 유지한다. 이 과거 계약과 최신 MVP의 검증 범위·호출 예산을 구분한다.

PG Startup Probe 후보는 이번 통합에서 제외했다. PR의 `k8s/values/postgresql-ha-values.yaml`은 master와 동일하며, 기존 변경은 `dev-kafka`의 `564b112` 및 후속 커밋에 보존한다. Primary rollout·Helm 반영 검증은 별도 작업으로 남긴다.
