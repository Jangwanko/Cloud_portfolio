# Kafka Event Stream Systems Portfolio

DB 중심 동기 처리 구조에서 발생하는 장애 전파와 write path 병목을 줄이기 위해, Kafka 기반 event log를 request intake 경로에 두고 persistence를 Worker consumer group으로 분리한 이벤트 처리 파이프라인입니다.

API는 event request를 PostgreSQL에 직접 쓰지 않고 Kafka ingress topic에 append한 뒤 `202 Accepted`를 반환합니다. Worker는 Kafka consumer group으로 partition을 소비해 PostgreSQL HA에 비동기 영속화하고, 실패 이벤트는 retry 후 Kafka DLQ topic과 DLQ Replayer를 통해 복구합니다.

## Architecture
```mermaid
flowchart LR
    Client[Client] --> Ingress[Ingress nginx]
    Ingress --> API[FastAPI API]
    API -->|202 Accepted| Client
    API --> Kafka[Kafka ingress topic]
    Kafka --> Worker[Worker consumer group]
    Worker --> Pgpool[Pgpool]
    Pgpool --> DB[(PostgreSQL HA)]

    Worker --> DLQ[Kafka DLQ topic]
    DLQ --> Replayer[DLQ Replayer]
    Replayer --> Kafka

    API --> Metrics[Metrics]
    Worker --> Metrics
    Metrics --> Prometheus[Prometheus]
    Prometheus --> Grafana[Grafana]
    Prometheus --> KEDA[KEDA Kafka scaler]
    KEDA --> Worker

    GitHub[GitHub Actions] --> Argo[Argo CD GitOps path]
    Argo --> K8s[Kubernetes sync]
```

처리 흐름:
- API는 event request를 Kafka ingress topic에 append하고 `202 Accepted`를 반환합니다.
- Kafka message key는 `stream_id`로 두어 같은 stream 이벤트의 ordering boundary를 partition 단위로 유지합니다.
- Worker consumer group은 Kafka partition을 나눠 소비하고 PostgreSQL HA에 영속화합니다.
- 실패한 job은 retry 후 Kafka DLQ topic으로 이동하고, DLQ Replayer가 복구 조건에서 ingress topic으로 재주입합니다.
- Prometheus는 API / Worker metrics를 수집하고, Grafana는 latency, consumer lag, replica 변화를 보여줍니다.
- Worker는 CPU가 아니라 KEDA Kafka scaler의 consumer lag 기준으로 scale-out합니다.

Design choice: 이 시스템은 최소 latency보다 요청 수락 안정성과 복구 가능성을 우선합니다. Kafka event log와 Worker persistence를 거치며 일부 latency를 감수하지만, DB 장애 전파를 줄이고 partition ordering, consumer group scale-out, DLQ replay 기반 복구 경로를 확보합니다.

## Key Features
- Kafka-backed async event intake
- Partition key 기반 stream ordering boundary
- Worker consumer group processing
- Kafka DLQ topic / DLQ Replayer
- Kafka consumer lag based KEDA autoscaling
- PostgreSQL HA + Pgpool
- API CPU HPA
- Prometheus / Grafana observability
- PostgreSQL backup / restore
- Ingress nginx + local self-signed TLS
- Argo CD GitOps sync path
- AWS Terraform IaC extension path

## Verified Scenarios
로컬 `kind` 환경에서 아래 시나리오를 검증했습니다. 최신 수치는 [TEST_RESULTS.md](docs/TEST_RESULTS.md)에 기록합니다.

- Kafka mode smoke test
- Kafka ingress topic append / Worker consume / PostgreSQL persisted
- Kafka DLQ topic listing through `GET /v1/dlq/ingress`
- KEDA Kafka scaler readiness and external metric lookup
- API HPA scaling
- PostgreSQL backup / restore
- Argo CD GitOps sync

상세 결과와 Kafka-native 설계 trade-off는 [TEST_RESULTS.md](docs/TEST_RESULTS.md)와 [KAFKA_EXPERIMENT.md](docs/KAFKA_EXPERIMENT.md)에 정리했습니다.

## Performance Summary
Kafka 포트폴리오의 성능 기준은 기능 테스트와 분리해서 봅니다. 기능 검증은 `quick_start_all.ps1`에서 확인하고, 성능 기준선은 아래 suite로 측정합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_kafka_performance_suite.ps1
```

이 suite는 Kafka-native 구조를 기준으로 아래 값을 함께 확인합니다.

| 기준 | 결과 | 해석 |
| --- | --- | --- |
| Kafka smoke | Pass | event accepted -> Kafka ingress topic -> Worker -> PostgreSQL persisted |
| Kafka DLQ listing | Pass | `GET /v1/dlq/ingress`로 DLQ topic 최근 메시지 조회 |
| Kafka async persistence latency | Pass | 50 events, accept avg `56.28ms`, accept p95 `68.19ms` |
| Kafka intake load | Pass | 100 VU / 30s, `30922` requests, `0.01%` error, avg `46.50ms`, p95 `92.25ms` |
| HPA and metrics sanity | Pass | API HPA and Worker KEDA reached 8 replicas during load |

Latency는 k6 `http_req_duration` 기준으로, event request가 intake path에서 수락되고 API 응답을 받을 때까지의 시간입니다. PostgreSQL persisted 완료까지의 lag는 `messaging_event_persist_lag_seconds`로 별도 관측합니다.
현재 Kafka intake load baseline은 `X-Idempotency-Key`를 보내지 않는 Kafka append 중심 경로입니다. Idempotency header를 켜면 PostgreSQL state path가 다시 hot path가 되어 별도 병목으로 봅니다.

Kafka 실험의 핵심 결과:
- Kafka ingress / DLQ transport는 동작했습니다.
- DLQ topic listing과 replay 흐름도 확인했습니다.
- API intake path는 Kafka append 중심으로 동작합니다.
- Worker가 persistence 시점에 sequence를 배정하고 request status를 갱신합니다.

성능 suite 결과는 실행 후 `results/kafka-performance/latest.txt`에 남습니다. Kafka 설계 검증 내용은 [TEST_RESULTS.md](docs/TEST_RESULTS.md)와 [KAFKA_EXPERIMENT.md](docs/KAFKA_EXPERIMENT.md)에 정리했습니다.

## Quick Start
Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1
```

