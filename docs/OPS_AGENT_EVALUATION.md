# Ops Agent 진단 품질 평가

## 2026-10-29 Step 7 — 관측·가설·문맥을 구분하는 v3 후보

별도 `ops.diagnosis.decision.v3` 후보에서 observations·hypotheses·context·gaps·conclusion을 분리했습니다.
Stage 상승을 관측으로 남기고 원인 판단은 보류할 수 있으며, lag/KEDA 문맥이 직접 원인 지지 근거로 승격되는 경우를 거부합니다.
기존 v2 입력·출력·validator/rubric과 실제 실행 점수는 유지했습니다.

- [역할·vocabulary 변경·검증 경계와 로컬 예시](OPS_AGENT_V3_CANDIDATE.md)
- 후보 테스트 `22 passed in 0.41s`; `results/validation/20261029T125012Z-40b84558/`
- 전체 suite `756 passed in 30.44s`; `results/validation/20261029T125048Z-df56b36f/`
- 예시 export: `results/validation/20261029T125047Z-25098cc6/`, API 요청 `0`
- 작성한 예시 4개는 계약 표현 점검이며 실제 모델 평가·과거 결과 자동 변환 제외
- 기존 Worker path label의 역할이 바뀌므로 v2/v3 통합 점수의 직접 비교·기준 승격 제외
- 실제 v3 실행기와 평가 기준은 후속 범위; commit/push/merge·runtime 배포 없음

## 2026-10-29 Step 6 — 기존 결과의 기준 검토와 분리 보고

추가 모델 호출 없이 Step 5의 저장된 8회 응답을 분석했습니다.
[review_diagnosis_pilot.py](../scripts/review_diagnosis_pilot.py)는 source schedule·평가 입력 hash와
catalog/rubric identity를 확인하고, v1 점수를 재계산해 기존 summary와 일치해야 검토 리포트를 생성합니다.
리포트는 별도 폴더에 저장하며 기존 score·prompt·validator·rubric과 실행 원본을 변경하지 않습니다.

| 평가 항목 | 결과 | 해석 |
| --- | ---: | --- |
| 기존 통합 quality | 1/8 | 원래 Gate 유지 |
| Fixture supported label 일치 | 5/8 | 제한된 fixture 기대값과의 일치; causal truth 아님 |
| 필수 조사 완료 | 6/8 | KEDA 관측 누락 2회 유지 |
| 보류 기준 충족 | 6/8 | 관측 부족 시나리오 2회는 계속 실패 |
| 인용 관계 | 현재 규칙 일치 5회 / 검토 필요 3회 | 새로운 합격률로 합산하지 않음 |

각 차원은 중복되므로 분모를 합치거나 합격 수를 더하지 않습니다.
`ops.diagnosis.review.v1`은 검토용 분해 버전이며 새로운 quality rubric이 아닙니다.
`new_integrated_pass_rate=null`을 명시적으로 유지합니다.

### R001 — 미정의 인용과 KEDA 문맥

- 기존 v1에서 미정의된 초기 lag·HOT_KEY 반박 인용 2회를 `REVIEW_REQUIRED`로 표시
- 존재하는 ID·지표의 문맥 인용과 특정 원인에 대한 직접 근거를 구분할 필요
- KEDA active 상태는 replica availability deficit의 독립 증거가 아니므로 어느 인용 방향이든 문맥 검토 대상으로 표시
- 현재 8회 중 KEDA 인용 문맥 검토 1회와 미정의 인용 2회, 총 검토 대상 3회
- 기존 v1의 signed relation·quality 결과는 보존; 검토 표시를 올바른 인용이나 거짓 인용 확정으로 사용하지 않음
- 추후 문맥 근거 역할을 명시하려면 별도 output/rubric version에서 정의하고 자동 정답 처리 제외

### R002 — KEDA 조회와 조사 완결성

- Replica desired/available 관측으로 shortfall 가설의 fixture label에 일치한 두 실행 확인
- KEDA 조회는 현재 rubric의 운영 조사 완결성 요구이므로 누락 실패 유지
- 누락된 추가 조사와 원인 가설 label의 불일치를 별도 항목으로 보고
- 이번 단계에서 KEDA 요구 제거·필수 도구 집합 변경 없음

### R003 — 관측된 증상과 원인 후보

- Stage 상승은 관측된 Worker path 신호; commit latency나 DB 원인을 직접 확정하는 지표로 확대 금지
- HA/복제 health와 query 성능의 차이를 보존하고 PostgreSQL 관측 부재를 정상으로 해석하지 않음
- DB path와 Worker path를 함께 supported로 표시한 1회에 symptom/explanation 구분 검토 표시
- PostgreSQL discriminator가 없는 상태에서 Worker path를 supported로 표시한 2회와 기존 보류 실패 보존
- 다음 후보의 설계 방향은 관측 신호·원인 가설·문맥/지지/반박 근거의 역할 분리; 이번에 기존 출력 schema와 가설 집합을 변경하지 않음

