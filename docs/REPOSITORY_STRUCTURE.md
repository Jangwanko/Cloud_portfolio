# 저장소 구조

프로젝트의 디렉터리와 주요 파일 역할을 정리한 문서입니다.

## 디렉터리 구조
```text
.
├─ alembic/                   # DB schema version scripts
├─ demo/                      # 브라우저로 여는 정적 데모 화면
├─ docs/                      # 프로젝트 문서
├─ infra/                     # AWS IaC(Terraform) 코드
├─ k8s/                       # Kubernetes 배포/검증 리소스
├─ monitoring/                # Prometheus/Grafana 설정
├─ ops_agent/                 # evidence, grounded investigation, incident search + versioned evaluation
├─ portfolio/                 # FastAPI 애플리케이션 본체
├─ results/                   # tracked evidence + ignored local raw archives
├─ scenario_lab/              # 과거 합성 진단 시나리오의 정적 replay
├─ scripts/                   # 운영/테스트 자동화 스크립트
├─ tools/                     # ignored local binaries + tracked PostgreSQL HA chart archive
├─ worker/                    # 비동기 처리 워커
├─ README.md                  # 국내 Cloud/DevOps 지원용 개요
└─ README_EN.md               # Canada/해외 지원용 영어 개요
```

## 폴더 설명
- `portfolio/`: API 엔드포인트, 설정, Kafka publish, DB/state 연결 로직
- `worker/main.py`: ingress / notification Kafka consumer, DB 저장, retry, DLQ 이동
- `worker/dlq_replayer.py`: replay guard와 ingress 재주입
- `demo/`: 범용 처리 흐름을 주문 lifecycle reference scenario로 보여주는 정적 화면
- `scripts/`: quick start, 장애 재현, 성능 측정, 백업/복구 스크립트
- `infra/`: AWS migration blueprint용 Terraform 환경/모듈
- `monitoring/`: Prometheus 규칙, Grafana 대시보드 설정
- `ops_agent/`: 현재 grounded investigation·사건 경험 검색, collectors·condition/recovery·incident lifecycle, 과거 비교용 diagnosis 계약과 fixtures
- `k8s/`: 배포/스케일링/검증 매니페스트
- `docs/`: 실행 가이드, 아키텍처, 테스트 결과 문서
- `alembic/`: schema version history
- `tools/`: bootstrap 뒤 생성되는 ignored local binaries와 재현용 vendored Helm chart source
- `results/`: 마지막 성능/ordering 원본과 provenance guide; allowlist된 Ops Agent live capture 외 중간 산출물 기본 추적 제외

