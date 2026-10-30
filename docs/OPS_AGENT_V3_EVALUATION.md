# Ops Agent v3 실행·평가

후속 반복에서 같은 rubric의 최종 8/8 달성: [v3.2 실행 지시 개선·실제 결과](OPS_AGENT_V3_ITERATIONS.md).
아래는 그 이전 v3.1의 조건과 실패를 보존한 기록입니다.

2026-10-29 관측·원인 후보·문맥을 구분하는 v3 decision의 독립 실행 경로입니다.
기존 v2 validator, rubric과 저장된 점수는 유지합니다. Public replay와 클러스터 runtime에는 연결하지 않습니다.

## 실행 전에 고정한 기준

네 development fixture에 대해 각 2회, 총 8회를 실행합니다. 기존 설정의 모델·API key를 재사용하며 fixture 이름을 임의 ID로 가립니다.
실제 클러스터 대신 digest를 검증한 frozen catalog가 normalized evidence를 제공합니다. Freshness는 해당 snapshot 시각 기준입니다.
모델은 초기 activation과 공통 지시, 선택한 도구의 결과만 받습니다. Rubric·작성 예시·정답 label·기대 경로는 모델 입력에서 제외합니다.

| 제한 | 기준 |
| --- | --- |
| 도구 | allowlist의 인자 없는 read-only 도구, turn당 1개, 중복 금지, 최대 4개 |
| 모델 | 기존 설정 모델, reasoning low, output 상한 1,600 tokens |
| Repair | 최대 1회, 도구 추가 금지, 관측명·가설명·지지 상태 유지, 기존 출력에 없던 citation 추가 금지 |
| API | 시도당 최대 6회, 전체 최대 48회, transport retry 0, timeout 30초 |
| 실패 | 실행된 시도 분모에 포함, 통신 실패 시 남은 schedule을 미실행으로 분리 |
| 비교군 | 동일 evidence registry에서 stage → PostgreSQL → replica → KEDA 고정 조회, 일반 evidence rule로 v3 decision 생성 |

Repair는 새 조사를 수행하지 않습니다. 최초 출력과 최종 출력의 유효성을 따로 보존합니다.
인용 역할 검사에서 실패한 출력도 남기며, 통신 오류는 예외 종류만 기록합니다.

## 채점 차원

모든 실행 시도가 각 차원의 분모에 들어갑니다. 먼저 activation hash, 도구 제한과 full canonical evidence를 다시 검증합니다.
Contract 실패 시 관측·원인 점수는 실패로 유지하고, 조사 완결성은 획득한 도구 목록으로 별도 평가합니다.

| 차원 | 통과 기준 |
| --- | --- |
| Contract / evidence roles | JSON schema, 실제 존재하는 citation, 직접 측정한 observation, 가설별 signed 근거, 보류·gap·권한 경계 만족 |
| 관측 | fixture에서 직접 측정 가능한 양성 signal 집합과 정확히 일치 |
| 원인 후보·보류 | 직접 근거가 있는 supported 후보 집합과 정확히 일치; 빈 집합은 gap을 보존한 ABSTAIN |
| 조사 완결성 | 사전에 지정한 필수 도구 조회 완료 |
| v3 종합 | 위 네 차원 모두 통과 |

| Fixture | 기대 관측 | Supported 후보 | 필수 도구 |
| --- | --- | --- | --- |
| worker-db-path-pressure | stage 상승 | 없음, ABSTAIN | stage·PostgreSQL·replica |
| worker-replica-shortfall | replica 부족 | Worker capacity shortfall | stage·replica·KEDA |
| postgres-path-degradation | stage 상승·PG guardrail 저하 | PG guardrail pressure | stage·PostgreSQL |
| telemetry-unavailable | stage 상승 | 없음, ABSTAIN | stage·PostgreSQL |

Replica 부족 fixture의 stage는 정상 범위입니다. Stage 상승을 기대값에 넣었던 초안은 오프라인 테스트에서 실패해 실제 호출 전에 수정했습니다.
모든 사례에서 ABSTAIN을 선택하면 원인 근거가 있는 두 fixture에서 실패합니다. KEDA 누락은 조사 완결성 실패로 집계합니다.
KEDA 상태와 초기 lag는 문맥 역할이며, 직접 원인 지지 근거로 승격하지 않습니다.

V3에서는 stage 상승을 관측으로, PostgreSQL 후보를 HA·replication guardrail 범위의 의심으로 표현합니다.
V2 quality와 vocabulary·평가 목표가 달라 이전 `1/8`과 이번 종합 점수의 향상률을 계산하지 않습니다.
고정 비교군은 모델 없이 evidence rule을 실행합니다. LLM이 이 작업에 필요한지 판단하는 기준으로 함께 보존합니다.

## 재현과 원본

```powershell
.venv\Scripts\python.exe scripts\run_diagnosis_v3_pilot.py --live `
  --output-dir results\ops-agent\quality-evaluation\v3-new-run