### 실행과 검증

```powershell
.venv\Scripts\python.exe scripts\review_diagnosis_pilot.py `
  --input-dir results\ops-agent\quality-evaluation\step4-prompt-v2-live-20261029 `
  --output-dir results\ops-agent\quality-evaluation\criteria-review-01
```

- 이번 로컬 결과: `results/ops-agent/quality-evaluation/step6-prompt-v2-review-20261029/`
- [공유용 차원별 결과·source/implementation hash](../results/ops-agent/diagnosis/criteria-review-20261029-summary.json)
- 검토 실행: `results/validation/20261029T123416Z-1a96b0c5/`; exit `0`은 검토 완료이며 quality Gate 승격 아님
- 관련 테스트: 최초 `8 passed / 1 failed`는 test fixture의 enum/string 타입 오류, enum으로 수정 후 9개 통과
- 전체 suite `734 passed in 31.94s`; `results/validation/20261029T123417Z-b8e29402/`
- 재계산 점수와 원본 summary 일치, 결과 디렉터리 덮어쓰기 거부·source 불변·network-free 검증
- 추가 API 호출·기준 소급 변경·배포·commit/push/merge 없음

## 2026-10-29 Step 5 — prompt v2 실제 재평가

`ops.diagnosis.prompt.v2` 후보로 기존 네 development scenario를 두 번씩 실제 실행했습니다.
이전 run과 요청 모델 `gpt-5.6-luna`, schedule, budget, catalog/rubric snapshot이 같음을 확인했습니다.
Scorer·validator·schema·normalizer·tool source hash도 이전 평가 기록과 일치합니다.
요청·응답에는 같은 blinding 규칙을 적용하고 새 디렉터리에 원본을 보존했습니다.

- 로컬 원본: `results/ops-agent/quality-evaluation/step4-prompt-v2-live-20261029/`
- 실행 기록: `results/validation/20261029T122626Z-45da5ff2/`
- 공유용 [v2 축약 결과·원본 hash](../results/ops-agent/diagnosis/pilot-prompt-v2-20261029-summary.json)
- 이전 [0/8 원본 요약](../results/ops-agent/diagnosis/pilot-20261029-summary.json) 보존; 새 요약에 이전 파일 hash와 조건 비교 포함
- Prompt identity: `ops.diagnosis.prompt.v2`, SHA-256 `b87b4656a6c0b937059a75000e9886bae4b36481cccd2ce12bb63b3f075764bc`

### 관측 결과

| 항목 | 이전 prompt | Prompt v2 |
| --- | ---: | ---: |
| 실제 모델 조사 | 8 | 8 |
| 최초 contract 통과 | 0/8 | 7/8 |
| Repair 사용 | 8/8 | 1/8 |
| 최종 contract 통과 | 0/8 | 8/8 |
| 현재 통합 quality 통과 | 0/8 | 1/8 |
| 같은 조건 Runbook quality 통과 | 8/8 | 8/8 |
| 관측 tool result 수 | 31 | 30 |
| API 요청 | 47 | 39 |
| Input / output tokens | 85,942 / 11,837 | 82,920 / 6,393 |
| LLM elapsed 평균 | 18.63s | 11.58s |

V2에서 남은 최초 contract 오류 1회는 `CONFLICTED_REQUIRES_BOTH_CITATION_SIDES`이며,
도구 없는 repair 1회 뒤 VALID가 됐습니다. 여덟 조사 모두 contract를 통과했지만 quality 실패 7회를 유지했습니다.
따라서 wrapper exit `1`은 평가 실행 중단이 아닌 고정 quality Gate 실패입니다.
Usage는 39개 응답 전부 확보했고 미실행 trial은 `0`입니다.
Elapsed 합은 `92.68s`입니다. 반복 8회·network 영향을 포함하므로 일반적인 속도·비용 개선율로 승격하지 않습니다.
실제 response model snapshot과 금액 비용은 이번에도 미측정입니다.

### Quality 실패의 분리

| 시나리오 | Supported set exact match | Quality | 남은 확인 |
| --- | ---: | ---: | --- |
| Worker DB-path pressure | 2/2 | 0/2 | 초기 lag 또는 HOT_KEY 반박 인용의 의미 관계가 현재 rubric에 미정의 |
| Worker replica shortfall | 2/2 | 0/2 | 필수 집합의 KEDA 조회 누락 |
| PostgreSQL path degradation | 1/2 | 1/2 | 한 실행이 PostgreSQL path와 Worker path를 함께 supported로 표시 |
| Telemetry unavailable | 0/2 | 0/2 | 두 실행 모두 Worker path를 supported로 유지, 보류 기대값 불충족 |

