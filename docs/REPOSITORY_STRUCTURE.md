# 저장소 구조

프로젝트의 디렉터리와 주요 파일 역할을 정리한 문서입니다.

## 디렉터리 구조
```text
.
├─ alembic/                   # DB schema version scripts
├─ docs/                      # 프로젝트 문서
├─ infra/                     # AWS IaC(Terraform) 코드
├─ k8s/                       # Kubernetes 배포/검증 리소스
├─ monitoring/                # Prometheus/Grafana 설정
├─ observer/                  # 관측 UI/보조 서비스 코드
├─ portfolio/                 # FastAPI 애플리케이션 본체
├─ scripts/                   # 운영/테스트 자동화 스크립트
├─ tools/                     # 로컬 검증용 바이너리(kind/helm 등)
├─ worker/                    # 비동기 처리 워커
└─ README.md                  # 프로젝트 개요
```

## 폴더 설명
- `portfolio/`: API 엔드포인트, 설정, Kafka publish, DB/state 연결 로직
- `worker/`: Kafka consume, DB 저장, 재시도, DLQ 재처리
- `scripts/`: quick start, 장애 재현, 성능 측정, 백업/복구 스크립트
- `infra/`: AWS 배포용 Terraform 환경/모듈
- `monitoring/`: Prometheus 규칙, Grafana 대시보드 설정
- `k8s/`: 배포/스케일링/검증 매니페스트
- `docs/`: 실행 가이드, 아키텍처, 테스트 결과 문서
- `alembic/`: schema version history
- `observer/`: 처리 상태/지표 확인용 보조 UI
- `tools/`: 로컬 실험용 도구 바이너리

## 주요 파일 설명
- `Dockerfile`: Kubernetes에서 실행할 애플리케이션 이미지 빌드 기준
- `k8s/app/manifests-ha.yaml`: 로컬 HA 검증용 통합 매니페스트
- `k8s/gitops/base/manifests-ha.yaml`: GitOps 기준 통합 매니페스트
- `k8s/app/k6-job.yaml`: 클러스터 내부 부하 테스트 Job
- `requirements.txt`: Python 패키지 의존성 목록
- `alembic.ini`: Alembic 실행 설정
- `.env.example`: 환경변수 샘플
- `README.md`: 프로젝트 개요, 아키텍처, 결과 요약
- `scripts/load_test_k6.js`: k6 공통 시나리오/결과 출력 정의
- `monitoring/prometheus/alerts.yml`: 장애 감지 임계치 알람 규칙
- `infra/terraform/envs/dev/main.tf`: AWS dev 환경 진입점
- `k8s/scripts/install-keda.ps1`: Worker autoscaling용 KEDA 설치
- `k8s/scripts/install-kube-state-metrics.ps1`: Grafana replica 관측용 kube-state-metrics 설치
- `scripts/quick_start_all.sh`: Linux quick start
- `scripts/install_linux_prereqs.sh`: Ubuntu / Debian 계열 Linux 사전 도구 설치
- `scripts/run_recommended_tests.ps1`: 권장 테스트 순서 일괄 실행

## 문서 연결
- 빠른 실행: [QUICK_START.md](QUICK_START.md)
- 아키텍처: [ARCHITECTURE.md](ARCHITECTURE.md)
- 신뢰성 정책: [RELIABILITY_POLICY.md](RELIABILITY_POLICY.md)
- 관측 지표 안내: [OBSERVABILITY.md](OBSERVABILITY.md)
- AWS IaC 설계: [AWS_IAC_PLAN.md](AWS_IAC_PLAN.md)
- 테스트 결과: [TEST_RESULTS.md](TEST_RESULTS.md)
- 운영 정리: [OPERATIONS.md](OPERATIONS.md)
- Runbook: [RUNBOOK.md](RUNBOOK.md)
