# Runtime Evidence Adapter — 2026-10-30 KST

## Result and scope

실제 normalized Evidence Bundle을 v3.5 조사 도구에 연결하는 `FROZEN_PROJECTED` adapter와 별도 runtime 실행기를 구현했습니다. 새 실행기는 실제 bundle과 condition을 재계산해 일치 여부를 검증하고, `CORE_BACKLOG_PRESSURE=PRESENT`에서 모델과 고정 Runbook을 비교합니다. 도구는 저장된 증거만 읽으며 호출 시 추가 network나 Kubernetes 작업을 수행하지 않습니다.

| 검증 | 실제 결과 | 범위 |
| --- | --- | --- |
| 직전 실제 정상 스냅샷 3개 | `ABSENT`, GPT 0회, 양쪽 진단 생략 | `--live`에도 시작 gate 우선 |
| 2026-08-16 실제 backlog sequence | 22개 bundle·condition canonical replay 일치 | 과거 실제 수집 원본 |
| 동일 backlog의 projection | 최초 matched window `[1,2,3]`의 마지막 capture 선택 | index `3`, 해당 시점 Worker `4/4`, PG 정상 |
| 현재 모델의 단일 재생 | `gpt-5.6-luna`, 최초 contract valid, repair `0` | API 4회, 필수 도구 3개 |
| 모델 / 고정 Runbook | 모두 관측 positive signal 없음·supported cause 없음·`ABSTAIN` | label 일치, 원인 정답 독립 검증 제외 |
| Worker stage | mean 약 `15.07ms`, 운영 기준선 `UNAVAILABLE` | 상승 또는 commit latency 판정 제외 |
| 설명문 수동 검토 | 문장 분할과 혼합 문자 확인 | 원문 보존, contract와 문장 품질 분리 |

이번 모델 호출은 8월 실제 스냅샷의 재생입니다. 현재 장애를 재현하거나 새로운 event를 쓰지 않았습니다. 모델과 Runbook의 일치는 정확도 점수나 GPT 추가 가치의 증거로 승격하지 않습니다. Input/output token은 `20,953 / 846`이며 시간·비용 개선 비교는 수행하지 않았습니다.

## Evidence and execution boundaries

- 입력: ordered `ops.evidence.v1`과 `ops.conditions.v2`, source 순서·digest·condition 전체 재계산 일치 필수
- 모델 시작 조건: verified `PRESENT`; `ABSENT`/`UNKNOWN`은 key 로딩과 모델 호출 이전 종료
- Source snapshot: 최초 matched activation window의 마지막 capture, 전체 sequence digest와 projection index 함께 기록
- 지원 도구: stage latency, PostgreSQL health, Worker replica, KEDA 네 개의 zero-argument tool
- 출처: `FrozenProjectedProvenance`의 원본 evidence IDs와 canonical bundle digest, collection 시점 freshness 명시
- PG: 기존 evaluator의 readiness reason·component·scope·freshness·raw-source consistency 검사 재사용
- Replica: 기존 dependency 검증 후 available/desired 비교, 단일 snapshot의 `grace_proven=false` 유지
- 누락·오래된 값·incoherent dependency: 정상값 또는 signed cause로 변환 제외
- Stage: 실제 측정 context 유지, `RUNTIME_STAGE_BASELINE_UNAVAILABLE` gap, synthetic baseline 복사 제외
- Controller: 필수 3개 조회·replica shortfall 시 KEDA, 도구 최대 4개·repair 최대 1회·요청 최대 6회
- API: 현재 모델 설정 유지, timeout 30초·transport retry 0회, 각 요청 직전 wire request와 응답 직후 원문 저장
- 비교: 모델 획득 evidence canonical 재검증, supported candidate·관측·결론의 label 비교만 제공

기존 Scenario Lab 실행 파일과 v3.5 prompt/validator/scorer/정책은 변경하지 않았습니다. Runtime loop는 별도 파일이며 기존 네 합성 사례에서 request payload와 결과의 동등성을 오프라인 확인했습니다. Artifact는 base v3.5 execution identity와 runtime adapter/driver source hashes를 함께 기록합니다. 기존 합성 평가 수치를 새 runtime 경로의 실측 정확도로 재사용하지 않습니다.

Canonical digest는 로컬 파일의 일관성 확인이며 원격 서버 인증이나 원본 위변조 방지 서명을 뜻하지 않습니다. Replay는 저장 시점 freshness를 사용하며 현재 서비스 상태를 판정하지 않습니다.

## Commands

```powershell
$runtimeInputs = Get-ChildItem results/ops-agent/shadow/20261029-observation-01/bundles/sample-*.json |
    Sort-Object Name | ForEach-Object FullName
.venv\Scripts\python.exe scripts\run_runtime_diagnosis_v3.py `
  --conditions results/ops-agent/shadow/20261029-observation-01/conditions.json `
  --input $runtimeInputs `
  --output-dir results/ops-agent/shadow/<new-preparation-directory>
```

기본 실행은 verified PRESENT에 대해 projection과 deterministic Runbook만 저장합니다. 모델 비교를 실행할 때 `--live`를 추가합니다. 기존 output directory는 덮어쓰지 않습니다. CLI는 file당 16 MiB, sequence 합계 64 MiB, 최대 256개 입력으로 제한합니다.

## Validation and evidence

- Adapter·runtime loop·wire parity 관련 `21 passed in 5.08s`
- 전체 suite `871 passed in 50.38s`
- Source reorder·digest mismatch·missing/stale/null replica·generation mismatch·PG component conflict 거부 확인
- 단일 snapshot replica shortfall과 grace 미확정 구분, normal activation의 API/key 접근 차단 확인
- Tool 반복·미허용 도구·조기 종료 거부, repair 1회·tool-free 제한 확인
- 실제 4개 요청 schema/citation audit·획득 evidence canonical replay·plan source hashes 일치
- 기존 v3.5 execution identity와 사전 등록 frozen source hashes 유지 확인
- [추적 summary·모델 원문·projection·hash inventory](../results/ops-agent/diagnosis/runtime-adapter-20261030-summary.json)
- 실제 재생 원본: `results/ops-agent/shadow/20261030-runtime-adapter-historical-live/`
- 관련 검사 log: `results/validation/20261029T150556Z-f1415145/`
- 정상 snapshot log: `results/validation/20261029T150612Z-36370267/`
- 과거 snapshot 준비 log: `results/validation/20261029T150645Z-047c1956/`
- 실제 모델 재생 log: `results/validation/20261029T150724Z-b15a8b34/`
- 전체 suite log: `results/validation/20261029T150652Z-f38c7097/`

다음 검증 대상은 운영 stage 기준선과 추가 causal discriminator, 설명문 품질, 새로운 실제 incident의 반복 관찰입니다. 새로운 장애 주입과 workload 생성은 이번 범위에서 수행하지 않았습니다. Snapshot 재생 경로를 마련했으므로 향후 모델 비교도 동일한 입력으로 진행할 수 있습니다.