Supported set exact match 전체는 `5/8`이며 인용·조사 완결성을 포함한 통합 quality와 다릅니다.
Failing check 수는 인용 의미 `3`, 필수 관측 `2`, supported set `3`, 보류 `2`입니다.
한 실행이 여러 check에서 실패할 수 있으므로 합을 실패 조사 수로 사용하지 않습니다.

인용 의미 실패 중 두 실행은 `unscored_citation_count=1`입니다.
이 값은 자동 채점 규칙이 없는 관계이며 거짓 인용으로 확정한 결과가 아닙니다.
다른 한 실행의 KEDA 인용 방향은 현재 rubric 관계와 불일치합니다.
KEDA 누락은 현재 조사 완결성 기준의 실패로 기록하며 replica shortfall 가설의 오답과 구분합니다.
이번 점수를 높이기 위해 rubric을 소급 변경하지 않았습니다.

### 다음 검토

- 출력 계약 개선은 이 고정 입력·8회 범위에서 확인, 판단 품질·일반화 개선 주장은 보류
- 다음은 추가 API 호출보다 인용 미채점·조사 완결성·증상과 원인 후보의 정의를 사람이 검토하는 단계
- KEDA 관측의 필수성, 초기 lag의 문맥 근거 역할, Worker path 가설의 의미를 별도 결정 기록으로 고정할 필요
- 기준 변경이 필요하면 새 rubric/version에서 전후 결과를 분리하고 과거 점수 유지
- 이후 재실행은 검토된 기준과 별도 평가 입력을 준비한 뒤 새 run으로 보존
- 이번 단계는 실행·분석·문서 변경; prompt/validator/scorer 수정, 배포·commit/push/merge 없음

## 2026-10-29 Step 4 — 공통 출력 계약과 오프라인 repair 검증

Step 3의 실제 8회 실패를 바탕으로 `ops.diagnosis.prompt.v2` 후보를 구현했습니다.
[공통 계약](../ops_agent/scenario_contract.py)을 초기 메시지와 repair 메시지에 동일하게 제공합니다.
이번 단계는 오프라인 코드·실행 경로 검증이며 추가 API 호출과 실제 모델 재평가는 수행하지 않았습니다.
기존 pilot의 manifest·summary·8개 state는 축약 결과의 hash와 모두 일치합니다.

### 변경 내용

- `SUPPORTED`: 직접 지지하는 관측과 supporting citation 요구
- `CONFLICTED`: 지지·반박 양쪽 인용 요구; 반박 근거만 있는 상태와 구분
- `INSUFFICIENT`: nonempty gap 요구; 지지 근거 부재·관측 누락·반박만 있는 상태 보존
- Rebalance: 전용 telemetry 부재와 정확한 `CONSUMER_REBALANCE_TELEMETRY_UNAVAILABLE` gap 코드 명시
- 관측 한계: stage와 commit latency, PostgreSQL HA/복제 건강과 query latency 구분
- 판단 보류: 누락 관측 때문에 원인 후보 구분이 어려우면 affected candidate를 INSUFFICIENT로 유지
- Repair: 첫 오류가 전체 오류 목록을 뜻하지 않음을 명시하고 모든 가설·인용·gap·stop을 한 번에 점검하도록 전달
- Repair 제약: 재진단·새 가설·가짜 지지 근거·새 evidence·도구 호출 금지, 유효한 결론과 근거 보존 지시

시나리오 이름·정답·수치 임계값·기대 도구 순서는 공통 계약에 포함하지 않습니다.
기존 validator, quality rubric, tool allowlist, 조사 4회·repair 1회 제한은 유지합니다.
잘못된 모델 출력을 Python에서 성공 값으로 바꾸는 자동 교정 경로는 추가하지 않았습니다.
공통 계약의 준수 여부와 출력의 의미적 품질은 계속 별도로 평가합니다.

### 오프라인 검증 범위

| 확인 대상 | 검증 |
| --- | --- |
| 누락 gap·잘못된 rebalance gap·CONFLICTED 양쪽 인용 누락 | 실제 scenario loop의 최초 실패·repair 1회·최종 통과 이력 보존 |
| 첫 오류 뒤의 다른 계약 위반 | scripted repair 결과에서 두 오류 모두 해소한 경로 확인 |
| Repair 후 같은 위반·가짜 citation·도구 요청 | 최종 거부, 추가 repair 미실행 |
| 관측 부족에도 supported를 유지하는 scripted 응답 | contract 통과와 별개로 기존 quality 보류 검사 실패 |
| 정답 노출 | 네 시나리오 모두 같은 계약과 system 지시, fixture/title/기대 분기 미포함 |

