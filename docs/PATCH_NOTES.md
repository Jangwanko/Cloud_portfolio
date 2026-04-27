# Patch Notes

Kafka Event Stream Systems 포트폴리오의 주요 릴리즈 노트입니다.

## Kafka-Native Event Intake

범위:

- FastAPI 기반 event request API
- Kafka ingress topic 기반 request intake
- `stream_id` key 기반 partition ordering boundary
- Worker consumer group 기반 비동기 persistence
- PostgreSQL 기반 event 영속화

결과:

- API는 event request를 Kafka에 append한 뒤 빠르게 수락 응답을 반환합니다.
- Worker는 Kafka partition을 소비해 PostgreSQL에 최종 event를 저장합니다.
- 같은 stream의 event는 같은 Kafka key를 사용해 partition 내부 순서를 유지합니다.

## Kafka HA Runtime

범위:

- 3-broker KRaft Kafka StatefulSet
- topic replication factor `3`
- `min.insync.replicas=2`
- ingress / DLQ topic partition 수 `8`
- topic bootstrap Job

결과:

- 로컬 Kubernetes 환경에서도 replicated Kafka runtime을 실행합니다.
- Kafka broker replication, ISR, partition 구성을 포트폴리오에서 직접 확인할 수 있습니다.

## Failure Recovery And DLQ

범위:

- Worker retry policy
- Kafka DLQ topic
- DLQ listing API
- DLQ Replayer

결과:

- persistence 실패 event는 retry 후 Kafka DLQ topic에 보존됩니다.
- 운영자는 DLQ API로 실패 event를 확인할 수 있습니다.
- replay 조건이 맞으면 DLQ Replayer가 ingress topic으로 event를 재주입합니다.

## Readiness And Reliability Policy

범위:

- Kafka intake path readiness
- PostgreSQL persistence path 상태 분리
- `ready`, `degraded`, `not_ready` 상태 모델
- PostgreSQL outage recovery scenario

결과:

- Kafka append 가능 여부를 request intake의 핵심 readiness 기준으로 봅니다.
- PostgreSQL primary outage는 persistence 장애로 해석하되, Kafka append path가 살아 있으면 degraded 상태로 분리합니다.
- 이미 확인된 stream membership 경로에서는 DB outage 중에도 Kafka append를 유지할 수 있습니다.

## Observability

범위:

- API request latency
- API stage latency
- Worker stage latency
- accepted-to-persisted async lag
- Queue wait / backlog interpretation
- Prometheus / Grafana dashboard
- Alert rules

결과:

- API 응답 latency와 PostgreSQL persisted 완료 latency를 분리해서 볼 수 있습니다.
- Kafka publish, Worker consume, DB persistence 병목을 단계별로 해석할 수 있습니다.
- Worker autoscaling은 Kafka consumer lag 관점으로 설명합니다.

## Autoscaling

범위:

- API CPU HPA
- Worker KEDA Kafka lag scaler
- kube-state-metrics 기반 replica 관측

결과:

- API는 CPU metric 기반으로 scale-out합니다.
- Worker는 Kafka consumer lag 기준으로 scale-out합니다.
- 최신 성능 검증에서 API HPA와 Worker KEDA가 모두 8 replicas까지 증가했습니다.

## Performance Baseline

범위:

- k6 Kafka intake load
- Kafka async persistence latency sample
- HPA / KEDA sanity check
- `results/kafka-performance/latest.txt` 결과 산출

최신 기준:

- profile: `single500`
- concurrent users: `100`
- duration: `30s`
- idempotency header: disabled
- total HTTP requests: `30922`
- event status 200: `30916`
- event status 503: `2`
- error rate: `0.01%`
- average latency: `46.50ms`
- p95 latency: `92.25ms`

해석:

- 이 수치는 Kafka append 중심 intake baseline입니다.
- idempotency header를 켠 write path는 별도 state-path profile로 측정합니다.
- state-path profile에서는 PostgreSQL state store가 API hot path에 들어오므로 별도 설계와 튜닝 대상입니다.

## GitOps And CI

범위:

- Kustomize base / local overlay
- Argo CD AppProject / Application example
- GitOps quick start script
- GitHub Actions compile / image build / manifest render check

결과:

- 직접 배포 경로와 GitOps sync 경로를 모두 제공합니다.
- 로컬 Kubernetes 환경에서 Argo CD sync 흐름을 검증할 수 있습니다.

## Operations

범위:

- Runtime Secret 분리
- local TLS ingress
- PostgreSQL logical backup / restore
- weekly backup CronJob
- Prometheus / Grafana 운영 경로

결과:

- 로컬 환경에서 운영 시나리오를 재현할 수 있습니다.
- backup, restore, readiness, alert, DLQ replay를 문서화된 명령으로 검증할 수 있습니다.

## State Path Design Notes

현재 성능 기준선은 Kafka append 중심 intake path를 보여줍니다. Idempotency, request status, authorization cache 같은 low-latency state path는 Kafka event log와 책임을 분리해 다룹니다.

운영형 설계에서는 아래 항목을 별도 profile로 관리합니다.

- idempotency-enabled write load
- request status lookup volume
- DB outage 중 신규 authorization 정책
- Kafka consumer lag / DLQ depth dashboard
