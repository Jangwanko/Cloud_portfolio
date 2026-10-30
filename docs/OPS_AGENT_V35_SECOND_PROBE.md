# V3.5 고정 후 두 번째 새 사례 평가

2026-10-29 기존 회귀 16/16을 기록한 v3.5를 고정하고, 네 가지 새 합성 관측 조합을 작성했습니다.
동일 요청 모델·prompt·schema 생성·validator·scorer·최소 조사·repair 권한으로 각 사례를 2회 평가합니다.
첫 8회 결과와 실패를 보존하며 결과에 따른 코드 수정이나 추가 유료 반복을 하지 않습니다.

## 모델 호출 전에 정한 기대값

| 사례 | 주요 새 조건 | 기대 관측 | Supported 후보 |
| --- | --- | --- | --- |
| probe2-01 | Stage·PG ERROR, replica UNAVAILABLE | 없음 | 없음, ABSTAIN과 gap |
| probe2-02 | Fresh stage ratio 2.0, PG의 정상 수치는 stale, fresh replica 7개 중 available 0, KEDA 조회 불가 | stage 상승·replica 부족 | Worker capacity |
| probe2-03 | Stage 상승 수치는 stale, fresh PG primary unreachable, replica 조회 불가 | PG guardrail 저하 | PG guardrail pressure |
| probe2-04 | Stage 조회 불가, fresh PG delay가 상한보다 1 byte 큼, fresh replica 5개 중 available 4, KEDA 상태 stale | PG 저하·replica 부족 | PG guardrail pressure·Worker capacity |

모든 사례에서 stage·PG·replica 조회를 완료해야 합니다. Fresh replica 부족이 있는 02·04는 KEDA 조회도 필수입니다.
UNAVAILABLE/ERROR 조회는 조사 시도에 포함되며 해당 지표가 정상이라는 뜻으로 해석하지 않습니다.
Stale 정상 수치는 후보의 직접 반박 근거로 사용하지 않습니다. 오래된 KEDA 상태도 fresh replica 부족을 반박하지 않습니다.

기존 두 catalog의 여덟 사례와 새 네 사례를 primary tool의 `status / freshness / semantic flag` 조합으로 비교했습니다.
새 네 조합은 서로 다르고 기존 여덟 조합과도 겹치지 않습니다. 숫자나 이름만 바꾼 사례와 구분하기 위한 검사입니다.

## 평가 경계

이 사례는 작업자가 작성한 합성 데이터입니다. 평가 대상 모델에는 처음 제시하지만 독립 외부 holdout이나 운영 분포의 표본으로 주장하지 않습니다.
이전 실패와 현재 계약을 아는 작업자가 설계한 작은 전이 검사라는 한계를 유지합니다.
Historical backlog PRESENT activation을 공통 입력으로 재사용하고, 관측은 frozen snapshot 기준의 합성 freshness입니다.
실제 클러스터 수집·장애 주입·운영 복구를 수행하지 않습니다.

기대값은 새 catalog에 맞게 사전 작성하고, exact observation·supported-cause set·조사 완결성·전체 분모의 v3 채점 방식은 유지합니다.
사례명은 API 입력에서 alias로 가리며 전체 catalog·rubric·legacy 정답/branch 필드를 모델에 제공하지 않습니다.
도구 최대 4회·repair 1회·시도당 API 6회·전체 48회·transport retry 0을 유지합니다.
실행 전 v3.5의 모든 관련 구현 파일 hash와 execution identity·모델 설정 일치를 확인했습니다.

## 재현과 검증

```powershell
.venv\Scripts\python.exe scripts\run_diagnosis_v3_pilot.py --live `
  --catalog ops_agent\fixtures\diagnosis\novel_v2\catalog.json `
  --rubric ops_agent\fixtures\diagnosis\novel_v2\rubric.json `
  --output-dir results\ops-agent\quality-evaluation\second-probe-new-run
