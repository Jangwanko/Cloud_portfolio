# Kubernetes HA Design (Local Practice)

이 폴더는 DB 자동 failover 와 quorum 구조를 로컬에서 실습하기 위한 설정입니다.

## 목표
- PostgreSQL: `primary 1 + replicas 2` 기반 자동 failover
- Redis: `master 1 + replicas 2` + Sentinel quorum 기반 자동 failover
- App 은 PostgreSQL / Redis 를 동시에 사용

## 구성
- PostgreSQL HA: `bitnami/postgresql-ha` chart + `bitnamilegacy/*` runtime images
  - total postgres nodes: 3
  - topology: primary 1 + replicas 2
  - pgpool enabled
  - 로컬 데모 readiness는 async streaming standby를 정상으로 해석
  - 3노드 중 과반 생존을 기준으로 새 primary 승격 판단
- Redis HA: `bitnami/redis`
  - master 1 + replicas 2
  - sentinel 3
  - quorum 2

## 1) kind 클러스터 생성
```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/setup-kind.ps1
```

## 2) HA 스택 설치
```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/install-ha.ps1 -Namespace messaging-app
```

추가 운영 컴포넌트:

```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/install-kube-state-metrics.ps1 -Namespace messaging-app
powershell -ExecutionPolicy Bypass -File k8s/scripts/install-keda.ps1
```

## 3) 앱 연결 포인트
- PostgreSQL endpoint: `messaging-postgresql-ha-pgpool.messaging-app.svc.cluster.local:5432`
- Redis endpoint (sentinel):
  - `messaging-redis-node-0.messaging-redis-headless.messaging-app.svc.cluster.local:26379`
  - `messaging-redis-node-1.messaging-redis-headless.messaging-app.svc.cluster.local:26379`
  - `messaging-redis-node-2.messaging-redis-headless.messaging-app.svc.cluster.local:26379`

앱 배포:
```powershell
kubectl apply -f k8s/app/manifests-ha.yaml
```

## 4) 페일오버 테스트
- PostgreSQL primary pod 강제 삭제 -> quorum 충족 replica 가 새 primary 로 승격
- Redis master pod 강제 삭제 -> sentinel quorum 으로 replica 승격

## 5) 관측 스택
Prometheus + Grafana 로 아래 항목을 관측합니다.

- API: request rate, latency p95/p99, stage latency, readiness 실패 횟수
- PostgreSQL: primary reachability, standby count, sync standby count, replication state, replication lag
- Redis: role, connected replicas, replica link, Sentinel master/quorum, queue depth, queue wait, reconnect event
- Worker: event processed count, success/failure rate, processing latency, stage latency, accepted-to-persisted lag
- Kubernetes: worker replica count, KEDA desired replicas, pod restart count, CPU/memory, node disk usage, network I/O

autoscaling 기준:
- API: CPU HPA
- Worker: KEDA queue depth scaling

## GitOps / Argo CD
이 저장소는 기존 `kubectl apply -f k8s/app/manifests-ha.yaml` 경로 외에 Argo CD 로 관리할 수 있는 GitOps 경로도 포함합니다.

- GitOps sync path: `k8s/gitops/overlays/local-ha`
- Argo CD project manifest: `k8s/argocd/project-messaging-portfolio.yaml`

Argo CD 설치:

```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/install-argocd.ps1
```

Argo CD application bootstrap:

```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/bootstrap-argocd-app.ps1 `
  -RepoUrl https://github.com/<your-account>/<your-repo>.git `
  -Revision ops
```

부트스트랩 단계에서는 여전히 cluster, ingress, metrics-server, TLS, HA data store 설치를 먼저 해야 합니다.
그 이후 앱 매니페스트 반영은 Argo CD가 Git 원하는 상태(`desired state`) 기준으로 동기화합니다.

로컬 검증 기준 브랜치는 현재 `ops` 로 두고 있으며, 이후 실제 운영 배포 기준 브랜치는 `master` 로 연결할 수 있습니다.

## 참고
- 기본 앱 실행은 `k8s/app/manifests-ha.yaml` 기준이며, PostgreSQL / Redis 는 HA 구성을 전제로 합니다.
- HA 실습은 quorum 기반 failover, queue buffering, queue-depth autoscaling 검증에 초점을 둡니다.