위 통과 경로는 미리 정한 응답을 반환하는 test client로 검증했습니다.
실제 LLM이 새 prompt에서 repair 또는 판단 보류를 더 잘한다는 결과로 사용하지 않습니다.

- 관련 오프라인 테스트: `52 passed in 2.99s`; `results/validation/20261029T122211Z-8c150628/`
- 전체 suite: `725 passed in 38.87s`; `results/validation/20261029T122236Z-c86c10ee/`
- 문서 링크 검사 오류 `0`, diff 공백 검사 통과
- 기존 pilot manifest·summary·8개 state hash 일치, 원본 변경 없음
- 추가 API 호출·배포·commit/push/merge 없음; 실제 모델 비교는 후속 run 범위

### 다음 실제 모델 실행의 식별

새 pilot manifest는 `prompt_identity.version`과 canonical SHA-256를 기록하고,
`prompt-contract.json`에 system 지시·공통 계약·repair 지시 snapshot을 보존합니다.
이 identity는 output schema도 포함하며 기존 source/file hash와 함께 사용합니다.
Step 3의 이전 manifest는 해당 필드가 없는 원본 상태로 유지합니다.
다음 실행은 새 출력 디렉터리를 사용하고 같은 모델·입력·budget에서 비교해야 합니다.
Prompt 길이 변화에 따른 token·비용·정확도 변화와 새 장애 조건의 대응력은 아직 미측정입니다.

