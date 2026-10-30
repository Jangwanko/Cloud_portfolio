# V3.3 근거에 제한된 관측 제거

후속 최소 조사 조건과 16회 평가: [V3.4 investigation gate](OPS_AGENT_V3_INVESTIGATION_GATE.md). 아래 v3.3 결과는 이전 기록으로 유지합니다.

2026-10-29 v3.2 새 사례의 실패를 바탕으로 repair에서 허용하는 관측 제거를 분리했습니다.
최초 출력의 오류는 별도로 집계합니다. 과거 결과를 새 규칙으로 성공 처리하거나 모델 원본 출력을 코드로 수정하지 않습니다.

## 허용 범위

잘못 추가한 양성 관측의 **원래 citation 전부**가 획득된 Fresh/OK 데이터이며, 같은 도구에서 해당 신호의 정상 상태를 직접 측정한 경우에만 제거를 허용합니다.

| 제거하려는 관측 | 원래 인용이 직접 보여야 하는 상태 |
| --- | --- |
| WORKER_STAGE_ELEVATED | get_worker_stage_latency / WITHIN_SCENARIO_BASELINE |
| WORKER_REPLICA_SHORTFALL | get_worker_replica_status / WORKER_CAPACITY_AVAILABLE |
| POSTGRES_GUARDRAIL_DEGRADED | get_postgres_health / POSTGRES_PATH_HEALTHY |

누락·오래됨·알 수 없는 ID·다른 도구·실제 양성 상태는 제거 권한의 근거가 될 수 없습니다.
올바른 양성 관측 삭제, 새 관측·가설·citation 추가, 가설명·지지 상태 변경은 계속 거부합니다.
기존 범위 안에서 context·signed 인용의 역할과 형식·gap·결론을 수정할 수 있으며 최종 decision validator도 통과해야 합니다.

모델에 보내는 repair 메시지는 같은 획득 evidence에서 계산한 `removable_observations`와 직접 정상 상태를 측정한 ID를 제공합니다.
모델이 수정 JSON을 생성하며 caller가 제거 범위와 전체 근거 역할을 별도로 검사합니다.
실제 제거한 관측과 반박 evidence ID는 `repair_scope_audit`에 남깁니다.
Scope 거부는 일반 ValueError 대신 `V3_REPAIR_REDIAGNOSIS_FORBIDDEN` 코드로 기록합니다.

최초 지시·공통 조사 절차·최종 자기 점검·decision schema·validator·scorer·normalizer·기존 두 catalog/rubric은 유지합니다.
Repair 지시·정책·범위 검증을 변경하고 execution version을 `ops.diagnosis.execution.v3.3`으로 올렸습니다.
정책은 [diagnosis_repair_v3.py](../ops_agent/diagnosis_repair_v3.py), 연결은 [scenario_agent_v3.py](../ops_agent/scenario_agent_v3.py)에서 확인할 수 있습니다.

## 사전 검증

- 직접 반박된 관측 제거, 정상 양성 관측 보존, stale/unavailable·unknown ID·잘못된 도구의 제거 거부
- 가설 상태 변경·새 관측·새 citation 거부, 실제 모델 응답 형태의 repair와 scope audit 검증
- Contract-valid 관측 누락도 최초 종합 점수에서 실패 유지
- 과거 `novel-03 / pilot-1-3`의 저장된 최초·repair JSON 쌍을 새 scope로 재생, 원래 PG 오관측 제거 허용 확인
- 이 재생은 새 LLM 호출이 없는 정책 검사이며 과거 공식 점수 `7/8` 유지
- 관련 검사 `43 passed in 3.32s`; `results/validation/20261029T135224Z-de1d5170/`

## 16회 회귀 평가 계약

기존 development 4개와 이전에 새로 작성한 합성 4개를 각각 2회 평가합니다.
두 그룹은 모두 이번 변경에 사용한 회귀 사례입니다. 신규 holdout으로 분류하지 않습니다.
모델은 기존 설정을 재사용하며 그룹별 최대 API 48회·합계 96회입니다. 도구 최대 4회·repair 1회·transport retry 0을 유지합니다.
두 그룹을 시작하기 전에 전체 schedule·catalog/rubric hash·driver hash·execution identity를 등록합니다.
결과를 본 뒤 prompt를 바꾸거나 추가 유료 반복을 하지 않습니다.