Linux:

```bash
bash scripts/quick_start_all.sh
```

Kafka runtime:

```powershell
kubectl apply -f k8s/gitops/base/kafka-ha.yaml
kubectl rollout status statefulset/kafka -n messaging-app --timeout=600s
kubectl wait --for=condition=complete job/kafka-topic-bootstrap -n messaging-app --timeout=300s
kubectl -n messaging-app set env deployment/api KAFKA_BOOTSTRAP_SERVERS=kafka.messaging-app.svc.cluster.local:9092
kubectl -n messaging-app set env deployment/worker KAFKA_BOOTSTRAP_SERVERS=kafka.messaging-app.svc.cluster.local:9092
kubectl -n messaging-app set env deployment/dlq-replayer KAFKA_BOOTSTRAP_SERVERS=kafka.messaging-app.svc.cluster.local:9092
```

기본 접근 경로:
- API: `http://localhost`
- Grafana: `http://localhost/grafana`
- Prometheus: `http://localhost/prometheus/`

Grafana 기본 계정:
- ID: `admin`
- Password: `1q2w3e4r`

자세한 실행 방법은 [QUICK_START.md](docs/QUICK_START.md)를 참고합니다.

## GitOps / CI
이 저장소는 직접 배포 경로와 Argo CD 기반 GitOps 경로를 함께 포함합니다.

- GitOps sync path: `k8s/gitops/overlays/local-ha`
- Argo CD bootstrap scripts:
  - `k8s/scripts/install-argocd.ps1`
  - `k8s/scripts/bootstrap-argocd-app.ps1`
- GitHub Actions CI:
  - Python compile check
  - Docker image build check
  - Kustomize manifest render check

자세한 내용은 [GITOPS.md](docs/GITOPS.md)에 정리했습니다.

## AWS IaC Path
현재 로컬 검증 구조를 AWS로 확장하기 위한 Terraform 골격도 포함되어 있습니다.

포함된 AWS 구성:
- VPC
- EKS
- ECR
- RDS PostgreSQL
- managed messaging path 검토
- Secrets Manager
- optional Route 53 + ACM

현재 AWS IaC는 실제 리소스 운영 배포가 아니라 `terraform plan` 검증 단계입니다. 설계 의도와 구성은 [AWS_IAC_PLAN.md](docs/AWS_IAC_PLAN.md)와 [infra/terraform/README.md](infra/terraform/README.md)에 정리했습니다.

## Operating Notes
- Kafka broker는 로컬 기준 3-broker KRaft StatefulSet으로 실행합니다.
- 최신 Kafka intake baseline은 100 VU / 30초 기준 `30922` requests, error `0.01%`, p95 `92.25ms`입니다.
- HTTPS는 production certificate가 아니라 local self-signed TLS 검증용입니다.
- Grafana / Prometheus는 로컬 포트폴리오 확인을 위해 ingress로 노출합니다.
- AWS IaC 문서는 운영형 확장 설계를 설명합니다.

## Documentation
- [QUICK_START.md](docs/QUICK_START.md): 실행 가이드
- [ARCHITECTURE.md](docs/ARCHITECTURE.md): 구조와 처리 흐름
- [KAFKA_EXPERIMENT.md](docs/KAFKA_EXPERIMENT.md): Kafka 설계와 검증 기록
- [OPERATIONS.md](docs/OPERATIONS.md): 운영 지침
- [OBSERVABILITY.md](docs/OBSERVABILITY.md): 지표, 대시보드, 병목 해석
- [RELIABILITY_POLICY.md](docs/RELIABILITY_POLICY.md): readiness / degraded / not_ready 정책
- [TEST_RESULTS.md](docs/TEST_RESULTS.md): 검증 결과
- [GITOPS.md](docs/GITOPS.md): Argo CD GitOps
- [AWS_IAC_PLAN.md](docs/AWS_IAC_PLAN.md): AWS 확장 설계
- [PATCH_NOTES.md](docs/PATCH_NOTES.md): 변경 이력
- [REPOSITORY_STRUCTURE.md](docs/REPOSITORY_STRUCTURE.md): 저장소 구조
