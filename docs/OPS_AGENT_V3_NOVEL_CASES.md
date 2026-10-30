# V3.2 새 관측 조합 평가

후속 관측 제거 정책·기존 8개 사례의 16회 회귀: [OPS_AGENT_V3_REPAIR.md](OPS_AGENT_V3_REPAIR.md). 아래 최초 평가의 `7/8` 점수는 유지합니다.

2026-10-29 기존 네 development 사례에서 8/8을 달성한 v3.2를 고정하고, 새로 작성한 네 합성 사례에 각 2회 적용하는 평가입니다.
이 문서의 사례와 기대값은 모델 호출 전에 작성했습니다. 실제 클러스터 장애를 주입하거나 수집한 결과는 아닙니다.

## 새 사례와 사전 기대값

| 사례 | 새 관측 조합 | 기대 관측 | Supported 후보 |
| --- | --- | --- | --- |
| novel-01 | Stage 정상, fresh replica 6개 중 3개 available, fresh PG replication·sync guardrail 저하 | replica 부족·PG 저하 | Worker capacity·PG guardrail 두 후보 |
| novel-02 | Fresh stage 상승, 5분 전 replica 부족·PG 장애 수치 | stage 상승 | 없음, gap을 보존한 ABSTAIN |
| novel-03 | Stage·KEDA 조회 불가, fresh PG 정상, fresh replica 6개 중 4개 available | replica 부족 | Worker capacity 후보 |
| novel-04 | Stage ratio 1.99, PG delay가 정상 상한과 같음, replica 정상, backlog PRESENT 유지 | 없음 | 없음, gap을 보존한 ABSTAIN |

네 사례 모두 stage·PG·replica 조회를 조사 완결성의 최소 조건으로 고정합니다. Fresh replica 부족이 있는 01·03은 KEDA 조회도 필요합니다.
KEDA 조회가 UNAVAILABLE인 경우에도 조회 수행 자체는 조사 완료이며, 그 결과를 원인 근거로 사용할 수는 없습니다.
기대값은 새 catalog에 맞게 사전 작성했고, exact-set·근거 역할·보류·분모·종합 점수의 채점 방식은 기존 v3 scorer를 유지합니다.

04의 optional partition-lag 도구는 강하게 편중된 표본을 반환합니다. 모델이 이 도구를 선택한 경우에만 해당 신호에 노출됩니다.
조회하지 않았다면 hot-key 유혹에 대한 저항을 검증했다고 해석하지 않습니다. 현재 v3에는 hot-key 직접 causal rule이 없습니다.

## 고정한 실행 경계

- 기존 `gpt-5.6-luna` 설정·key·v3.2 execution identity 유지
- Prompt·decision schema·validator·scorer·normalizer·기존 development fixture 유지
- 최대 도구 4회·repair 1회·batch API 48회·transport retry 0 유지
- 네 새 사례 × 2회, 첫 batch 전체 결과와 실패 보존; 결과에 따른 prompt 수정·재시도 제외
- 모델 원본 출력 보존, 사례명은 임의 alias로 가리기, rubric·정답·catalog 전체는 모델 입력에서 제외
- 같은 registry와 expected sets로 고정 Runbook 비교군 함께 평가

Historical PRESENT activation을 통제된 공통 입력으로 재사용합니다. 새 도구 관측은 해당 frozen snapshot에 대한 합성 데이터입니다.
Freshness는 snapshot 기준으로 지정한 상태이며 현재 시각의 live source freshness가 아닙니다.
Catalog schema가 요구하는 v2 정답·branch 필드는 placeholder로 보존하지만 v3 채점과 모델 입력에 사용하지 않습니다.

작업자가 만든 사례이며 평가 대상 모델에는 처음 제시하는 조합입니다. 독립적인 외부 holdout이나 production 분포의 표본으로 주장하지 않습니다.
기존 사례에서 8/8을 얻기까지 본 실패를 바탕으로 사례를 설계했다는 점도 해석 범위에 포함합니다.

## 첫 실제 평가 결과

요청 모델 `gpt-5.6-luna`, 최초·최종 contract와 종합 모두 **7/8**입니다. 고정 Runbook은 **8/8**입니다.
8회 모두 실행했고 조사 완결성은 8/8입니다. 모델과 Runbook은 각각 도구 32회를 사용했습니다.

| 새 사례 | 모델 종합 | 고정 Runbook |
| --- | ---: | ---: |
| 복합 PG·replica 저하 | 2/2 | 2/2 |
| 오래된 장애 수치 | 2/2 | 2/2 |
| Stage·KEDA 없음, replica 부족 | 1/2 | 2/2 |
| 직접 지지 원인 없음 | 2/2 | 2/2 |

