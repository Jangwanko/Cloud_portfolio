# AWS IaC Plan

## Purpose

- 로컬 `kind + Kubernetes` 검증 구조를 AWS managed architecture로 옮기는 blueprint.
- 실제 AWS 배포 완료 보고서가 아니다.
- 포트폴리오에서 보여줄 지점:
  - async intake
  - failure recovery
  - autoscaling
  - observability
  - backup / restore

## Current Migration Blueprint

| Local validation | AWS managed architecture |
| --- | --- |
| `kind` Kubernetes | Amazon EKS |
| `ingress-nginx` | AWS Load Balancer Controller + ALB |
| local self-signed TLS | ACM + Route 53 |
| Kafka 3-broker KRaft | Amazon MSK |
| PostgreSQL HA + Pgpool | RDS PostgreSQL Multi-AZ / Aurora PostgreSQL |
| runtime Kubernetes secret | AWS Secrets Manager |
| local image build/load | Amazon ECR + EKS deploy |
| Prometheus / Grafana in cluster | EKS 유지 또는 AMP / AMG |

## Responsibilities Kept

- API:
  - Kafka append 후 `202 Accepted`
- Worker:
  - Kafka consumer group으로 event consume
  - PostgreSQL persistence
- Failure handling:
  - retry
  - DLQ
  - replay guard
- Worker autoscaling:
  - Kafka consumer lag
  - backlog drain time
- Operator signals:
  - readiness
  - DLQ summary
  - consumer lag
  - accepted-to-persisted lag

## Responsibilities Moved To AWS

- Kafka runtime:
  - local Kafka -> Amazon MSK
- PostgreSQL runtime:
  - PostgreSQL HA + Pgpool -> RDS / Aurora
- TLS:
  - self-signed -> ACM
- Secret:
  - Kubernetes secret 직접 주입 -> Secrets Manager 연동
- Backup:
  - local CronJob -> RDS automated backup / snapshot / PITR

## First Target

- 목표:
  - `terraform validate` 가능한 dev 환경 골격
- 실제 `terraform apply`:
  - 선택 작업
  - MSK / RDS / EKS 비용 발생

## Compute

- `Amazon EKS`
  - API
  - Worker
  - DLQ Replayer
  - notification-worker
- `Managed Node Group`
  - 첫 단계는 단일 node group
  - 이후 API / Worker node 분리 가능

## Networking

- `VPC`
  - public subnet: ALB
  - private subnet: EKS node, RDS, MSK
- `AWS Load Balancer Controller`
  - Kubernetes `Ingress` -> ALB
- `Application Load Balancer`
  - `/`
  - `/grafana`
  - `/prometheus`
- `Route 53`
  - domain 연결
- `ACM`
  - TLS certificate
  - ALB HTTPS termination

## Container Registry

- `Amazon ECR`
  - API image
  - Worker image
  - DLQ Replayer image
- image strategy:
  - same image with different command
  - separate images if runtime boundary grows

## Data

- `Amazon RDS for PostgreSQL`
  - first option for portfolio explanation
  - Multi-AZ
  - automated backup
- `Aurora PostgreSQL`
  - higher scale option
  - read/write separation 가능

## Event Log

- `Amazon MSK`
  - ingress topic
  - DLQ topic
  - status / snapshot compacted topics
- production settings:
  - Multi-AZ broker
  - replication
  - `min.insync.replicas`
  - producer `acks`

## Secret

- `AWS Secrets Manager`
  - DB credential
  - Kafka credential
  - JWT secret
  - Grafana admin credential
- Kubernetes injection:
  - External Secrets Operator
  - CSI driver

## Observability

- Option 1:
  - Prometheus / Grafana inside EKS
  - 현재 구조 설명에 적합
- Option 2:
  - Amazon Managed Service for Prometheus
  - Amazon Managed Grafana
  - 운영 부담 감소

## Backup / Recovery

- RDS automated backups
- manual snapshot
- point-in-time recovery
- Kafka durability:
  - replication factor
  - `min.insync.replicas`
  - producer `acks`

## Terraform Structure

```text
infra/terraform/
  envs/
    dev/
  modules/
    network/
    eks/
    msk/
    rds/
    secrets/
    observability/
```

## Portfolio Talking Points

- 로컬에서 검증한 책임을 AWS managed service로 치환했다.
- Kubernetes 중심 application 구조는 유지했다.
- Kafka / PostgreSQL 운영 부담은 MSK / RDS로 넘겼다.
- Worker scaling 기준은 CPU보다 consumer lag에 둔다.
- Terraform은 배포 완료 증거가 아니라 migration blueprint 증거다.
