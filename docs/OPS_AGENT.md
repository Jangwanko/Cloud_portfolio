# Ops Agent — 운영 조사와 런북 활용

**현재 사건을 조사하고 과거 사례·런북을 대조해 운영자의 다음 판단을 돕는 에이전트**를 개발한다. 수집된 증거와 런북 3개를 대조하고 후속 시점의 판단을 연결하는 최소 CLI 흐름을 구현했다. 상시 관측 연결과 결과 기반 지식 축적은 다음 구현 범위다. 모델 가중치 학습이나 fine-tuning은 수행하지 않았다.

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

저장 증거 기반 런북 검색·적용 판단·후속 설명 연결은 합성 pilot까지 구현했다. 상시 감시 연결, 중요한 판단 충돌의 자동 처리, 지식 갱신과 모델 변경 시 회귀 검증은 미완료다. 현재 기능은 아래 표, 확인 기준은 [평가 문서](OPS_AGENT_EVALUATION.md#operational-assistance-evaluation)에 둔다.

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

런북이 없거나 조건이 맞지 않아도 현재 증거로 다음 확인을 제안한다. 향후 조사 결과로 새 런북 초안 또는 기존 항목 수정안을 작성하되, 모델의 설명만으로 검증된 절차로 승격하지 않는다. 관측된 결과와 운영자 검토를 연결하고 실제·합성 사례 및 검증 범위·버전을 구분해 다음 사건에서 재사용한다. 초안 작성·검토·승격의 연결은 아직 미구현이다.

현재 [런북 목록](../ops_agent/runbook_catalog.json)은 기존 [운영 Runbook](RUNBOOK.md)의 Kafka intake·PostgreSQL/Pgpool·Worker lag 항목을 연결한다. 원문과 조건 metadata를 함께 제공하고 각각 버전을 기록한다. 키워드 검색이 후보를 고르고 LLM이 조건별 근거를 대조한다. 과거 평가의 **고정 Runbook 비교군은 코드 분류 규칙**이며, 이 문서 검색 기능과 다르다. 과거 incident 자체의 검색·지식 갱신은 미구현이다.

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
| 모델이 조회·검색하고 직접 설명 | `--investigate`: 모델의 읽기 도구 선택과 런북 검색, 조회한 근거만 인용, 모델 문장 그대로 출력 | 기존 잠금/회복 기록에서 6회 호출. DEGRADED→NORMAL. 전량 도구 조회·저장 시각 및 202 계약 해석 오류는 미해결; 실시간 조회 아님 |

**현재 한계:** 범용 장애 정확도, 고정 Runbook 대비 추가 가치, 상시 자동 진단, 자율 복구는 입증하지 않았다. 실험 runner의 [판정 결함은 반례 검증과 함께 보강](OPS_AGENT_EVALUATION.md#known-gaps)했지만 파일 생성이나 exit 0만으로 원인 정확성·복구 성공을 선언할 수 없다.

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

이 경계는 다음 구현의 완료 기준이며 현재 assistance 경로에 모두 강제되어 있지는 않다. `--investigate`는 선택적 실험으로 유지한다. 두 시점 모두 모든 관측 도구를 읽었으므로 모델의 도구 선택이 비용이나 조사 품질을 개선했다는 근거는 없다. 인용·형식 검증 통과와 설명의 사실 정확성을 별도로 평가하며, Codex의 해석 대신 주입 기록·요청 ID·DB 대기 관계·회복 관측으로 검증한다.

<a id="execution-paths"></a>
## 실행 경로

현재는 verified backlog 진단, v3.5 adapter, 오프라인 후속 replay, Worker·PG 실험, 새 `assist_incident.py` 경로가 존재한다. 새 경로는 backlog activation을 요구하지 않고 선택된 증거와 질문을 받아 조사 설명을 저장하며 기존 incident lifecycle을 변경하지 않는다. 상세 명령·예산·출력 계약은 [CLI 안내](../ops_agent/README.md)에 둔다. 통합된 상시 운영 보조자는 아직 없다.

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

**다음 완료 조건:** 5의 상시 호출 연결에 앞서 고정 사실·현재 상태·과거 이력을 코드 출력으로 분리하고 모델 설명의 계약 위반을 검출한다. 잠금 발생→해제→회복, 증거 누락, 유사 증상·다른 원인의 회귀 사례에서 상태 일관성·원인 갱신·잘못된 확신·추가 확인의 유용성을 검증한다. 같은 입력의 고정 관측+런북 흐름과 비교해 Luna가 더 제공한 정보를 기록한다. 현재는 `dev-kafka`의 실험 체크포인트이며 안정 운영 기능 승격은 보류한다. `master` 병합 시 실험 기능의 적용 경계와 별도 PG 설정의 전체 rollout 검증 여부를 각각 검토한다.

지속 원칙: 새 작업은 위 구성 중 해결할 빈틈과 완료 조건을 명시한다. 설계는 이 문서, 결과·실패는 평가 문서, 명령은 CLI 안내에만 갱신한다. 모델·프롬프트·도구·런북 버전 변경 시 관련 회귀 사례를 다시 확인하고, 반복 횟수·합격 기준은 실행 전에 고정한다. 상세 회차 기록은 원본으로 보존하며 설명 문서를 추가하지 않는다.

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
