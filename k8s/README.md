# Kubernetes HA Design (Local Practice)

이 폴더는 DB 자동 페일오버와 쿼럼 구조를 로컬에서 실습하기 위한 설정입니다.

## 목표
- PostgreSQL: `primary 1 + replicas 2` 기반 자동 failover
- Redis: `master 1 + replicas 2` + Sentinel quorum 기반 자동 failover
- App은 PostgreSQL/Redis를 동시에 사용

## 구성
- PostgreSQL HA: `bitnami/postgresql-ha` chart + `bitnamilegacy/*` runtime images
  - total postgres nodes: 3
  - topology: primary 1 + replicas 2
  - pgpool enabled
  - `synchronousCommit` + `numSynchronousReplicas: 1`
  - 3노드 중 과반 생존을 기준으로 새 primary 승격 판단
- Redis HA: `bitnami/redis`
  - master 1 + replicas 2
  - sentinel 3
  - quorum 2 (3개 중 2개 동의 시 master 승격)

## 1) kind 클러스터 생성
```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/setup-kind.ps1
```

## 2) HA 스택 설치
```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/install-ha.ps1 -Namespace messaging-app
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
- PostgreSQL primary pod 강제 삭제 -> quorum 충족 replica가 새 primary로 승격
- Redis master pod 강제 삭제 -> sentinel quorum으로 replica 승격

## 5) 관측 스택
Prometheus + Grafana로 아래 항목을 관측합니다.

- API: request rate, latency p50/p95/p99, error rate, readiness 실패 횟수
- PostgreSQL: up/down, active connections, replication lag, transaction rate, failover event
- Redis: memory usage, queue length, ops/sec, connected clients, reconnect event
- Worker: message processed count, success/failure rate, processing latency, retry count, queue lag
- Kubernetes: pod restart count, CPU/memory, node disk usage, network I/O

## 참고
- 기본 실행 구성은 단일 DB/Redis입니다.
- HA 실습은 quorum 기반 확장 시나리오 검증에 초점을 둡니다.