## 주요 파일 설명
- `Dockerfile`: digest-pinned Python base, BuildKit pip cache, bytecode 제외, UID/GID `10001` non-root 실행, 요청별 Uvicorn access log·server header 제외
- `k8s/app/manifests-ha.yaml`: 로컬 HA 검증용 통합 매니페스트
- `k8s/gitops/base/manifests-ha.yaml`: GitOps 기준 통합 매니페스트
- `k8s/gitops/base/migration-job.yaml`: Argo 일반 Sync wave `-2` Alembic migration Job
- `k8s/app/k6-job.yaml`: 클러스터 내부 부하 테스트 Job
- `requirements.txt`: Python 패키지 의존성 목록
- `alembic.ini`: Alembic 실행 설정
- `.env.example`: 환경변수 샘플
- `results/README.md`: validation evidence 해석/갱신 규칙
- `results/kafka-performance/latest.txt`: 마지막 완료 performance suite 원본
- `results/ordering-failure/latest.json`: 마지막 ordering/failure injection 원본
- `results/postgres-restore/latest.json`: 마지막 disposable PostgreSQL restore 정합성 원본
- `results/postgres-recovery/latest.json`: 마지막 PostgreSQL restart/sync/cache/outage recovery tracked structured summary
- `ops_agent/README.md`: Phase 경계, evidence status/freshness, read-only·보안 계약
- `scripts/ops_agent_mvp.py`: 통제된 로컬 MVP preflight/execute/archive/search 진입점
- `scripts/assist_incident.py`: 기본 grounded assistance와 명시적 legacy/investigation 비교 모드
- `ops_agent/incident_experience.py`: hash 검증된 사건 경험 sidecar 저장·검색
- `ops_agent/controller.py`: source result를 `ops.evidence.v1` bundle로 정규화
- `ops_agent/models.py`: strict Evidence Bundle schema
- `ops_agent/evaluator.py`: frozen bundle을 source 재조회 없이 deterministic tri-state condition으로 평가
- `ops_agent/sequence_evaluator.py`: ordered bundle sequence의 calibrated backlog activation을 평가
- `ops_agent/sequence_models.py`: `ops.conditions.v2` policy, ordered source reference, output integrity schema
- `ops_agent/diagnosis_models.py`: `ops.diagnosis.v1`, hypothesis, step/stop, usage와 deterministic ID schema
- `ops_agent/diagnosis_tools.py`: normalized Evidence만 반환하는 fixed read-only tool registry
- `ops_agent/diagnosis_agent.py`: explicit live opt-in Responses API loop와 bounded cost/step policy
- `ops_agent/diagnosis_validator.py`: citation/tool/budget/rebalance/forbidden-claim validation
- `ops_agent/diagnosis_evals.py`: scripted golden artifact 평가 metric
- `ops_agent/recovery_evaluator.py`: frozen activation과 post-activation bundle의 deterministic ACTIVE/RECOVERING/RECOVERED 평가
- `ops_agent/recovery_models.py`: versioned recovery policy, evidence trace, output integrity schema
- `ops_agent/incident_lifecycle.py`: deterministic incident 생성, diagnosis/recovery attach, closure/current observation transition
- `ops_agent/incident_models.py`: `ops.incident.v1`, timeline, identity/record hash schema
- `ops_agent/evaluation_models.py`: strict `ops.conditions.v1` output, policy/profile, provenance 계약
- `ops_agent/calibration.py`: Evidence Bundle에서 rate·lag·replica·KEDA·PostgreSQL·Worker stage 값을 threshold 없이 요약
- `ops_agent/collectors/`: Application, Prometheus, Kubernetes, Argo CD fixed read-only collectors
- `ops_agent/policies/local-ha.yaml`: GitOps local-ha endpoint, resource, freshness, partition policy
- `ops_agent/fixtures/`: synthetic unit-test input; live capture와 분리
- `scripts/worker_backlog_calibration.py`: 기존 KEDA 정책을 유지한 multi-stream 3-run evidence timeline runner
- `scripts/worker_backlog_negative_controls.py`: frozen pressure candidate의 short/sustainable/transient false-positive calibration runner
- `scripts/worker_recovery_calibration.py`: Phase 4 arrival-rate operating envelope와 recovery evidence runner
- `scripts/worker_recovered_calibration.py`: recovery v2 MEDIUM re-entry supplemental runner
- `scripts/worker_incident_e2e.py`: strict workload gate를 적용한 actual local-ha Phase 5.1 incident orchestrator
- `results/ops-agent/live-baseline/no-backlog-20260812.json`: 2026-08-12 captured no-backlog operations reference
- `results/ops-agent/live-baseline/no-backlog-20260812.conditions.json`: captured bundle의 deterministic derived result
- `results/ops-agent/diagnosis/golden-eval-v1.json`: Phase 3 offline golden evaluation summary
- `results/ops-agent/incident-e2e/`, `results/ops-agent/incidents/`: Phase 5 live/canonical local artifacts; 기본 Git 추적 제외
- `README.md`: 국내 지원용 프로젝트 개요, 아키텍처, 결과 요약
- `README_EN.md`: 동일한 검증 경계를 유지하는 영어권 지원용 개요
- `demo/order-dashboard.html`: Reliable Event Processing System 흐름과 order reference payload를 보여주는 브라우저 데모; 파일명은 URL 호환을 위해 유지
- `scripts/load_test_k6.js`: k6 공통 시나리오/결과 출력 정의
- `scripts/ordering_failure_injection.py`: single/multi stream ordering, Pgpool 장애 주입, PostgreSQL row evidence 검증
- `monitoring/prometheus/alerts.yml`: 장애 감지 임계치 알람 규칙
- `infra/terraform/envs/dev/main.tf`: AWS dev 환경 진입점
- `k8s/scripts/install-keda.ps1`: Worker autoscaling용 KEDA 설치
- `k8s/scripts/install-kube-state-metrics.ps1`: Grafana replica 관측용 kube-state-metrics 설치
- `scripts/quick_start_all.sh`: Linux quick start
- `scripts/install_linux_prereqs.sh`: Ubuntu / Debian 계열 Linux 사전 도구 설치
- `scripts/run_recommended_tests.ps1`: 권장 테스트 순서 일괄 실행

