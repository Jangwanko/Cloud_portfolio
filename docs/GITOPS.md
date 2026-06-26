# GitOps / Argo CD

## Purpose

- Git 기준 desired state를 Kubernetes에 반영한다.
- 로컬 `kind` 환경에서 Argo CD sync 흐름을 검증한다.
- AWS EKS 같은 원격 클러스터로 확장 가능한 배포 구조를 설명한다.

## Components

- `k8s/gitops/base`
  - HA application manifest를 묶는 Kustomize base
- `k8s/gitops/overlays/local-ha`
  - 로컬 `kind` HA 환경용 sync path
- `k8s/argocd/project-messaging-portfolio.yaml`
  - Argo CD `AppProject`
- `k8s/argocd/application-messaging-portfolio-local-ha.example.yaml`
  - 예시 `Application`
- `k8s/scripts/install-argocd.ps1`
  - Argo CD 설치
- `k8s/scripts/bootstrap-argocd-app.ps1`
  - Git repository URL과 revision으로 `Application` 생성
- `scripts/quick_start_gitops.ps1`
  - GitOps quick start

## Bootstrap vs GitOps

- Bootstrap:
  - cluster 생성
  - ingress / metrics-server / TLS 설치
  - HA PostgreSQL / Kafka runtime 설치
  - Argo CD 설치
- GitOps-managed runtime:
  - Argo CD가 `k8s/gitops/overlays/local-ha` sync
  - manifest 변경은 Git commit 기준 반영
  - 직접 `kubectl apply` 반복 제외

## Sync Strategy

- 기본 revision: `master`
- HPA 관리 replica drift 처리:
  - `RespectIgnoreDifferences=true`
  - `/spec/replicas` ignore rule
- local-path storage 특성:
  - `postgres-backups` PVC는 첫 backup CronJob 전 `WaitForFirstConsumer` 가능
  - 해당 상태가 Application health를 계속 `Progressing`으로 잡지 않도록 health customization 적용

## Run

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_gitops.ps1 `
  -RepoUrl https://github.com/<your-account>/<your-repo>.git `
  -Revision master
```

## Script Flow

- local cluster bootstrap
- HA PostgreSQL / Kafka runtime 설치
- Argo CD 설치
- `messaging-portfolio-local-ha` Application 생성
- readiness 확인
- smoke test 실행

## Verified Behavior

- Git remote에 검증할 revision push
- Argo CD `Application`이 해당 revision 바라봄
- initial sync로 application stack 생성
- `messaging-portfolio-local-ha` 상태:
  - `Synced`
  - `Healthy`
- manifest 변경 commit / push
- Argo CD가 새 revision 반영
- drift가 원하는 값으로 복구됨

## Status Check

- 전체 상태 확인:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_portfolio_status.ps1
```

- 함께 보는 항목:
  - Argo CD `Synced / Healthy`
  - workload readiness
  - Kafka exporter lag
  - KEDA 상태
  - backup PVC 상태

## GitHub Actions Boundary

- CI 역할:
  - Python syntax / test 확인
  - Docker image build 확인
  - Kustomize manifest render 확인
- Argo CD 역할:
  - Git에 선언된 manifest를 cluster에 반영
- image 배포 확장 시 필요한 흐름:
  - image registry push
  - image tag 갱신
  - manifest commit
  - Argo CD sync

## Operating Notes

- 로컬 데모:
  - image build
  - kind image load
  - rollout restart
- GitOps 데모:
  - Git commit 기준 sync
  - `check_portfolio_status.ps1`로 상태 확인
- Argo CD는 CI 도구가 아니다.
- test / lint / image build 검증은 CI에서 분리한다.
