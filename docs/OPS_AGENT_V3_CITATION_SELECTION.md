# V3.5 제공된 증거 ID 선택

후속 실행 코드 고정 후 새 조합 평가: [V3.5 두 번째 합성 probe](OPS_AGENT_V35_SECOND_PROBE.md). 아래 16회는 기존 회귀 결과입니다.

2026-10-29 v3.4의 evidence ID 오타 실패를 바탕으로 citation을 요청 시점의 선택 목록으로 제한했습니다.
모델이 원본 ID를 선택하며 caller가 모델 출력을 자동 교정하거나 정답을 삽입하지 않습니다.

## 선택 목록의 범위

| 요청 | 허용 ID |
| --- | --- |
| 최초 조사·decision | 초기 activation ID와 해당 요청 전에 획득한 도구 evidence ID |
| Repair | 위 목록과 최초 decision이 인용한 ID의 교집합 |

관측, 가설 지지·반박, 문맥의 네 citation 배열에 같은 enum 정의를 적용합니다.
Base decision model과 기존 schema는 유지하고, 요청용 schema 사본의 `$defs/ProvidedEvidenceIdV3`를 공유 참조합니다.
목록은 매 turn 새로 만들며 미조회 도구·다른 사례·rubric·기대 정답을 참조하지 않습니다.
최대 256개 ID·ID 길이 1~256자로 제한하고, 초과 목록을 조용히 잘라내지 않습니다.

이 방식은 [OpenAI 공식 Structured Outputs 문서](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)의 strict schema·enum·definition 참조를 사용합니다.
Caller도 반환된 ID가 선택 목록에 있는지 검사해 `V3_CITATION_SELECTION_OUTSIDE_ENUM`으로 거부할 수 있습니다.
모델/provider가 제약을 무시한 응답을 반환하는 경로도 오프라인 테스트에 포함했습니다.

Repair에서 인용 가능한 ID가 없으면 observation 객체를 허용하지 않고 다른 citation 배열도 빈 배열로 제한합니다.
빈 enum이나 자유 문자열 fallback을 만들지 않습니다. 실제 의미·gap·가설 상태·repair 권한 검사는 그대로 적용합니다.

**목록에 존재하는 ID와 주장에 맞는 ID는 별도로 검사합니다.**
예를 들어 실제 stage ID를 PostgreSQL 후보의 지지로 선택하면 enum 검사는 통과해도 기존 evidence-role validator가 거부합니다.
Freshness·관측 누락·hypothesis status·context와 signed 인용 분리·최소 조사 gate·repair 제한도 유지합니다.

## 변경 범위와 검증

- Execution `ops.diagnosis.execution.v3.5`, 선택 정책 `ops.diagnosis.citation-selection.v1`
- [citation_selection_v3.py](../ops_agent/citation_selection_v3.py)의 schema 사본·선택 목록·local membership 검사 추가
- [scenario_agent_v3.py](../ops_agent/scenario_agent_v3.py)의 요청별 enum·허용 ID·schema hash audit 연결
- 기존 decision model·base contract·validator·scorer·normalizer·catalog/rubric·최소 조사·repair 정책 유지
- 기존 설정 모델·도구 최대 4회·repair 1회·시도당 API 최대 6회 유지
- 관련 오프라인 검사 `71 passed in 3.93s`; `results/validation/20261029T141452Z-61d38e72/`
- 네 citation 역할의 오타 거부·정상 ID의 잘못된 인과 사용 거부·repair 교집합·빈 목록·원본 schema 불변 검증
- 실제 도구 호출 뒤에만 enum 확장, 미조회 ID 노출 없음·mock provider 위반 거부 검증
- 과거 실패 최초 ID와 repair의 `_`가 새 목록에서 거부되는지 재생, 과거 공식 점수 유지

## 회귀 평가 계약

두 catalog의 기존 8개 사례를 각각 2회 평가합니다. 총 16회, 그룹별 API 최대 48회·합계 96회입니다.
첫 실행 결과·최초 종합·최종 종합·repair를 구분하고 결과를 본 뒤 prompt 조정·추가 유료 반복을 하지 않습니다.
이 사례들은 회귀 set이며 새로운 holdout으로 분류하지 않습니다.

원본: `results/ops-agent/quality-evaluation/step13-citation-enum-20261029/`.
`source-before/`는 v3.4 소스, `historical-citation-replay.json`은 과거 응답의 목록 검사입니다.
`evaluation/`에는 사전 등록·그룹별 원본·turn별 `citation_selection_audit`·요청 schema·최초/최종 점수를 보관합니다.
실행은 [회귀 driver](../scripts/run_diagnosis_repair_regression.py)를 새 output 디렉터리와 `--live`로 호출합니다.

## 실제 결과

16회 모두 **최초 contract·최초 종합·최종 contract·최종 종합 16/16**입니다.
Repair 0회이며 고정 Runbook도 16/16입니다. Development 8회와 이전 합성 8회가 각각 모두 통과했습니다.
Repair 교집합·빈 선택 목록 분기는 이번에는 오프라인 검증 범위입니다.

| 검증 항목 | 결과 |
| --- | ---: |
| 요청 schema·허용 ID 집합 재생 검증 | 72/72 요청 |
| 실제 JSON 응답의 citation membership 검사 | 16/16 응답 |
| 선택 목록 밖 citation ID | 0 |
| 최소 조사 완료 | 16/16 |
| 필수 도구 호출 | 54 |
| 선택 도구 호출 | 2 |

매 요청의 enum이 그때까지 실제 호출한 도구의 결과와 초기 ID에서만 만들어졌는지 확인했습니다.
요청 schema와 audit의 canonical hash·source hash·개별 최초/최종 점수도 재검증했습니다.
미조회 ID 제공·가짜 ID 교정·사후 score 변환 없이 모델 원본으로 채점했습니다.

V3.4의 15/16과 비교하면 이번 회귀에서는 인용 ID 오타가 발생하지 않았습니다.
기존에 반복 사용한 작은 회귀 set의 한 번의 결과입니다. 외부 holdout 일반화·실제 incident 원인 정답·LLM 추가 가치를 입증하지 않습니다.
Schema가 보장하는 선택 범위와 validator가 검사하는 근거 의미는 계속 분리합니다.

- 요청 모델 `gpt-5.6-luna`, 기존 모델 설정·도구/repair budget·catalog/rubric 유지
- API `72/96` 상한, input/output `216,590 / 8,458` tokens, usage 전체 확보
- 도구 합계 모델 `56`·고정 Runbook `64`, API/network 포함 elapsed 합계 `190.94s`·평균 `11.93s`
- Input tokens와 elapsed는 이전 회귀보다 증가, 비용 절감·속도 향상 주장 제외
- 전체 suite `828 passed in 34.76s`; `results/validation/20261029T141547Z-1c89e6ae/`
- 실제 실행 로그 `results/validation/20261029T141547Z-37a9842a/`, exit `0`
- [최초/최종 decision·점수·turn별 enum·검증 요약·원본 hash](../results/ops-agent/diagnosis/citation-v35-regression-20261029-summary.json)
- 기존 최소 조사·repair·validator·scorer·base schema·fixture hash 유지, 과거 결과 불변
- 목표 회귀 완료 후 추가 유료 반복·runtime 배포·commit/push/merge 없음