## Windows Tool Bootstrap

- `scripts/bootstrap_tools.ps1`: Windows quick start용 `kind`, `kubectl`, `helm` 자동 준비 스크립트
- `tools/kind.exe`, `tools/kubectl.exe`, `tools/helm/windows-amd64/helm.exe`: bootstrap이 내려받는 ignored local files, Git 추적 제외
- `tools/helm-cache/repository/postgresql-ha-16.3.2.tgz`: PostgreSQL HA manifest 재현을 위해 의도적으로 추적하는 유일한 vendored chart archive
- 그 밖의 Helm repository metadata, download cache, unused chart: Git 추적 제외

## 문서 연결
- 서비스 요구사항: [SERVICE_REQUIREMENTS.md](SERVICE_REQUIREMENTS.md)
- 아키텍처: [ARCHITECTURE.md](ARCHITECTURE.md)
- 신뢰성 정책: [RELIABILITY_POLICY.md](RELIABILITY_POLICY.md)
- 관측 지표 안내: [OBSERVABILITY.md](OBSERVABILITY.md)
- Ops Agent evidence 계약: [OPS_AGENT.md](OPS_AGENT.md)
- AWS IaC 설계: [AWS_IAC_PLAN.md](AWS_IAC_PLAN.md)
- 테스트 결과: [TEST_RESULTS.md](TEST_RESULTS.md)
- 개선 로드맵: [IMPROVEMENT_ROADMAP.md](IMPROVEMENT_ROADMAP.md)
- 운영 정리: [OPERATIONS.md](OPERATIONS.md)
- Runbook: [RUNBOOK.md](RUNBOOK.md)
- 서비스 프로세스 점검표: [SERVICE_PROCESS_CHECKLIST.md](SERVICE_PROCESS_CHECKLIST.md)

## 개발 에이전트 문서

- 작업별 context 선택과 공통 계약: [root AGENTS.md](../AGENTS.md)
- Human / Codex 역할, 검증 피드백, 문서 작성과 rollback 규칙: [AI_ENGINEERING_WORKFLOW.md](AI_ENGINEERING_WORKFLOW.md)
- 개발 환경과 반복 검증 명령: [QUICK_START.md](QUICK_START.md)

## 2026-11-03 일회 유지보수 감사

기준은 원격 `master` `12f3890eb36f604f6c63ecceeb076c0b936f5624`, 작업 브랜치는 `cleanup/repository-maintenance-20261103`이다. Git 추적 파일 495개 전체를 대상으로 import·CLI·테스트·문서 링크·CI/Docker/GitOps 참조와 동일 내용 파일을 확인했다. 아래 분류는 이 기준점에 대한 보존 판단이며 향후 자동 삭제 규칙이 아니다.

### 파일 분류와 보존 근거

아래 예외를 우선 적용하고 나머지 추적 파일은 ACTIVE로 분류한다. 따라서 모든 기준점 파일에 분류가 하나씩 적용된다. LEGACY는 과거 비교·검증 재현용 실행 코드, ARCHIVE는 실행 입력이 아닌 보존 증거, SAFE_DELETE는 참조와 증거 가치가 없는 항목이다.

