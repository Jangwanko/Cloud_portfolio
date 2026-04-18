# GitOps / Argo CD

이 문서는 이 저장소에 추가한 `GitOps` / `Argo CD` 경로를 정리한 문서입니다.
현재 프로젝트는 기존 `kubectl apply` 기반 실행 경로를 유지하면서도, 면접에서 `GitOps` 요구사항을 설명할 수 있도록 별도 Git 기반 동기화 경로를 함께 제공합니다.

## 무엇을 추가했는가
- `k8s/gitops/base`
  - 기존 HA 애플리케이션 매니페스트를 GitOps 진입점으로 묶는 `Kustomize base`
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

## 왜 Bootstrap 과 GitOps 를 분리했는가
`GitOps` 라고 해서 처음부터 끝까지 전부 Argo CD가 대신하는 것은 아닙니다.
초기에는 여전히 사람이 cluster 와 controller 를 준비하는 `bootstrap` 단계가 필요합니다.

현재 저장소 기준 역할 분리는 아래와 같습니다.

- bootstrap
  - cluster 생성
  - ingress / metrics-server / TLS 설치
  - HA PostgreSQL / HA Redis 설치
  - Argo CD 자체 설치
- GitOps-managed application runtime
  - Argo CD가 `k8s/gitops/overlays/local-ha` 를 동기화
  - 앱 매니페스트 변경은 직접 `kubectl apply` 하지 않고 Git 원하는 상태(`desired state`) 기준으로 반영

즉 이 프로젝트는
`초기 1회 bootstrap 은 수동`,
`그 이후 애플리케이션 반영은 GitOps`
라는 구조를 보여주도록 설계했습니다.

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
- HA PostgreSQL / Redis 설치
- Argo CD 설치
- `messaging-portfolio-local-ha` Application 생성
- readiness 확인
- smoke test 실행

## 현재 구조의 의미
이 경로를 추가함으로써 이 저장소는 단순히
`Kubernetes 에 수동 배포하는 포트폴리오`
가 아니라,
`Git 에 선언된 상태를 기준으로 앱을 동기화하는 GitOps 포트폴리오`
로 설명할 수 있습니다.

특히 면접에서는 아래처럼 설명하면 자연스럽습니다.

- 로컬 `kind` 환경에서 GitOps 흐름을 재현했다.
- bootstrap 단계와 GitOps 관리 범위를 의도적으로 분리했다.
- 동일한 구조를 AWS `EKS` 같은 managed Kubernetes 환경으로 확장할 수 있도록 방향을 잡아두었다.

## 주의 / 한계
- 현재 앱 이미지는 여전히 `messaging-portfolio:local` 이므로, 로컬 데모에서는 image build 후 kind 에 load 하는 단계가 먼저 필요합니다.
- Argo CD는 `deployment controller` 이지 테스트 프레임워크가 아닙니다.
  - lint / test / image build 검증은 이후 `CI` 에서 분리하는 것이 맞습니다.
- AWS 나 다른 cloud 환경으로 확장하려면 이후 단계에서 아래 항목이 추가로 필요할 수 있습니다.
  - image registry
  - external secret management
  - environment 분리
  - CI 와 이미지 태그 전략
