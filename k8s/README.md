# Kubernetes HA Design (Kafka)

이 폴더는 PostgreSQL HA와 Kafka event intake 경로를 로컬 Kubernetes에서 검증하기 위한 설정입니다.

## 현재 구성

- namespace: `messaging-app`
- Kafka: KRaft broker `3`, partition `8`, replication factor `3`, `min.insync.replicas=2`
- PostgreSQL: primary `1`, standby `2`, Pgpool `2`
- API: CPU HPA `6→8`
- Core Worker: Kafka lag KEDA `2→4`, threshold `100`
- Notification Worker: Kafka lag KEDA `1→2`, threshold `100`
- 관측: Prometheus, Grafana, kafka-exporter, kube-state-metrics
- 환경 한계: single-node kind, node·AZ failover 증거 제외

## 목표

- PostgreSQL: `primary 1 + replicas 2` 기반 자동 failover
- Kafka: event intake topic / DLQ topic 기반 비동기 처리
- App은 Kafka에 event를 append하고 Worker가 PostgreSQL에 비동기 영속화

## 구성

- PostgreSQL HA: `bitnami/postgresql-ha` chart + `bitnamilegacy/*` runtime images
  - total postgres nodes: 3
  - topology: primary 1 + replicas 2
  - pgpool enabled
  - 로컬 데모 readiness는 async streaming standby를 정상으로 해석
- Kafka runtime: `k8s/gitops/base/kafka-ha.yaml`
  - local 3-broker KRaft cluster
  - ingress topic: `message-ingress`
  - DLQ topic: `message-ingress-dlq`
  - Request status / event read: PostgreSQL source of truth
  - Worker autoscaling: KEDA Kafka lag scaler

## 실행
```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/setup-kind.ps1
powershell -ExecutionPolicy Bypass -File k8s/scripts/install-ha.ps1 -Namespace messaging-app
powershell -ExecutionPolicy Bypass -File k8s/scripts/install-kube-state-metrics.ps1 -Namespace messaging-app
powershell -ExecutionPolicy Bypass -File k8s/scripts/install-keda.ps1
kubectl apply -f k8s/gitops/base/kafka-ha.yaml
kubectl rollout status statefulset/kafka -n messaging-app --timeout=600s
kubectl wait --for=condition=complete job/kafka-topic-bootstrap -n messaging-app --timeout=300s
kubectl apply -f k8s/app/manifests-ha.yaml
```

## 연결 포인트
- PostgreSQL endpoint: `messaging-postgresql-ha-pgpool.messaging-app.svc.cluster.local:5432`
- Kafka bootstrap: `kafka.messaging-app.svc.cluster.local:9092`

## 관측 스택

Prometheus + Grafana 로 아래 항목을 관측합니다.

- API: request rate, latency p95/p99, stage latency, Kafka publish stage
- Kafka: bootstrap health, Worker consumer lag through KEDA
- PostgreSQL: primary reachability, standby count, sync standby count, replication state, replication lag
- Worker: event processed count, success/failure rate, processing latency, accepted-to-persisted lag
- Kubernetes: worker replica count, KEDA desired replicas, pod restart count, CPU/memory

## Container 기준

- application image: digest-pinned Python base, UID/GID `10001`, `SIGTERM`
- application pod: non-root, `RuntimeDefault` seccomp, privilege escalation 차단, capability 전체 제거, read-only root filesystem
- writable path: workload별 `/tmp` `emptyDir`, backup 전용 PVC
- batch workload: migration, k6, backup Job/CronJob에 resource requests/limits 적용
- image publication: tested SHA candidate digest의 non-root 실행 확인 뒤 release tag 승격

## 참고

- 기본 앱 실행은 `k8s/app/manifests-ha.yaml` 기준이며 PostgreSQL HA와 Kafka runtime을 전제로 합니다.
- 현재 Kafka manifest는 local dev용 3-broker KRaft runtime입니다. 운영형 HA는 Strimzi/MSK 같은 관리형 또는 operator 기반 runtime으로 확장하는 것을 전제로 합니다.