| 분류 | 대상 | 판단 근거 |
| --- | --- | --- |
| ACTIVE | 나머지 추적 파일 전체 | 현재 실행·배포·테스트·운영 안내에 사용. 버전이 붙은 fixture·migration·정책도 포함 |
| ACTIVE | `results/README.md`, `results/ops-agent/diagnosis/README.md`, `results/release-failure/README.md` | 보존 증거의 현재 안내 문서 |
| ACTIVE | `results/ops-agent/live-baseline/`와 해당 `results/ops-agent/raw/ac4d45ac-6280-4eb7-9d35-ea735243f00b/` | evaluator·CLI·recovery 테스트의 captured input과 raw provenance |
| ACTIVE | `results/ops-agent/calibration/20260816T032411Z/run-01/summary.json`, `run-02/summary.json`, `run-03/summary.json` | `test_calibration.py`가 읽는 frozen positive 사례 |
| ACTIVE | `results/kafka-performance/worker-ab-fixed.txt`, `worker-ab-keda.txt` | `test_infrastructure_contracts.py`가 읽는 과거 A/B 증거 |
| LEGACY | 아래 명시한 Ops Agent 모듈·스크립트 | 현재 MVP와 별도로 유지되는 과거 계약·평가·재현 경로 |
| ARCHIVE | 위 ACTIVE 예외를 제외한 `results/` 추적 파일 | 성능·장애·복구·모델 평가 원본 또는 공유 요약; hash와 provenance 보존 |
| ARCHIVE | `docs/harness/*.json`, `docs/superpowers/plans/2026-06-18-order-event-service-surface.md` | 과거 실행 증거와 설계 기록 |
| SAFE_DELETE | 이 작업 트리의 `__pycache__/`, `.pytest_cache/` | 재생성 가능한 ignored 캐시; 추적 파일·실험 증거 제외 |

LEGACY 모듈은 다음과 같다. `ops_agent/` 기준 상대 경로이며, 공통 transport·모델·collector와 현재 사용되는 dependency는 나머지 ACTIVE 규칙을 따른다.

- `diagnosis_evals.py`, `diagnosis_quality.py`, `diagnosis_quality_v3.py`, `diagnosis_review.py`, `diagnosis_scenarios.py`
- `diagnosis_v2_models.py`, `diagnosis_v2_validator.py`, `diagnosis_v3_contract.py`, `diagnosis_v3_models.py`, `diagnosis_v3_validator.py`
- `citation_selection_v3.py`, `diagnosis_repair_v3.py`, `investigation_gate_v3.py`, `fixed_runbook.py`
- `scenario_agent.py`, `scenario_agent_v3.py`, `scenario_contract.py`, `runtime_agent_v3.py`, `runtime_evidence_v3.py`, `followup_replay.py`
- `recovery_calibration.py`, `recovered_calibration.py`

LEGACY 스크립트는 `scripts/` 기준 다음 파일이다.

- `audit_incident_followups.py`, `audit_live_experiments.py`, `audit_negative_lag_history.py`, `audit_runtime_case_candidates.py`
- `build_diagnosis_v35_probe.py`, `build_diagnosis_v3_novel_cases.py`, `build_scenario_lab_artifact.py`, `export_diagnosis_v3_candidate.py`
- `compare_diagnosis_runbook.py`, `evaluate_diagnosis_quality.py`, `review_diagnosis_pilot.py`, `run_diagnosis_pilot.py`, `run_diagnosis_v3_pilot.py`, `run_diagnosis_repair_regression.py`
- `run_runtime_diagnosis_v3.py`, `replay_followup_diagnosis.py`
- `worker_backlog_calibration.py`, `worker_backlog_negative_controls.py`, `worker_recovery_calibration.py`, `worker_recovered_calibration.py`

테스트만 직접 참조하는 builder/exporter도 과거 fixture·계약·정적 replay 생성에 연결되어 보존했다. `python -m ops_agent diagnose`는 v1, scenario 경로는 v2, runtime 진단은 v3 계열 계약이며, 현재 기본 assistance와 명시적 `--legacy-assistance`·`--investigate`도 서로 다른 경로다. [CLI 안내](../ops_agent/README.md)의 계약을 유지하고 테스트를 삭제하지 않았다.

### 정리 범위와 남긴 중복