```

실제 API 비용이 발생하는 명령입니다. 새 디렉터리만 허용합니다.
첫 요청 전에 schedule, execution contract/schema, catalog/rubric snapshot과 hash, source commit·dirty status·관련 파일 hash를 기록합니다.
각 turn의 blinded request·response, 개별 실행·검증 실패·usage, 고정 비교군 원본, 전체 summary를 보존합니다.
기존 prompt candidate hash와 v3 실행 지시의 hash는 분리합니다.

- 실행기: [scenario_agent_v3.py](../ops_agent/scenario_agent_v3.py)
- 채점·고정 비교군: [diagnosis_quality_v3.py](../ops_agent/diagnosis_quality_v3.py)
- 사전 rubric: [quality_rubric_v3.json](../ops_agent/fixtures/diagnosis/quality_rubric_v3.json)
- Pilot: [run_diagnosis_v3_pilot.py](../scripts/run_diagnosis_v3_pilot.py)
- 기존 출력 후보와 의미 변경: [OPS_AGENT_V3_CANDIDATE.md](OPS_AGENT_V3_CANDIDATE.md)

## 해석 범위

반복 사용한 네 development fixture의 작은 예비 평가입니다. 독립 holdout, 실시간 source freshness, 실제 incident 원인 정답과 production 운영 효과는 별도 검증 대상입니다.
Supported는 관측에 근거한 후보이며 root cause 확정이 아닙니다. 비교군의 로컬 실행 시간과 모델의 API/network 포함 시간을 속도 개선율로 사용하지 않습니다.
현재 네 사례에서 LLM의 추가 가치는 확인되지 않았습니다. 고정 비교군이 더 일관된 결과를 냈으며 모델은 도구 호출 수를 줄이지 못했습니다.

## 실제 모델 실행 결과

요청 모델 `gpt-5.6-luna`, 8회 완료·미실행 0회입니다. 응답 모델 snapshot 이름은 현재 client가 기록하지 않습니다.
실행 전에 고정한 rubric·validator·prompt를 실행 중 변경하지 않았습니다.

| 차원 | 실제 모델 | 고정 Runbook |
| --- | ---: | ---: |
| 최초 contract / evidence roles | 6/8 | 8/8 |
| 최종 contract / evidence roles | 6/8 | 8/8 |
| 관측 | 5/8 | 8/8 |
| 원인 후보·보류 | 6/8 | 8/8 |
| 조사 완결성 | 8/8 | 8/8 |
| V3 종합 | 5/8 | 8/8 |
| 도구 호출 합계 | 32 | 32 |

관측·원인 점수는 contract 실패 2회를 실패로 포함한 수치입니다. 실제 관측 항목 누락은 별도로 1회입니다.
Repair 2회 모두 최종 실패입니다. API 요청 42회, input/output `95,720 / 9,861` tokens, usage 전체 확보입니다.
조사·repair·API/network를 포함한 시도별 elapsed 합계 `142.51s`, 평균 `17.81s`입니다.
과금 금액과 모델 간 가격·속도 비교는 계산하지 않았습니다.

| 실패 시도 | 관측과 실패 경계 |
| --- | --- |
| pilot-1-1 / worker-db-path-pressure | stage 상승을 replica shortfall 후보의 지지 근거로 인용; repair에서 지지 인용 제거 후 CONFLICTED의 양측 근거 조건 실패 |
| pilot-2-2 / worker-replica-shortfall | 원인 후보·직접 지지는 정확, observations에서 replica 부족 신호 누락 |
| pilot-2-4 / telemetry-unavailable | stage 상승을 replica shortfall 후보 지지로 인용; repair에서도 동일 인용 유지 |

이번 repair는 가설의 지지 상태 변경을 금지합니다. 이 제한은 첫 실패의 CONFLICTED를 INSUFFICIENT로 바꾸는 수정도 막습니다.
기준을 실행 후 완화하지 않았습니다. 후속 실험에서는 semantic 오류 거부와 형식 수정의 책임 범위를 사전에 다시 정할 수 있습니다.
관측 필드를 normalized evidence에서 결정적으로 생성하고 LLM을 조사 선택·해석에 한정하는 방식도 검토 대상입니다.
재사용 fixture에서 얻은 이번 결과만으로 이러한 후속 설계의 효과를 주장하지 않습니다.

- 원본: `results/ops-agent/quality-evaluation/step8-v3-live-20261029/`
- 실행 로그: `results/validation/20261029T131900Z-21c502d6/`; 종합 실패를 보존한 exit `1`
- [축약 결과·시도별 출력·원본 hash](../results/ops-agent/diagnosis/pilot-v3-20261029-summary.json)
- Execution identity: `ops.diagnosis.execution.v3.1`, `187ebeaceff98439b2b03292d8520d09ef61b503f582fd16a1a6a73570d6d17e`
- Rubric canonical SHA-256: `fb4e24dd6f6dcce6bab539ff0c06a1153e5a1f49cef99e800b29975de4fe867b`
- Export 전 frozen acquisition 재생성·개별 점수·전체 summary·source hash 일치 확인
- 기존 v2 prompt·구현 hash 불변 확인; runtime 배포·commit/push/merge 없음

## 오프라인 검증

- 새 loop·repair·채점·wire trace·보류·조사 완결성 관련 테스트 16개 통과
- 전체 suite `773 passed in 27.96s`; `results/validation/20261029T131757Z-926357ff/`
- 원본 재채점·export·문서 숫자 검사 보강 뒤 관련 검사 `49 passed in 1.37s`; `results/validation/20261029T131845Z-aa7b1198/`
- 문서 stale-count 검사를 정확한 `44`·`56` 숫자에 한정, `756`에 대한 부분 문자열 오탐 수정·회귀 검증
- 실제 실행·결과 문서 반영 후 최종 전체 suite `773 passed in 30.57s`; `results/validation/20261029T132231Z-d1d8f382/`
- 최종 문서·JSON 검사 오류 `0`; `results/validation/20261029T132303Z-63da7dad/`
