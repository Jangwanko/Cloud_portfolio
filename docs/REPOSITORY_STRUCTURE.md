# Repository Structure

프로젝트의 디렉터리와 주요 파일 역할을 정리한 문서입니다.

## 디렉터리 구조
```text
.
├─ alembic/                   # DB 마이그레이션 스크립트
├─ docs/                      # 프로젝트 문서
├─ frontend/                  # 프론트엔드 정적 리소스
├─ k6/                        # 부하 테스트 시나리오 및 결과
├─ k8s/                       # Kubernetes 배포/검증 리소스
├─ monitoring/                # Prometheus/Grafana 설정
├─ nginx/                     # Reverse proxy 설정
├─ observer/                  # 관측 UI/보조 서비스 코드
├─ portfolio/                 # FastAPI 애플리케이션 본체
├─ scripts/                   # 운영/테스트 자동화 스크립트
├─ tools/                     # 로컬 검증용 바이너리(kind/helm 등)
├─ worker/                    # 비동기 처리 워커
└─ README.md                  # 프로젝트 개요
```

## 폴더 설명
- `portfolio/`: API 엔드포인트, 설정, DB/Redis 연결, 큐 처리 로직
- `worker/`: 큐 소비, DB 저장, 재시도, DLQ 재처리
- `scripts/`: 상태 점검, 재시작, 장애 재현 테스트 스크립트
- `monitoring/`: Prometheus 규칙, Grafana 대시보드 설정
- `nginx/`: API/Observer 라우팅과 진입점 제어 설정
- `k8s/`: 배포/스케일링/검증 매니페스트
- `k6/`: 부하 시나리오(`scenarios`)와 결과(`results`)
- `docs/`: 실행 가이드, 아키텍처, 테스트 결과 문서
- `alembic/`: 스키마 변경 이력과 마이그레이션 버전
- `observer/`: 처리 상태/지표 확인용 보조 UI
- `tools/`: 로컬 실험용 도구 바이너리

## 주요 파일 설명
- `Dockerfile`: Kubernetes에서 실행할 애플리케이션 이미지 빌드 기준
- `k8s/app/manifests.yaml`: 앱 배포, 서비스, HPA 매니페스트
- `k8s/app/k6-job.yaml`: 클러스터 내부 부하 테스트 Job
- `requirements.txt`: Python 패키지 의존성 목록
- `alembic.ini`: Alembic 실행 설정
- `.env.example`: 환경변수 샘플
- `README.md`: 프로젝트 개요, 아키텍처, 결과 요약
- `scripts/load_test_k6.js`: k6 공통 시나리오/결과 출력 정의
- `monitoring/prometheus/alerts.yml`: 장애 감지 임계치 알람 규칙

## 문서 연결
- 빠른 실행: [QUICK_START.md](QUICK_START.md)
- 아키텍처: [ARCHITECTURE.md](ARCHITECTURE.md)
- 테스트 결과: [TEST_RESULTS.md](TEST_RESULTS.md)