- 추적 파일 삭제 0개. 사용되지 않고 export되지 않는 import 이름 7개 제거: `diagnosis_evals.DiagnosisStopReason`, `diagnosis_tools.datetime`, `incident_lifecycle.IncidentTimelineEvent`, `recovery_models.BaseModel`, `recovery_policies.json`, `scripts/ops_agent_mvp.py`의 `datetime`·`timezone`
- Quick Start의 동일 Windows 도구 목록과 이 문서의 중복 Quick Start 링크 제거; 현재 MVP·사건 검색·정적 scenario replay 위치 명시
- 로컬 캐시 디렉터리 11개, 파일 105개, 3,522,361 bytes 제거. 기존 `results/validation/20260925T151907Z-350ffa58/`와 Terraform provider cache는 보존
- `k8s/app/manifests-ha.yaml`과 `k8s/gitops/base/manifests-ha.yaml`: CI의 동일성 검사와 별도 설치/GitOps 경로가 요구하는 복사본
- `results/kafka-performance/latest.txt`와 `notification-batch-keda-run3.txt`: 마지막 완료 결과와 3회 반복 원본의 역할 유지
- `results/outbox-failure/20260905T144053Z/snapshot-*.json`: 동일 내용도 서로 다른 관측 시점이며 `hashes.json`·실험 요약과 함께 보존
- `tools/helm-cache/repository/postgresql-ha-16.3.2.tgz`: CI Helm render가 직접 쓰는 vendored chart. Terraform lock 파일과 배포된 `scenario_lab` JSON도 의도된 추적 산출물
- PowerShell/Python 상태 CLI는 wrapper와 구현, PowerShell/bash 운영 CLI는 OS별 실행 경로로 보존

정리 전 Markdown 46개·JSON 126개 검사에서 깨진 로컬 링크·anchor·JSON 오류는 0개였다. 코드·스크립트 파일 6개의 미사용 import 정리와 문서 2개만 변경하며, 추적 파일 수는 495개로 유지한다. 실행 경로 비교·전체 회귀·최종 diff 검사는 PR의 검증 결과에 기록한다. 새 검증기·하네스·모델 실험은 추가하지 않는다.

### 브랜치와 로컬 작업 트리

| 브랜치 | 감사 시점 상태 / 용도 | 처리 |
| --- | --- | --- |
| `master` | `12f3890`, 원격과 일치. 최종 통합·기본 GitOps revision | cleanup PR만 제출, 원격 병합은 사용자 승인 대기 |
| `dev-kafka` | 로컬 `ffd2d1f`, 원격 `5d7444e`보다 1 commit 앞섬. 일반 개발·검증 경로 | 유지. `564b112`의 별도 PG Startup Probe 후보는 master에 복사하지 않음 |
| `demo-dev` | 로컬·원격 `c4b8dea`, 별도 demo 작업 트리 | 유지 |
| `demo-lite` | 로컬 `eec800b`, 원격 `0683d61`보다 7 commits 뒤. 축소 데모 배포 | 유지, 동기화·전파 제외 |
| `dev`, `ops` | 각각 `f077b20`, `811aa8d`, 로컬·원격 일치. master ancestry에 포함된 과거 경로 | 삭제 검토 후보만 보고. `ops`는 CI push 대상이므로 운영 용도 확인 필요 |
| `origin/codex/portfolio-review-workflow` | `6c82093`, master ancestry에 포함된 과거 문서 작업 | 원격 삭제 후보만 보고 |

기존 작업 트리는 `Cloud_portfolio`(detached `12f3890`), `Cloud_portfolio-master`(이번 cleanup), `Cloud_portfolio-demo-dev`의 3개다. 원본 `Cloud_portfolio`에는 `scripts/build_diagnosis_v35_probe.py`·`scripts/build_diagnosis_v3_novel_cases.py`의 미커밋 사용자 변경이 있어 보존했다. 추가 worktree를 만들거나 기존 worktree·브랜치를 삭제하지 않았다. Git-ignored PostgreSQL·Worker 실험 원본과 Incident Record는 clone만으로 복원할 수 없으므로 정리 대상에서 제외했다.

후속 후보는 원격 과거 작업 브랜치의 운영 용도 확인과 로컬 원본이 필요한 과거 평가 CLI의 장기 보존 위치 결정이다. 이번에는 애매한 파일을 유지하고 한 차례로 종료한다. Kubernetes·Terraform·GitOps 설정, 현재 image tag, Docker/Kubernetes 리소스·DB·PVC/PV/Namespace는 변경하지 않는다.