```

실제 API 비용이 발생하는 명령이며 새 디렉터리만 허용합니다.

- [새 사례 catalog](../ops_agent/fixtures/diagnosis/novel_v2/catalog.json)
- [사전 expected sets·필수 조회](../ops_agent/fixtures/diagnosis/novel_v2/rubric.json)
- [작성 provenance·frozen identity·protocol](../ops_agent/fixtures/diagnosis/novel_v2/design.json)
- [모델 없이 재현하는 작성기](../scripts/build_diagnosis_v35_probe.py)
- 관련 검사 `28 passed in 0.85s`; `results/validation/20261029T143407Z-01b7a609/`
- 의미 조합의 비중복·static 재현·stale 정상 수치의 signed 인용 거부·Fresh deficit의 KEDA 분기·덮어쓰기 거부 검증
- 원본 `results/ops-agent/quality-evaluation/step14-v35-second-probe-20261029/`, 사전 등록과 `live/` trace·summary 보존
- 이전 회귀 결과: [V3.5 제공된 ID 선택](OPS_AGENT_V3_CITATION_SELECTION.md)

## 최초 실제 모델 결과

새 네 사례 × 2회 모두 **최초 contract·최초 종합·최종 종합 8/8**입니다.
Repair 0회이며 고정 Runbook도 8/8입니다. 기존 회귀 16회는 이전 실행으로 구분하고, 이번 실행을 합산한 하나의 실험처럼 표현하지 않습니다.

| 새 조건 | 모델 종합 | Runbook 종합 |
| --- | ---: | ---: |
| Primary 조회 세 가지 모두 실패 | 2/2 | 2/2 |
| Stale 정상 PG·fresh stage 상승·replica 부족 | 2/2 | 2/2 |
| Stale stage·replica 조회 불가·fresh PG 장애 | 2/2 | 2/2 |
| Stage 조회 불가·stale KEDA·fresh PG/replica 문제 | 2/2 | 2/2 |

각 요청에 실제 제공된 ID만 enum에 들어갔는지 36개 wire request를 재생했고, JSON 응답 8개의 citation이 해당 목록 안에 있는지 확인했습니다.
목록 밖 ID 0개·미조회 ID 노출 0개·최소 조사 완료 8/8입니다. 개별 최초/최종 점수와 source·schema hash도 재검증했습니다.
기존 v3.5의 모든 실행 구현 파일·모델 설정·execution identity 일치를 사전 등록과 실행 완료 뒤에 확인했습니다.

도구는 모델 28회·Runbook 32회입니다. 모델의 28회는 모두 controller가 요구한 필수 조회였으며 선택 조회는 0회입니다.
이 차이는 최소 조사 정책과 고정 4회 체크리스트의 차이로 설명합니다. LLM이 도구 선택에서 추가 가치를 냈다는 근거로 사용하지 않습니다.
새 관측 조합에서 통과한 작은 합성 평가이며 실제 운영 분포의 정확도·원인 정답·production 효과로 확장하지 않습니다.

- 요청 모델 `gpt-5.6-luna`, API `36/48` 상한, input/output `106,645 / 3,960` tokens, usage 전체 확보
- API/network 포함 elapsed 합계 `77.02s`, 평균 `9.63s`; 이전 batch와 속도/비용 개선 비교 제외
- 전체 suite `841 passed in 38.12s`; `results/validation/20261029T143457Z-5107c587/`
- 실제 모델 실행 로그 `results/validation/20261029T143457Z-84c7ed8e/`, exit `0`
- [최초/최종 점수·새 사례별 출력·요청 enum audit·원본 hash](../results/ops-agent/diagnosis/v35-second-probe-20261029-summary.json)
- 결과를 본 뒤 모델 구현·prompt·정책·rubric 수정 또는 추가 유료 재실행 없음
- Runtime 배포·commit/push/merge 없음
