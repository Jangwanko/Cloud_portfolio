# V3.4 판단 종료 전 최소 조사

후속 evidence ID 선택 제한과 16회 결과: [V3.5 제공된 ID 선택](OPS_AGENT_V3_CITATION_SELECTION.md). 아래 v3.4의 15/16 결과는 이전 기록으로 유지합니다.

2026-10-29 복합 장애에서 PG만 확인한 뒤 종료한 v3.3 회귀 실패를 바탕으로 최소 조사 범위를 코드 정책으로 고정했습니다.
코드가 최소 조사 범위를 보장하고 모델이 허용된 범위에서 도구 순서와 최종 관측·가설을 생성합니다.

## 종료 조건과 선택 범위

| 조건 | 반드시 시도해야 하는 조회 |
| --- | --- |
| 모든 incident | Worker stage latency·PostgreSQL health·Worker replica status |
| Fresh/OK replica에 WORKER_CAPACITY_SHORTFALL | KEDA status 추가 |
| Replica가 stale 또는 unavailable | KEDA는 선택 사항, 부족 상태의 직접 근거로 승격 제외 |

조회 결과가 UNAVAILABLE/ERROR여도 조회 시도는 완료로 집계합니다. 해당 신호는 정상 상태나 양성 원인 근거로 사용하지 않습니다.
최대 4개 도구·중복 금지·turn당 1개·최종 decision·repair 최대 1회·시도당 API 최대 6회는 유지합니다.

