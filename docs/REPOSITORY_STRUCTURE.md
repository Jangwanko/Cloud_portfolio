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
├─ ops_agent/                 # evidence + condition/diagnosis/recovery + incident lifecycle
├─ portfolio/                 # FastAPI 애플리케이션 본체
├─ results/                   # Git 추적 latest validation evidence
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
- `ops_agent/`: Phase 1 collectors, Phase 2 evaluators, Phase 3 bounded Diagnosis Agent, Phase 4 recovery evaluator, Phase 5 incident lifecycle, versioned policy와 fixtures
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
- 빠른 실행: [QUICK_START.md](QUICK_START.md)
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
