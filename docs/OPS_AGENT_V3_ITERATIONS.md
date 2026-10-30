# V3 모델 출력 반복 개선

2026-10-29 사용자 요청: 같은 모델이 고정된 v3 평가에서 8/8이 될 때까지 개선·재평가 반복.
종료 기준은 **한 번의 완전한 8회 batch에서 종합 8/8**입니다. 서로 다른 회차의 성공 사례를 골라 합치지 않습니다.
이 기준은 development set에서 처음 8/8이 나온 회차를 선택하는 절차입니다. 일반화 정확도나 반복 안정성의 추정값으로 사용하지 않습니다.

## 실제 결과

첫 수정 회차 `ops.diagnosis.execution.v3.2`에서 최종 종합 **8/8**을 달성해 반복을 종료했습니다.
최초 contract는 7/8입니다. PostgreSQL 사례 1회에서 동일한 replica evidence를 context와 signed conflict에 함께 넣었고,
기존 repair 제한 안에서 context 중복을 제거해 통과했습니다. 관측·가설명·지지 상태 변경이나 새 인용은 없었습니다.

| 차원 | 이전 v3.1 | 첫 수정 v3.2 | 고정 Runbook |
| --- | ---: | ---: | ---: |
| 최초 contract / evidence roles | 6/8 | 7/8 | 8/8 |
| 최종 contract / evidence roles | 6/8 | 8/8 | 8/8 |
| 관측 | 5/8 | 8/8 | 8/8 |
| 원인 후보·보류 | 6/8 | 8/8 | 8/8 |
| 조사 완결성 | 8/8 | 8/8 | 8/8 |
| 종합 | 5/8 | 8/8 | 8/8 |
| Repair | 2 | 1 | 0 |
| 도구 호출 | 32 | 30 | 32 |

추가 실제 실행은 이 8회 batch 한 번입니다. 모델 API 39회, input/output `105,858 / 6,106` tokens, usage 전체 확보입니다.
API/network·조사·repair 포함 elapsed 합계 `101.58s`, 평균 `12.70s`입니다.
도구 호출은 2회 줄었지만 input tokens는 늘었습니다. 비용 절감이나 통계적인 속도 개선은 주장하지 않습니다.
고정 Runbook도 8/8이므로 이번 성공만으로 LLM의 추가 가치를 입증하지 않습니다.

최초 결과와 수정 회차는 동일 rubric입니다. Manifest 비교에서 모델·budget·schedule·catalog·rubric·blinding이 일치하고,
관련 구현 중 `ops_agent/scenario_agent_v3.py`의 실행 지시만 변경된 것을 확인했습니다.
Decision contract·validator·scorer와 full canonical evidence를 재검증한 뒤 개별 점수·summary를 다시 계산해 일치를 확인했습니다.
최초 결과의 실패와 이번 1회 repair를 모두 보존했습니다.

- [실제 8회 축약 결과·개별 decision·원본 hash](../results/ops-agent/diagnosis/pilot-v3-iteration-r1-20261029-summary.json)
- Live 원본: `results/ops-agent/quality-evaluation/step9-v3-iteration-20261029/round-01/`
- Live 실행 로그: `results/validation/20261029T132649Z-8ddec7ab/`, exit `0`
- 추가 유료 호출 없이 종료, runtime 배포·commit/push/merge 없음

## 고정 범위

- 요청 모델·기존 key·reasoning low·output 상한 1,600 tokens 유지
- 같은 frozen catalog의 네 fixture × 2회·이름 가리기 유지
- 도구 최대 4회·repair 최대 1회·batch 최대 API 48회·transport retry 0 유지
- V3 decision schema·validator·scorer·rubric·고정 Runbook 유지
- 모델 원본 출력 보존; 관측 자동 삽입·가설 교체·정답 변환 제외
- 실패 회차·개별 원본·prompt/source hash 보존, 회차 시작 전 변경과 목표 기록

Rubric canonical SHA-256: `fb4e24dd6f6dcce6bab539ff0c06a1153e5a1f49cef99e800b29975de4fe867b`.
Catalog canonical SHA-256: `f567404085065895321319d9525f9f1b8db5ecfebe290f6592d61c8dddde7900`.

## 첫 수정

기존 v3.1은 종합 5/8입니다. Stage 상승을 replica shortfall의 원인 지지로 인용한 실패 2회와 replica 관측 항목 누락 1회가 있었습니다.
기존 결과와 실행 전 소스는 로컬 campaign의 `source-before/`에 보존했습니다.

`ops.diagnosis.execution.v3.2`는 다음 공통 지시를 추가합니다.

- Validator에 이미 정의된 tool·semantic flag와 관측명·signed causal 역할의 대응표
- 획득한 Fresh/OK 양성 신호를 빠짐없이 observations에 보고하는 규칙
- 같은 evidence를 가설에 사용해도 observation을 유지하는 규칙
- 직접 근거가 없는 가설의 선택적 생략, gap을 보존한 ABSTAIN
- Stage·PostgreSQL 조사와 replica·KEDA 후속 조사의 일반 운영 절차
- 네 도구 호출 완료 뒤 별도 API 요청 없이 제공하는 최종 자기 점검 지시

Fixture ID·정답 label·개별 예시·rubric을 prompt에 넣지 않습니다. 일반적인 보고 규칙과 조사 절차는 모델에 명시적으로 제공합니다.
도구 순서와 최종 decision은 모델이 생성합니다. Repair에서 가설 상태를 고정하는 기존 제약도 유지합니다.
모델이 관측을 누락하면 그대로 채점 실패가 되는 회귀 테스트를 추가했습니다.

## 검증과 원본

- 관련 오프라인 검사 `40 passed in 1.57s`; `results/validation/20261029T132632Z-44834bfe/`
- 전체 suite `775 passed in 30.02s`; `results/validation/20261029T132649Z-e01c8901/`
- Campaign: `results/ops-agent/quality-evaluation/step9-v3-iteration-20261029/`
- 사전 기준·회차 변경·결과: campaign의 `campaign.json`
- 기존 v3.1 조건과 실패: [OPS_AGENT_V3_EVALUATION.md](OPS_AGENT_V3_EVALUATION.md)

개선된 출력이 나오더라도 동일한 네 사례를 반복해서 검토한 결과입니다. 별도 holdout과 반복 실행을 통해 적용 범위를 추가 검증할 수 있습니다.