모델 API 41회, input/output `112,192 / 4,782` tokens, usage 전체 확보입니다.
API/network·조사·repair 포함 elapsed 합계 `88.82s`, 평균 `11.10s`입니다.
모든 시도의 도구 순서는 stage → PG → replica → KEDA로 같았습니다. Optional partition-lag 도구는 선택되지 않아 skew에 대한 판단은 검증 범위에서 제외합니다.

### 실패 1회와 repair 제한

`pilot-1-3`은 fresh PG health가 `POSTGRES_PATH_HEALTHY`임에도 `POSTGRES_GUARDRAIL_DEGRADED` 관측을 추가했습니다.
Replica 부족 후보의 직접 지지는 올바르게 인용했지만 PG observation의 의미가 수집된 evidence와 불일치해 `V3_OBSERVATION_NOT_MEASURED`로 거부됐습니다.

Repair는 잘못 추가한 PG observation을 제거했습니다. 기존 정책은 observation-name 집합 변경을 금지하므로 이 수정도 `V3_REPAIR_REDIAGNOSIS_FORBIDDEN` 범위에 들어갑니다.
현재 실행기의 저장된 최종 failure code는 일반 `ValueError`이며, scope 함수와 최초·repair 원본을 대조해 원인을 확인했습니다.
사후에 repair JSON만 직접 validator에 넣으면 인용·근거 역할 검사를 통과합니다. 이 검사는 repair 권한 검사를 제외한 별도 설명이며 **최종 7/8 점수를 바꾸지 않습니다**.

따라서 이번 실패는 최초 보고의 잘못된 관측과, 그 관측 제거를 허용하지 않는 repair 정책을 함께 보여줍니다.
같은 사례의 두 번째 반복은 최초 출력부터 통과했습니다. 이 2회만으로 실패 빈도나 데이터 누락의 인과 효과를 추정하지 않습니다.
후속 변경에서는 새로운 양성 관측을 추가하는 행위와, 직접 evidence에 반하는 관측을 제거하는 행위의 권한 범위를 별도로 설계할 수 있습니다.
이번 평가에서는 모델·prompt·validator·repair 제한을 변경하거나 추가 유료 재실행을 하지 않았습니다.

- [8회 원본 요약·개별 출력·hash](../results/ops-agent/diagnosis/pilot-v3-novel-20261029-summary.json)
- 실제 실행 기록: `results/validation/20261029T134214Z-6c019f5c/`, 품질 실패를 보존한 exit `1`
- 별도 사후 설명: 실제 실행 원본의 `posthoc-analysis.json`, 재채점·새 합격률 제외
- 관련 오프라인 검사 `28 passed in 2.15s`; `results/validation/20261029T134153Z-2c7fa32e/`
- 전체 suite `785 passed in 42.32s`; `results/validation/20261029T134214Z-4755b67d/`
- 기존 v3.2 model·budget·execution identity 및 prompt/schema/validator/scorer/normalizer/client/Runbook 파일 hash 일치 확인
- CLI의 catalog/rubric 경로 선택·외부 경로 기록·새 사례 해석 문구만 실행기에 추가
- 합성 사례 작성기·기대값·static fixture 재현, stale 지지 거부·복합 후보 누락 실패·원본 덮어쓰기 거부 검증

## 재현

```powershell
.venv\Scripts\python.exe scripts\run_diagnosis_v3_pilot.py --live `
  --catalog ops_agent\fixtures\diagnosis\novel_v1\catalog.json `
  --rubric ops_agent\fixtures\diagnosis\novel_v1\rubric.json `
  --output-dir results\ops-agent\quality-evaluation\novel-new-run
```

실제 모델 비용이 발생하는 명령입니다. 새 output 디렉터리만 허용합니다.
Catalog 작성 자체는 모델 호출 없이 [build_diagnosis_v3_novel_cases.py](../scripts/build_diagnosis_v3_novel_cases.py)로 재현할 수 있습니다.

- [사례 원본](../ops_agent/fixtures/diagnosis/novel_v1/catalog.json)
- [사전 기대값·필수 도구](../ops_agent/fixtures/diagnosis/novel_v1/rubric.json)
- [작성 provenance·평가 protocol·고정 execution identity](../ops_agent/fixtures/diagnosis/novel_v1/design.json)
- 사전 등록: `results/ops-agent/quality-evaluation/step10-novel-cases-20261029/preregistration.json`
- 실제 실행: 위 디렉터리의 `live/`, 초기·repair 응답과 개별 실패·usage·summary 보존
- 기존 v3.2 개선 기록: [OPS_AGENT_V3_ITERATIONS.md](OPS_AGENT_V3_ITERATIONS.md)