필수 조회가 남아 있으면 남은 필수 도구들만 모델에 제시하고 `tool_choice="required"`, `parallel_tool_calls=false`를 사용합니다.
필수 조회 완료 뒤에는 남은 예산에서 optional 도구 또는 최종 decision을 선택할 수 있습니다.
이 API 선택 방식은 [OpenAI 공식 function calling 문서](https://developers.openai.com/api/docs/guides/function-calling)에 따른 설정입니다.

API가 필수 조회 중 최종 JSON을 반환하더라도 caller는 `V3_MINIMUM_INVESTIGATION_INCOMPLETE`로 거부합니다.
추가 요청·자동 도구 실행·가짜 tool result·출력 보정 없이 실패 원본을 남깁니다.
필수 범위 밖 도구를 요청하거나 남은 예산으로 필수 조회를 채울 수 없는 경우도 거부합니다.
각 turn의 pending tool·offered tool·tool choice를 `investigation_audit`에 기록합니다.

최소 범위는 [investigation_gate_v3.py](../ops_agent/investigation_gate_v3.py)의 일반 정책입니다.
Fixture ID·정답·rubric을 읽지 않고 이미 획득한 canonical evidence의 status·freshness·semantic flag만 사용합니다.
Stage·PG·replica의 호출 순서는 고정하지 않습니다. Fresh replica 부족을 먼저 확인했다면 다른 primary 도구와 KEDA 중에서 다음 조회를 선택할 수 있습니다.

## 변경·유지 범위

- Execution identity `ops.diagnosis.execution.v3.4`, 최소 조사 정책 `ops.diagnosis.investigation.v1`
- Model·decision schema·직접 근거 validator·quality scorer·catalog/rubric·normalizer·repair 권한 유지
- 종료 가능 조건·제시 도구 필터·API 선택 제약·관련 공통 지시 변경
- Controller가 보장하는 coverage와 모델의 관측·인용·원인 추론 품질을 분리
- 기존 v3.3의 최초 `14/16`·최종 `15/16` 점수 보존

## 검증 절차

오프라인 검사는 초기 필수 조회, fresh/stale deficit의 분기, 조회 실패 후 coverage, 다른 도구 순서,
조기 최종 JSON 거부, 필수 조회 중 optional 도구 실행 거부, 예산·repair 제한 유지를 포함합니다.
기존 quality scorer는 그대로 유지해 과거 불완전한 기록의 근거 정확성과 조사 완결성도 별도로 평가합니다.

- 관련 오프라인 검사 56개 통과, `3.53s`; `results/validation/20261029T140213Z-56105db0/`
- 과거 조기 종료 응답의 조회 목록 재생 시 replica 조회 미완료 확인; 실제 fixture의 미조회 정답 참조 제외
- 위 재생은 종료 권한 검사이며 새 모델 성공 결과로 집계 제외
- 같은 두 catalog의 8개 회귀 사례 × 2회, 총 16회 사전 등록 평가
- 그룹별 최대 API 48회·합계 96회, 결과에 따른 prompt 변경·유료 재반복 제외
- 최초 contract·최초 종합·최종 종합·repair·Runbook 비교 분리

원본: `results/ops-agent/quality-evaluation/step12-investigation-gate-20261029/`.
`source-before/`는 변경 전 소스, `historical-gate-replay.json`은 과거 조기 종료의 정책 검사,
`evaluation/`은 실제 모델 16회의 source hash·wire trace·최초/최종 출력·turn별 gate audit 보관 경로입니다.
실행 명령은 [회귀 driver](../scripts/run_diagnosis_repair_regression.py)를 새 output 디렉터리와 `--live`로 호출합니다.

## 실제 결과

16회 모두 최소 조사를 완료했습니다. **최초 종합 15/16, 최종 종합 15/16**, 고정 Runbook은 16/16입니다.
Controller의 조사 coverage와 모델 출력 품질을 별도로 집계합니다.

| 그룹 | 최초 종합 | 최종 종합 | 최소 조사 완료 | Runbook 종합 |
| --- | ---: | ---: | ---: | ---: |
| Development 4개 × 2 | 7/8 | 7/8 | 8/8 | 8/8 |
| 이전 합성 4개 × 2 | 8/8 | 8/8 | 8/8 | 8/8 |
| 전체 | 15/16 | 15/16 | 16/16 | 16/16 |

이전 조기 종료가 발생한 `novel-01`은 두 반복 모두 stage·PG·replica·KEDA를 조회해 복합 후보를 보고했습니다.
실제 wire request와 turn별 audit를 대조해 필수 도구 호출 54회·선택 도구 호출 1회, 필수 미조회 상태에서 수락된 최종 답변 0회를 확인했습니다.
모델은 이번 실행에서 stage → PG → replica, stage → replica → PG, replica → stage → PG의 세 순서를 선택했습니다.
세 순서 모두 같은 최소 조사 조건을 만족했습니다. 다른 순서의 허용과 예산 유지는 오프라인 검사에서도 검증했습니다.

### 남은 인용 오류

`development / pilot-1-4`는 `WORKER_STAGE_ELEVATED`의 evidence ID에 원본에 없는 문자를 덧붙여 `V3_UNKNOWN_CITATION`으로 실패했습니다.
Repair는 해당 ID를 `_`로 바꿨고, 최초 출력에 없던 ID 추가를 금지하는 scope를 위반해 `V3_REPAIR_REDIAGNOSIS_FORBIDDEN`으로 실패했습니다.
올바른 관측을 제거하는 권한이나 새로운 ID를 도입하는 권한을 확대하지 않았습니다.
실제 repair 1회·구제 0회, 양성 관측 제거 0회입니다.

최초 contract·최종 contract도 각각 15/16입니다. 이전 v3.3은 최초 종합 14/16·최종 15/16이었으며, 이번 최종 종합 점수는 동일합니다.
조기 종료 차단은 실행 정책과 직접 audit로 확인했습니다. 작은 두 batch의 점수 차이를 일반화·인과 효과로 해석하지 않습니다.
기존 8개 사례는 회귀 set입니다. 다음 평가용 새 사례와 최초 결과는 별도로 유지해야 합니다.

- 실제 요청 모델 `gpt-5.6-luna`·budget·기존 두 catalog/rubric 유지
- API 요청 `72/96` 상한, input/output `195,810 / 9,778` tokens, usage 전체 확보
- 모델 도구 `55`회·Runbook `64`회, API/network 포함 elapsed 합계 `176.76s`·평균 `11.05s`
- 도구 수와 출력 정확도를 함께 해석, LLM 추가 가치·비용 절감·속도 향상 주장 제외
- 전체 suite `813 passed in 32.71s`; `results/validation/20261029T140248Z-20bece7b/`
- 실제 실행 로그 `results/validation/20261029T140248Z-c1926252/`, 남은 품질 실패를 보존한 exit `1`
- [최초·최종 출력·점수·wire gate audit·원본 hash](../results/ops-agent/diagnosis/investigation-v34-regression-20261029-summary.json)
- 모든 개별 점수·source hash 재검증, 각 wire request의 required 설정·제시 도구와 실제 호출 대조
- 기존 schema·validator·scorer·normalizer·repair 정책·catalog/rubric 유지, 이전 결과 불변
- 결과에 따른 추가 유료 재실행·runtime 배포·commit/push/merge 없음