최초 출력의 contract 점수와 관측·원인·조사 완결성을 포함한 **최초 종합 점수**를 따로 계산합니다.
최종 통과율과 repair로 구제된 수를 함께 보고하며, 최초 오류를 final 성공 뒤에 숨기지 않습니다.
첫 출력이 contract-valid여도 관측 누락 등이 있으면 최초 종합 점수에서 실패합니다.

```powershell
.venv\Scripts\python.exe scripts\run_diagnosis_repair_regression.py --live `
  --output-dir results\ops-agent\quality-evaluation\repair-new-run
```

실제 API 비용이 발생하는 명령입니다. 새 디렉터리만 허용합니다.
Driver: [run_diagnosis_repair_regression.py](../scripts/run_diagnosis_repair_regression.py).
원본: `results/ops-agent/quality-evaluation/step11-repair-regression-20261029/`.
`source-before/`는 변경 전 파일, `historical-pair-replay.json`은 과거 응답 쌍의 정책 재생입니다.
`evaluation/`에는 사전 manifest·두 그룹의 wire trace와 compact 원본·최초/최종 점수·scope audit를 보존합니다.

## 실제 회귀 결과

16회 모두 실행했습니다. **최초 종합 14/16, 최종 종합 15/16**, 고정 Runbook은 16/16입니다.
최초 contract 15/16과 최종 contract 16/16은 종합 점수와 구분합니다.

| 그룹 | 최초 종합 | 최종 종합 | 고정 Runbook |
| --- | ---: | ---: | ---: |
| 기존 development 4개 × 2 | 8/8 | 8/8 | 8/8 |
| 이전 합성 4개 × 2 | 6/8 | 7/8 | 8/8 |
| 전체 | 14/16 | 15/16 | 16/16 |

Repair 1회는 `novel-03 / pilot-1-3`에서 알 수 없는 citation ID를 제거해 통과했습니다.
관측명·가설명·지지 상태는 유지했고, 실제 관측 제거는 **0회**입니다. 이번 유료 실행에서 새 관측 제거 권한이 성능을 개선했다고 주장하지 않습니다.
새 권한 자체는 오프라인 adversarial 검사와 과거 실패 응답 쌍의 정책 재생에서 확인했습니다.

남은 실패는 `novel-01 / pilot-2-1`입니다. 모델이 stage·PG 두 도구만 조회하고 PostgreSQL 후보를 보고한 뒤 종료했습니다.
보고한 PG 관측과 후보의 인용은 유효하지만, 동시에 존재하는 replica 부족과 관련 KEDA 조사를 놓쳤습니다.
이 출력은 contract-valid이므로 형식 repair가 발생하지 않습니다. 조사 완결성·관측·supported 후보 집합의 누락으로 종합 실패를 유지합니다.
초기 지시·조사 지침·최종 점검·schema와 budget은 변경 전 계약과 일치합니다. 한 번의 회귀 실행으로 조기 종료를 repair 정책 변경의 인과 효과라고 해석하지 않습니다.

- API 요청 `75/96` 상한, input/output `200,680 / 9,513` tokens, usage 전체 확보
- 모델 도구 합계 `58`, 고정 Runbook `64`; 낮은 종합 점수와 함께 해석, LLM 추가 가치 주장 제외
- API/network·조사·repair 포함 elapsed 합계 `179.64s`, 평균 `11.23s`
- 실제 실행 로그: `results/validation/20261029T135257Z-736a1547/`, 품질 실패를 보존한 exit `1`
- 전체 suite `800 passed in 33.60s`; `results/validation/20261029T135257Z-ef226be6/`
- [최초·최종 점수·원본 hash·scope audit·과거 pair replay](../results/ops-agent/diagnosis/repair-v33-regression-20261029-summary.json)
- 개별 최초·최종 점수 재계산, source/canonical evidence·driver·group compact hash 일치 확인
- 기존 catalog/rubric·근거 validator·scorer 유지, 과거 공식 점수 불변, 추가 유료 재실행·runtime 배포·commit/push/merge 없음

다음 구현 검토 대상은 **판단 종료 전 최소 조사 조건**입니다. 관측 제거 권한을 넓혀 미조회 영역을 추정하는 방식은 사용하지 않습니다.

후속 단계는 v3.3을 고정한 새 사례의 최초 평가입니다. 현재 16회 회귀 결과로 그 단계의 성공을 대신하지 않습니다.
