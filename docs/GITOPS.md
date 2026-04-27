# GitOps / Argo CD

이 문서는 이 저장소에 추가한 `GitOps` / `Argo CD` 경로를 정리한 문서입니다.
현재 프로젝트는 직접 배포 경로와 Git 기반 동기화 경로를 함께 제공합니다.

## 목적
- 로컬 `kind` 환경에서도 GitOps 흐름을 재현
- Argo CD 를 통해 Git 의 원하는 상태(`desired state`)를 클러스터에 반영
- AWS `EKS` 같은 외부 클러스터로 확장 가능한 구조를 설명

## 추가한 구성 요소
- `k8s/gitops/base`
  - HA 애플리케이션 매니페스트를 GitOps 진입점으로 묶는 `Kustomize base`
- `k8s/gitops/overlays/local-ha`
  - 로컬 `kind` HA 환경에서 Argo CD가 바라보는 sync path
- `k8s/argocd/project-messaging-portfolio.yaml`
  - Argo CD `AppProject`
- `k8s/argocd/application-messaging-portfolio-local-ha.example.yaml`
  - 예시 `Application` 매니페스트
- `k8s/scripts/install-argocd.ps1`
  - 클러스터에 Argo CD를 설치하는 스크립트
- `k8s/scripts/bootstrap-argocd-app.ps1`
  - Git repository URL과 revision을 받아 `Application` 을 생성하는 스크립트
- `scripts/quick_start_gitops.ps1`
  - 로컬 GitOps 흐름을 한 번에 실행하는 quick start 스크립트

## Bootstrap 과 GitOps 를 분리한 이유
`GitOps`라고 해서 처음부터 끝까지 전부 Argo CD가 대신하는 것은 아닙니다.
초기에는 여전히 사람이 cluster 와 controller 를 준비하는 `bootstrap` 단계가 필요합니다.

현재 저장소 기준 역할 분리는 아래와 같습니다.

- bootstrap
  - cluster 생성
  - ingress / metrics-server / TLS 설치
  - HA PostgreSQL / Kafka runtime 설치
  - Argo CD 설치
- GitOps-managed runtime
  - Argo CD가 `k8s/gitops/overlays/local-ha` 를 동기화
  - 앱 매니페스트 변경은 직접 `kubectl apply` 하지 않고 Git 원하는 상태(`desired state`) 기준으로 반영

즉 이 프로젝트는
`초기 1회 bootstrap 은 수동`,
`그 이후 애플리케이션 반영은 GitOps`
라는 구조를 보여주도록 설계했습니다.

## Sync Strategy
GitOps 검증은 Git remote의 특정 revision을 Argo CD `Application`이 바라보게 하는 방식으로 수행합니다.

## 로컬 실행 방법
1. 이 저장소를 클러스터에서 접근 가능한 Git remote 에 push 합니다.
2. 아래 명령으로 GitOps quick start 를 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_gitops.ps1 `
  -RepoUrl https://github.com/<your-account>/<your-repo>.git `
  -Revision main
```

3. 스크립트는 아래 작업을 순서대로 수행합니다.
- local cluster bootstrap
- HA PostgreSQL / Kafka runtime 설치
- Argo CD 설치
- `messaging-portfolio-local-ha` Application 생성
- readiness 확인
- smoke test 실행

## 확인한 동작
아래 흐름을 실제로 확인했습니다.

- 검증할 revision을 원격 repository에 push
- Argo CD `Application` 을 해당 revision에 연결
- 초기 sync 로 애플리케이션 스택 생성
- 매니페스트 변경 commit / push
- Argo CD가 새 revision 을 읽고 deployment 상태를 갱신
- 클러스터 상태도 다시 원래 값으로 복귀

즉 이 프로젝트는 문서상으로만 GitOps 를 설명하는 것이 아니라, 로컬 Kubernetes 환경에서 실제 sync 동작까지 검증한 상태입니다.

## GitHub Actions 와의 관계
현재 저장소에는 기본 `GitHub Actions` CI 구성을 추가했습니다.

- Python 문법 검증
- Docker image build 확인
- Kustomize manifest render 확인

이 단계는 코드와 manifest가 배포 가능한 형태인지 빠르게 확인하는 역할을 합니다.

EKS까지 확장할 때는 보통 아래 단계가 이어집니다.
- image registry / ECR push
- 이미지 태그 갱신
- Argo CD 자동 동기화

## Operating Notes
- 로컬 데모에서는 앱 이미지를 build 한 뒤 kind 에 load 합니다.
- Argo CD는 `deployment controller` 이지 테스트 프레임워크는 아닙니다.
  - lint / test / image build 검증은 `CI` 에서 분리하는 것이 맞습니다.
- AWS 나 다른 cloud 환경으로 확장할 때는 아래 항목을 함께 설계합니다.
  - image registry
  - external secret management
  - environment 분리
  - CI 와 이미지 태그 전략
