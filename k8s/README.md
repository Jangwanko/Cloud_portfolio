# Kubernetes HA Design (Local Practice)

이 폴더는 "DB 자동 페일오버 + 쿼럼"을 로컬에서 실습하기 위한 설정입니다.

## 목표
- PostgreSQL: 다중 노드 + 자동 failover
- Redis: Sentinel quorum 기반 자동 failover
- App은 PostgreSQL/Redis를 동시에 사용

## 구성
- PostgreSQL HA: `bitnami/postgresql-ha`
  - postgres replicas: 3
  - pgpool enabled
  - `synchronousCommit` + `numSynchronousReplicas: 1`
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
powershell -ExecutionPolicy Bypass -File k8s/scripts/install-ha.ps1
```

## 3) 앱 연결 포인트
- PostgreSQL endpoint: `messaging-postgresql-ha-pgpool.messaging.svc.cluster.local:5432`
- Redis endpoint (sentinel):
  - `messaging-redis-node-0.messaging-redis-headless.messaging.svc.cluster.local:26379`
  - `messaging-redis-node-1.messaging-redis-headless.messaging.svc.cluster.local:26379`
  - `messaging-redis-node-2.messaging-redis-headless.messaging.svc.cluster.local:26379`

## 4) 페일오버 테스트
- PostgreSQL primary pod 강제 삭제 -> pgpool/repmgr가 새로운 primary 승격
- Redis master pod 강제 삭제 -> sentinel quorum으로 replica 승격

## 참고
- 이 리포의 로컬 Docker Compose는 단일 DB/Redis입니다.
- 면접 시에는 "로컬 단일 -> K8s HA 확장" 전략으로 설명하면 좋습니다.
