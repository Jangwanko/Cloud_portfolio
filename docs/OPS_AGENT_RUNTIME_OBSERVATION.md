# Runtime Observation — 2026-10-29

후속 2026-10-30 KST: [runtime adapter 구현과 실제 과거 snapshot 모델 비교](OPS_AGENT_RUNTIME_ADAPTER.md) 완료. 아래 `PENDING`은 이 capture-only 단계의 기록이며, 기존 observation summary는 변경하지 않았습니다.

## Current result

기존 read-only collector로 실제 local-ha를 15초 간격, 3회 수집하고 기존 v2 condition evaluator로 재평가한 결과입니다. `CORE_BACKLOG_PRESSURE=ABSENT`에 따라 모델과 causal Runbook 실행을 생략했습니다. 이번 작업은 observation 수집과 activation gate 검증이며 v3.5 실제 진단 정확도 측정은 다음 단계입니다.

| 신호 | 실제 세 capture 결과 | 해석 |
| --- | --- | --- |
| Kafka lag / 60초 slope | 모두 `0 / 0` | 각 capture의 full 60초 window backlog 없음 |
| Worker desired / available | 모두 `2 / 2` | 관측 시점 replica 가용 |
| PostgreSQL degraded condition | 모두 `ABSENT` | 기존 local-ha readiness guardrail 범위 |
| KEDA Ready / Active | 모두 `True / False` | Worker min/max `2/4` context |
| Worker stage latency | 모두 `MISSING`, freshness `UNKNOWN` | 정상·0ms로 대체 제외; 유휴와의 인과관계 미확정 |
| 전체 collection | 모두 `PARTIAL` | optional metric 누락과 required backlog evidence 가용성 분리 |
| Sequence activation | `ABSENT` | `LATEST_FULL_WINDOW_BACKLOG_ZERO_WITHOUT_SEQUENCE_ACTIVATION` |
| GPT / causal Runbook | 모두 `NOT_RUN` | API 0회, 진단 비교 `NOT_EVALUATED` |

수집 시작은 UTC `14:58:43.431212`, `14:58:58.430469`, `14:59:13.420203`입니다. 각 collector는 순차 호출이므로 atomic snapshot을 뜻하지 않습니다. Freshness는 각 collection 완료 시점에 고정되며 replay 시점의 현재 상태로 해석하지 않습니다. HTTP GET과 기존 고정 `kubectl get` 경로를 사용하며 event 생성, replica 변경, 장애 주입, remediation 수행은 제외했습니다.

## Reusable command and artifacts

```powershell
.venv\Scripts\python.exe scripts\observe_worker_backlog.py `
  --context kind-messaging-ha `
  --interval-seconds 15 `
  --output-dir results/ops-agent/shadow/<new-run-directory>
```

- 새 directory 필수, 기존 run overwrite 거부
- 고정 3회 capture, interval 허용 범위 9~30초, calibrated `kind-messaging-ha` context만 허용
- 시작 전 `plan.json`에 policy·script/module hash 기록
- 각 capture 직후 `bundles/sample-*.json` 저장, collector의 safe raw projection 보존
- `conditions.json`: 변경 없는 `ops.conditions.v2` sequence 결과
- `summary.json`: capture별 condition·신호 누락·gate·모델 미실행 상태
- `inventory.json`: summary/conditions/plan/bundles/raw 파일 18개 SHA-256
- 실패 시 완료 capture 보존, `failure.json`에 예외 종류만 기록

[추적 summary와 integrity inventory](../results/ops-agent/diagnosis/observation-20261029-summary.json)에 canonical bundle digests와 18개 파일 hash 검증 결과를 보존했습니다. 실제 raw와 bundles는 Git-ignored `results/ops-agent/shadow/20261029-observation-01/`에 있습니다. 첫 탐색 capture `20261029-first-observation/`은 별도 보존하며 세 capture 결과에 합산하지 않습니다.

## Implementation and validation

- `ops_agent/observation.py`: 저장된 3개 bundle 재평가, `PRESENT`/`ABSENT`/`UNKNOWN` gate, 모델 호출 없는 요약
- `scripts/observe_worker_backlog.py`: 기존 collector 재사용, 고정 capture 수·시작 간격·새 파일·실패 보존·inventory
- `PRESENT`이면 `ELIGIBLE_FOR_DIAGNOSIS`만 기록; 현재 runner의 v3.5 runtime adapter 상태 `PENDING`
- `ABSENT`와 `UNKNOWN`에서 diagnosis 시작 제외; synthetic catalog나 과거 PRESENT activation 대입 제외
- 기존 v3.5 모델·prompt·validator·scorer·실행 코드 유지
- 전체 suite `850 passed in 41.23s`, 관련 gate/sequence 검사 `25 passed in 2.86s`
- 실제 수집 wrapper exit `0`, summary·conditions frozen replay 일치, inventory 18개 모두 일치
- Gate/sequence log: `results/validation/20261029T145832Z-b3ae4782/`
- 실제 수집 log: `results/validation/20261029T145842Z-9836fe90/`
- 전체 suite log: `results/validation/20261029T145915Z-fb9168e2/`

## Next boundary and model choice

다음 구현은 실제 normalized evidence를 `FROZEN_PROJECTED` provenance로 제공하는 v3.5 runtime adapter입니다. Scenario Lab의 `scenario_baseline_ms`를 운영 baseline으로 복사하지 않고, stage 기준선이 없으면 명시적인 gap으로 유지해야 합니다. 실제 PRESENT activation과 그 근거 bundle을 연결한 뒤 모델과 고정 Runbook에 동일한 frozen snapshot을 제공할 수 있습니다. 현재 runner는 이를 자동 실행하지 않습니다.

모델 교체는 같은 snapshot·출력 계약·조사 예산에서 최초 판정, 잘못된 원인 지지, 누락, 보류, 토큰과 지연을 비교한 뒤 결정합니다. 현재 정상 관측의 gate는 deterministic code가 처리하므로 더 강한 모델의 추가 가치를 측정하지 않았습니다. 평가로 baseline을 확보한 뒤 개선하는 순서는 [OpenAI model optimization guidance](https://developers.openai.com/api/docs/guides/model-optimization)와 같습니다. 현재 requested model 설정은 유지했습니다.
