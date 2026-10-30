# V3 관측·가설·문맥 분리 후보

후속 독립 실행기와 사전 채점 기준: [OPS_AGENT_V3_EVALUATION.md](OPS_AGENT_V3_EVALUATION.md).
아래 내용은 실행기 연결 전 출력 계약 후보의 검토 기록입니다.

2026-10-29 `ops.diagnosis.decision.v3` 오프라인 후보입니다.
기존 v2 run schema·prompt·validator·quality rubric·public replay·runtime에 연결하지 않았습니다.
출력의 역할과 증거 경계를 검토하는 단계이며 실제 모델 실행·품질 개선 결과는 없습니다.

## 출력 역할

| 필드 | 의미 |
| --- | --- |
| `observations` | 도구가 직접 측정한 stage 상승·replica 부족·PostgreSQL guardrail 저하 |
| `hypotheses` | 직접 지지·반박 관측과 gap을 갖는 기여 원인 후보 |
| `context_evidence_ids` | 초기 lag·KEDA·다른 배경 관측; 원인 지지의 대체물로 사용 제외 |
| `investigation_gaps` | 더 구분하기 위해 필요한 관측 |
| `conclusion` | 직접 지지 후보 존재 시 `SUPPORTED_CANDIDATES`, 그 외 `ABSTAIN` |

`SUPPORTED_CANDIDATES`는 관측으로 지지하는 의심 단계입니다. 검증된 root cause나 incident 복구 판정이 아닙니다.
`ABSTAIN`에서도 observations는 유지합니다. 관측된 이상이 있다는 사실과 원인을 구분할 근거가 있다는 판단을 따로 표현합니다.

### 중요한 vocabulary 변경

- v2 `WORKER_PATH_PRESSURE_SUSPECTED`는 v3 causal enum에서 제외; 직접 측정 가능한 `WORKER_STAGE_ELEVATED` 관측으로 표현
- Stage 상승만 있고 구분할 직접 근거가 없으면 관측을 남긴 채 `ABSTAIN`
- v2 `POSTGRES_PATH_DEGRADED_SUSPECTED`보다 관측 범위를 좁힌 `POSTGRES_GUARDRAIL_PRESSURE_SUSPECTED` 후보 사용
- PostgreSQL 후보는 reachability·HA·replication guardrail에 한정; query/commit latency로 확대 제외
- Replica shortfall 후보는 실제 desired/available deficit을 요구; KEDA active 자체는 context로 유지
- HOT_KEY·retry·sequence contention·rebalance는 현재 직접 signed rule이 없어 `INSUFFICIENT`와 gap만 허용

기존 네 fixture의 v2 정답 label을 이 후보로 자동 변환하거나 점수를 비교하지 않습니다.
특히 v2의 Worker path 기대값을 v3 observation으로 옮기면서 과거 `1/8` 점수를 새 성공률로 재표현하지 않습니다.
새 vocabulary의 평가 목표와 비교 기준은 실제 모델 연결 전에 별도로 정해야 합니다.

## 형태 예시

아래 ID는 설명용입니다. 실제 검증은 입력 acquisition에 존재하는 ID만 허용합니다.

```json
{
  "schema_version": "ops.diagnosis.decision.v3",
  "input_condition": "CORE_BACKLOG_PRESSURE=PRESENT",
  "observations": [
    {"signal": "WORKER_STAGE_ELEVATED", "evidence_ids": ["known-stage-id"]}
  ],
  "hypotheses": [],
  "context_evidence_ids": ["known-lag-id", "known-postgres-unavailable-id"],
  "investigation_gaps": ["CAUSAL_DISCRIMINATOR_UNAVAILABLE"],
  "conclusion": "ABSTAIN"
}
```

## 검증 경계

- 모든 context·observation·signed citation의 존재 확인, 중복·역할 충돌 거부
- Observation은 해당 tool의 Fresh/OK 측정과 semantic flag가 일치해야 허용
- Replica 후보의 직접 지지는 replica shortfall, PostgreSQL 후보의 직접 지지는 guardrail 저하에 한정
- Lag·KEDA·stage 문맥을 직접 원인 지지/반박으로 승격하면 거부
- `CONFLICTED`는 양쪽 signed citation, `INSUFFICIENT`는 gap 요구
- 원인 보류의 gap·conclusion 불일치와 runtime action/복구 선언 거부
- Rebalance gap의 정확한 코드 유지, 확인되지 않은 다른 원인의 signed 지원 거부

이 검증기는 신뢰된 normalized acquisition 결과를 입력으로 받는 후보 모듈입니다.
Frozen bundle의 원본 hash·provenance·tool budget 검사는 기존 acquisition 경계에서 선행해야 합니다.
반환 결과의 `causal_truth_verified=false`, `investigation_completeness=NOT_EVALUATED`를 유지합니다.
KEDA 조사 완결성·실제 원인 진실·일반화 정확도는 이 decision validator가 판정하지 않습니다.

모델은 [diagnosis_v3_models.py](../ops_agent/diagnosis_v3_models.py),
검증은 [diagnosis_v3_validator.py](../ops_agent/diagnosis_v3_validator.py),
공통 지시·schema는 [diagnosis_v3_contract.py](../ops_agent/diagnosis_v3_contract.py)입니다.
객체의 추가 필드를 금지하고 모든 필드를 required로 내보냅니다.
Schema 형태는 [OpenAI Structured Outputs 지침](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)을 참고했으며 API 호환성의 실제 호출 검증은 미실행입니다.

## 로컬 검토 artifact

```powershell
.venv\Scripts\python.exe scripts\export_diagnosis_v3_candidate.py `
  --output-dir results\ops-agent\quality-evaluation\v3-candidate-01
```

새 디렉터리에 `contract.json`, `examples.json`, `manifest.json`을 보존합니다.
Contract identity는 `ops.diagnosis.prompt.v3-candidate`와 지시/schema의 canonical hash입니다.
Manifest는 관련 코드 hash와 API 요청 `0`을 기록합니다.
`HAND_AUTHORED_OFFLINE_EXAMPLES_NOT_MODEL_INPUT` 예시 4개는 사람이 정한 표현 예시이며 모델 prompt에 주입하지 않습니다.
예시의 계약 통과를 모델 성능이나 품질 정확도 수치로 사용하지 않습니다.

이번 로컬 artifact: `results/ops-agent/quality-evaluation/step7-v3-contract-candidate-20261029/`.
실제 v3 tool loop·repair·run artifact·quality scoring·모델 비교·배포는 다음 구현 범위입니다.

## 이번 검증

- 후보 관련 테스트 `22 passed in 0.41s`
- 전체 suite `756 passed in 30.44s`; `results/validation/20261029T125048Z-df56b36f/`
- 문서 링크 검사 오류 `0`, diff 공백 검사 통과
- 추가 모델 API 호출·기존 v2 점수 변환·runtime 배포·commit/push/merge 없음