OpenAI의 [평가 지침](https://developers.openai.com/api/docs/guides/evaluation-best-practices)과
[구조화 출력의 한계](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)를 참고했습니다.
이 프로젝트의 통과 기준은 위 공통 계약·고정 validator/rubric과 로컬 실행 기록입니다.

## 2026-10-29 Step 3 — 실제 모델 예비 실행

[run_diagnosis_pilot.py](../scripts/run_diagnosis_pilot.py)는 기존 로컬 키와 설정된 모델을 사용해
네 controlled development scenario를 두 번씩 순차 실행합니다. 실제 모델 호출은 `--live`가 있을 때만 진행합니다.
키는 기존 configuration loader에서 읽으며 prompt·trace·artifact에 포함하지 않습니다.

### 고정 실행 조건

- 모델: 로컬 `OPENAI_MODEL` 설정, 이번 실행 `gpt-5.6-luna`; 응답의 실제 snapshot 이름은 현재 parser의 보존 범위 밖
- 조사 8회, 조사당 최대 도구 4회 + 최종 출력 1회 + 도구 없는 semantic repair 1회
- 최대 API 요청 48회, response output 상한 1,600 tokens, reasoning `low`, 요청 timeout `30s`
- Transport 자동 재시도 `0`; model response가 없는 API/runtime 오류 발생 시 batch 중단
- 미실행 trial은 `unrun_attempt_count`로 분리; 실행한 실패는 실행 시도 분모에 유지
- 전체 schedule·policy·catalog/rubric·source/코드 hash를 API 호출 전에 저장
- 각 요청을 `turn-NN.json`에 먼저 저장하고 반환 response·usage 또는 안전한 error type 추가
- 최초 출력·repair 응답 모두 보존; 실패 envelope에도 token·repair 이력 유지
- 고정 Runbook도 같은 scenario마다 별도 실행하고 같은 quality rubric으로 pair 채점

### 이름을 통한 정답 노출 방지

기존 Agent payload에는 `scenario_fixture_id`와 도구 응답 provenance의 `fixture_id`가 들어 있습니다.
예비 실행의 `TracedBlindClient`는 이 이름을 조사별 무작위 `case-<opaque ID>`로 바꿔 모델에 전달합니다.
인코딩된 JSON history와 repair history에도 같은 alias를 적용합니다.
관측값·semantic flag·tool 이름·canonical evidence ID는 유지합니다.
기대 가설·branch expectation이 request에 섞이면 API 호출 전에 거부합니다.
로컬 artifact는 정합성 재검증을 위해 실제 fixture ID와 원본 evidence를 유지합니다.

### 실행과 결과 해석

```powershell
.venv\Scripts\python.exe scripts\run_diagnosis_pilot.py --live `
  --output-dir results\ops-agent\quality-evaluation\step3-live-pilot-01
```

결과 폴더의 `manifest.json`은 `running`, `completed`, `aborted`를 구분합니다.
`pilot-<repeat>-<scenario>/`는 요청·응답 trace와 개별 상태·실패·채점,
`live-attempts.json`과 `fixed-runbook-attempts.json`은 원본 batch,
`live/`와 `fixed-runbook/`는 같은 importer/scorer의 재채점 결과입니다.
`summary.json`과 `report.md`는 실행·미실행 수와 pair 결과를 보존합니다.
품질 실패가 하나라도 있으면 exit `1`, API/runtime 중단은 exit `2`입니다.

Contract 통과율과 quality 통과율은 출력 계약과 현재 rubric을 모두 포함한 시스템 평가입니다.
Contract 실패를 실제 원인 추정의 오답으로 일괄 해석하지 않습니다.
실패한 자연어·structured output은 trace에서 사람이 검토할 수 있습니다.
최종 실패·repair를 제외하고 성공 artifact만 평가하는 집계는 사용하지 않습니다.

이 입력들은 기존 개발용 네 시나리오입니다. 반복 8회로 새 장애 유형의 일반화·production 정확도·복구 시간 개선을 주장하지 않습니다.
LLM elapsed는 API/network/repair를 포함하고 Runbook elapsed는 로컬 함수 실행만 포함합니다.
시간 개선율과 금액 환산 비용은 계산하지 않습니다. 응답이 없는 요청의 usage는 unknown으로 두고 coverage를 별도 기록합니다.

API 계약 참고: [Function calling](https://developers.openai.com/api/docs/guides/function-calling),
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
현재 prompt/validator/rubric을 실행 중 조정하지 않으며 개선 후 재평가는 별도 run으로 보존합니다.

### 2026-10-29 실제 예비 실행 결과

로컬 원본: `results/ops-agent/quality-evaluation/step3-live-pilot-20261029/`.
공유용 [축약 결과와 원본 hash](../results/ops-agent/diagnosis/pilot-20261029-summary.json)는 Git 추적 대상으로 유지합니다.
전체 prompt·response trace는 로컬에 보관합니다.

| 항목 | 실제 LLM | 고정 Runbook |
| --- | ---: | ---: |
| 예정 / 실행 / 미실행 | 8 / 8 / 0 | 8 / 8 / 0 |
| 최종 contract 통과 | 0/8 | 8/8 |
| 현재 rubric의 통합 quality 통과 | 0/8 | 8/8 |
| Repair 사용 | 8/8 | 0/8 |
| 관측 tool result 수 | 31 | 32 |
| API 요청 | 47 | 0 |
| Input / output tokens | 85,942 / 11,837 | 0 / 0 |
| Usage coverage | 전체 47개 응답 확보 | 해당 없음 |

LLM 조사·repair 포함 elapsed 합 `149.04s`, 평균 `18.63s`입니다.
시간 개선율과 금액 비용은 미측정입니다. 출력은 JSON으로 파싱됐지만 애플리케이션의 의미 계약을 통과하지 못했습니다.
최종 실패는 `REBALANCE_GAP_REQUIRED` 7회, `CONFLICTED_REQUIRES_BOTH_CITATION_SIDES` 1회입니다.
첫 오류에는 `INSUFFICIENT_REQUIRES_EVIDENCE_GAP`도 있었고 repair 1회로 모든 위반이 해결되지 않았습니다.

사후 raw 출력 검토에서 supported causal 가설 집합의 rubric exact match는 `4/8`입니다.
이 값은 계약·인용 의미를 통과한 진단 정확도가 아니며 exploratory label agreement로만 보존합니다.
DB 저하 2회는 PostgreSQL path와 Worker path를 함께 supported로 표시했습니다.
관측 부족 2회는 Worker path를 supported로 유지해 현재 rubric의 보류 기대값과 불일치했습니다.
Raw 검토는 source state hash와 함께 축약 결과에 보존합니다.

### 이번 결과의 판단과 다음 Gate

- 현재 live prompt는 `CONFLICTED`의 양쪽 인용 조건과 rebalance의 필수 gap 코드 의미를 충분히 전달하지 않는 상태
- 실제 모델의 출력 계약 준수·관측 부족 시 보류와 제한된 tool budget 안의 조사 선택 보강 필요
- 현재 네 조건에서는 고정 Runbook의 품질 통과 확인; AI 이점 주장 보류
- 이번 8회 실패 기록 유지; 검사 완화·자동 교정·성공 결과만 선별한 재집계 제외
- 후속은 prompt에 공통 output/capability 계약 명시와 offline repair 검증부터 진행; 시나리오별 정답이나 기대 경로 주입 제외
- Prompt 변경·추가 live 실행은 별도 버전/run으로 진행; 이번 실행 중 변경·추가 요청 없음
- API 사용 기록 `results/validation/20261029T120252Z-b7253f17/`: 기대대로 quality 실패 exit `1`, batch는 8회 완료
- 오프라인 검증 `38 passed in 3.15s`, 전체 suite `717 passed in 29.12s`; source 실행 기록 `results/validation/20261029T120307Z-866eda7e/`

## 2026-10-29 Step 2 — 고정 Runbook 비교군

[fixed_runbook.py](../ops_agent/fixed_runbook.py)는 네 도구를 매번 같은 순서로 조회하는
`fixed-checklist.v1` 비교군입니다. 순서는 stage latency → PostgreSQL → replica → KEDA입니다.
네 조회가 끝난 뒤 획득한 normalized evidence에만 규칙을 적용합니다.

| 관측 조건 | 가설 |
| --- | --- |
| Fresh/OK stage 상승 + PostgreSQL 저하 | PostgreSQL path degraded |
| Fresh/OK stage 정상 + PostgreSQL 정상 + replica 부족 + KEDA active | Worker capacity shortfall |
| Fresh/OK stage 상승 + PostgreSQL 정상 + replica 정상 | Worker path pressure |
| 필수 관측 누락·stale·규칙에 맞지 않는 조합 | 판단 보류와 gap 기록 |

실행 함수는 activation과 tool registry만 받습니다. Fixture 정답·title·branch expectation·rubric을
조사 규칙에 전달하지 않습니다. 같은 registry가 반환한 관측값을 사용하며 expected label은 채점 단계에만 사용합니다.
이 baseline은 전체 체크리스트 한 가지입니다. 모든 규칙 기반 조사 방식의 최적 성능을 대표하지 않습니다.
기존 recorded 조사도 관측에 따라 분기하는 scripted policy이므로 현재 비교는 두 deterministic 절차의 점검입니다.

### 동일 조건과 실행

- 공통 catalog·normalizer·9개 tool allowlist·initial activation·quality rubric 사용
- 양쪽 최대 `4 steps / 4 tool calls`; 입력 replay의 제한이 다르면 실행 전 거부
- Runbook은 항상 4개 조회, recorded 조사는 관측에 따라 조기 종료 가능
- Runbook decision을 기존 `recorded` v2 artifact로 변환; `model=fixed-checklist.v1`, synthetic decision turn `1`, API 요청·token `0`
- 각 scenario의 두 결과를 pair로 보존; 완료 artifact가 잘못된 입력도 해당 arm의 분모에 유지
- 시작 manifest와 입력 replay 원본, baseline batch, 두 arm의 개별 평가·source/코드 hash·비교표 보존

```powershell
.venv\Scripts\python.exe scripts\compare_diagnosis_runbook.py `
  --output-dir results\ops-agent\quality-evaluation\step2-runbook-01
```

출력은 매번 새 디렉터리에 저장합니다. `report.md`는 시나리오별 비교,
`comparison.json`은 두 arm의 집계와 pair, `runbook-attempts.json`은 독립 재채점 가능한 baseline batch입니다.
`fixed-runbook/`과 `scripted-investigation/`은 Step 1과 같은 importer/scorer로 생성합니다.
비교가 끝난 `completed` 상태와 quality 통과는 분리하며, 어느 arm이든 quality 실패 시 exit `1`입니다.

### 2026-10-29 로컬 결과

| 항목 | 고정 Runbook | 기존 scripted 조사 |
| --- | ---: | ---: |
| 실행 수 | 4 | 4 |
| Contract / quality 통과 | 4 / 4 | 4 / 4 |
| 총 도구 호출 | 16 | 10 |
| Rubric 필수 집합 밖 호출 | 6 | 0 |
| 실제 LLM 호출 | 0 | 0 |

규칙만으로 현재 네 controlled scenario의 기대 가설·판단 보류 기준을 만족했습니다.
호출 차이는 고정 전체 체크리스트와 관측 기반 scripted 분기의 차이입니다.
이를 LLM의 정확도 향상·호출 절감 성과로 인용하지 않습니다.
고정 Runbook elapsed는 로컬 함수 실행 시간이며 모델의 추론·network 시간을 포함하지 않습니다.
기존 replay의 합성 시각은 latency 미측정으로 유지하고 시간 개선율은 계산하지 않습니다.

- 로컬 비교: `results/ops-agent/quality-evaluation/step2-runbook-20261029/`
- 실행 기록: `results/validation/20261029T115246Z-90092247/`
- 새 비교군과 기존 평가기 대상 테스트 최초 실행: `29 passed in 1.28s`; 이후 상충 관측 보류 테스트 추가
- 상충 관측 보류 테스트 포함 전체 suite: `709 passed in 30.87s`; `results/validation/20261029T115248Z-94738b66/`
- 문서 링크 검사 오류 `0`, diff 공백 검사 통과
- 실제 모델 예비 실행과 별도 평가 입력은 후속 범위; 모델 평가 전에 프롬프트의 의미 있는 scenario ID 노출도 점검 필요
- dev-kafka 공통 도구·문서 변경; master·demo 브랜치 공유와 commit/push/merge는 후속 승인 범위

## 2026-10-29 Step 1 — 오프라인 평가 기반

기존 Scenario Lab의 저장된 `ops.diagnosis.v2` 기록을 고정 rubric으로 채점합니다.
현재 단계의 목적은 채점기와 실패 집계 검증입니다. 실제 LLM의 정확도·비용·생산성 비교는 후속 실험에서 측정합니다.

- 작업 브랜치: `dev-kafka`; master·demo 브랜치 전파는 후속 승인 범위
- 입력: 기존 controlled scenario 4개, tracked scripted replay
- 실행: 모델 API·credential loading·Kubernetes 접근 없이 로컬 파일만 사용
- 범위: 독립 채점기·rubric·저장된 실행 import; 기존 Agent prompt·tool loop·UI 변경 제외
- 후속: 고정 Runbook 비교군 → 실제 모델 예비 실행 → 별도 평가 입력에서 본 실험

## 채점 계약

Rubric은 [quality_rubric_v1.json](../ops_agent/fixtures/diagnosis/quality_rubric_v1.json),
채점기는 [diagnosis_quality.py](../ops_agent/diagnosis_quality.py)에 있습니다.
카탈로그 전체와 rubric의 canonical SHA-256, scorer version을 평가 identity로 기록합니다.
기대 가설·필수 도구·핵심 supporting citation을 rubric에 고정하며 실행 중 변경하지 않습니다.

| 시나리오 | 기대 supported 가설 | 필수 조사 도구 | 핵심 supporting citation |
| --- | --- | --- | --- |
| Worker DB-path pressure | Worker path pressure | stage, PostgreSQL, replica | stage |
| Worker replica shortfall | Worker capacity shortfall | stage, replica, KEDA | replica |
| PostgreSQL path degradation | PostgreSQL path degraded | stage, PostgreSQL | PostgreSQL |
| Telemetry unavailable | supported causal 가설 없음, gap 기록 | stage, PostgreSQL | 없음 |

`contract_pass`는 기존 v2 schema·integrity와 output validator 재검증 결과입니다.
고정 activation과 context, tool별 evidence 전체를 catalog에서 재생성해 비교합니다.
저장된 `VALID` 표시만으로 계약 통과를 인정하지 않습니다.

`quality_pass`는 아래 항목을 모두 만족한 실행입니다.

- Supported causal 가설 집합과 rubric 기대값 일치
- 관측 부족 시 supported causal 가설 보류와 evidence gap 기록
- 필수 도구의 evidence 확보; 호출 순서 독립
- 기대 가설의 핵심 supporting citation 확보
- 각 supporting/conflicting citation과 제한된 의미 관계 규칙 일치

지정 경로의 일치 여부는 실제 step 순서로 다시 계산한 `strict_branch_pass`로 별도 보존합니다.
필수 evidence를 다른 순서로 확보한 실행은 quality에서 통과할 수 있습니다.
필수 집합 밖 호출은 `extra_tool_call_count`로 기록하며 자동 품질 실패 조건에 포함하지 않습니다.

### 인용 의미 검사의 범위

의미 규칙은 normalized stage baseline, PostgreSQL guardrail, replica gap, KEDA active 정보와
세 가설의 지지·반박 관계만 다룹니다. 예를 들어 정상 PostgreSQL 상태는 DB 경로 저하의
supporting citation으로 인정하지 않습니다. 누락·stale evidence도 지지 근거로 인정하지 않습니다.
Stage 상승은 DB 가설의 보조 근거로 허용하지만 PostgreSQL 핵심 citation을 별도로 요구합니다.

정의되지 않은 의미 관계는 `unscored_citation_count`에 보존하고 quality 통과에서 제외합니다.
이는 사람이 검토할 후보이며, 모든 인용을 자동으로 오답 확정하는 지표로 사용하지 않습니다.
초기 lag ID만으로 개별 원인 가설을 지지하는 경우도 의미 관계 미정으로 처리합니다.
이 rubric은 네 controlled scenario에 한정된 후보 기준입니다. 자연어 reason의 전체 의미,
실제 장애의 causal truth, 새로운 장애 유형에 대한 일반화는 검증 범위 밖입니다.

## 로컬 실행

저장된 scripted replay 4개 채점:

```powershell
.venv\Scripts\python.exe scripts\evaluate_diagnosis_quality.py `
  --output-dir results\ops-agent\quality-evaluation\step1-replay-01
```

출력 디렉터리는 매번 새 이름을 사용합니다. 기존 디렉터리 지정 시 덮어쓰기 없이 실패합니다.
전체 결과는 기존 ignore 정책에 따라 로컬에 보관합니다.

| 파일 | 내용 |
| --- | --- |
| `manifest.json` | 시작·완료 시각, scheduled count, source commit/dirty 상태, 코드 hash, 평가 identity |
| `source.json` | 입력 파일 원본; source SHA-256와 연결 |
| `catalog.json`, `rubric.json` | 채점 시점 입력·기준 snapshot |
| `attempt-NNN.json` | 각 실행 원본과 개별 채점·citation 관계 |
| `summary.json`, `report.md` | 전체 시도 집계와 읽기용 요약 |

Manifest의 `running`은 미완료입니다. 정상 종료한 `completed`도 quality 통과와 별개입니다.
모든 quality 통과 시 exit `0`, 품질 실패·완료 artifact 오류·기록된 실행 실패 포함 시 exit `1`입니다.
입력 설정 오류는 집계 전 실행을 중단합니다.

## 실패·repair·시간·token 집계

`--attempts <batch.json>`은 아래 형식의 이미 저장된 실행들을 읽습니다.
`evaluation_identity`는 같은 catalog/rubric의 manifest 값과 일치해야 합니다.
Batch의 각 항목은 `QualityAttempt` 계약을 따르며 `run` 또는 `failure_code` 중 하나를 요구합니다.

```json
{
  "schema_version": "ops.diagnosis.quality-attempts.v1",
  "evaluation_identity": {
    "catalog_sha256": "<manifest의 값>",
    "rubric_sha256": "<manifest의 값>",
    "scorer_version": "ops.diagnosis.quality.v1"
  },
  "attempts": [
    {
      "attempt_id": "trial-001",
      "fixture_id": "worker-db-path-pressure",
      "execution_kind": "scripted",
      "failure_code": "TEST_FAILURE",
      "responses": [{"output_text": "sanitized invalid response"}]
    }
  ]
}
```

- 분모: batch의 모든 시도; 성공 artifact가 없는 실패도 포함
- 최초 통과율: 전체 시도 중 최초 contract 통과가 확인된 비율; 최초 결과 미확인은 `first_pass_unknown_count`로 별도 보존
- Repair: 완료 artifact의 validation attempt·repair 수 기록; repaired contract success와 최초 성공 분리
- 최종 contract failure: 실패 envelope와 유효하지 않은 완료 artifact 모두 포함
- 시간: 명시적으로 측정해 전달한 `elapsed_seconds`만 집계; 합성 replay timestamp 제외
- Token: 유효한 완료 artifact의 usage, 실패 envelope의 명시된 usage만 집계; 누락값은 unknown과 known count로 표시
- 비용: 가격 환산·절감률 미측정; token을 금액으로 표현하는 기능 제외
- Scripted/live: 별도 batch와 요약; 혼합 집계 거부

현재 importer는 모델을 실행하거나 실패를 자동 수집하지 않습니다. 향후 실행기는 예정한 모든 시도를
사전 등록하고 원본 응답·repair·실패·측정값을 위 입력으로 남겨야 합니다. Import한 batch에
누락된 실행을 채점기가 찾아낼 수는 없습니다. 응답은 credential-free sanitized artifact만 사용합니다.

## 검증과 해석

2026-10-29 로컬 검증:

- 평가기 테스트: `19 passed in 0.82s`; `results/validation/20261029T114607Z-cadebc91/`
- 전체 회귀: `698 passed in 41.55s`; `results/validation/20261029T114745Z-07db04df/`
- Scripted replay: `4/4` contract·quality 통과, repair `0`, API token `0`, elapsed 미측정
- 최종 replay 실행 기록: `results/validation/20261029T114822Z-11858d4b/`
- 최종 로컬 출력: `results/ops-agent/quality-evaluation/step1-final-20261029/`
- 문서 링크 검사: 오류 `0`

기존 scripted replay의 4/4 통과는 채점기 점검입니다. 실제 모델의 진단 정확도 수치로 사용하지 않습니다.
테스트는 정상 ID의 잘못된 인용, 핵심 citation 누락, evidence 변조, 잘못된 fixture/context,
필수 도구 누락, 대체 조사 순서, 관측 부족의 잘못된 확신, repair 후 성공과 실패의 집계,
network-free 실행과 결과 덮어쓰기 방지를 검증합니다.

후속 평가에서는 기존 입력 반복에 따른 일관성과 새 조건의 대응력을 분리합니다.
경계값·상충 증거·일부 누락 입력을 추가할 때는 지표 정합성과 기대 가설을 먼저 검토하고,
프롬프트 조정에 사용하지 않을 평가 입력을 별도로 남깁니다.
