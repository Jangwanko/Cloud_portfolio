# AWS IaC Migration Blueprint

## Current Position

`infra/terraform`은 로컬 `kind + Kafka + PostgreSQL HA` 구조를 AWS managed services로 옮기기 위한 dev skeleton입니다.

- AWS deployment 완료 증거: 없음
- `terraform apply` 실행 증거: 없음
- local CLI: 공식 SHA256을 검증한 Terraform `1.15.8`
- local validation: `terraform fmt -recursive`, `terraform init -backend=false`, `terraform validate` 통과
- `terraform plan` 실행 증거: 없음
- repository test: Terraform source의 구조적 계약 확인
- CI source: exact Terraform `1.15.8`, `fmt -check`, `init -backend=false`, `validate` gate 포함; 실제 remote workflow 결과는 별도 확인

Version reproducibility:

- Terraform required version: `>= 1.15.8, < 1.16.0`
- direct provider pins: AWS `5.100.0`, Random `3.9.0`
- root module pins: VPC `5.21.0`, EKS `20.37.2`, RDS `6.13.1`
- provider selections and checksums: `envs/dev/.terraform.lock.hcl`

This is a migration blueprint. It documents service mapping, ownership boundaries, and hardening work required before an AWS deployment.

## Implemented Terraform Scope

| Module | Current resource scope | Important boundary |
| --- | --- | --- |
| `vpc` | VPC, public/private/database subnets, NAT | production network policy 검토 필요 |
| `ecr` | immutable tag, scan-on-push application repository | image build/push workflow는 별도 |
| `eks` | EKS cluster, managed node group, IRSA, private API endpoint default | Kubernetes add-ons/workloads 미설치 |
| `msk_kafka` | MSK provisioned cluster, security group | dev auth/encryption default hardening 필요 |
| `rds_postgres` | RDS PostgreSQL Multi-AZ, encrypted storage, backup retention | deletion/final snapshot policy hardening 필요 |
| `secrets` | DB, Kafka bootstrap, JWT, Grafana secret values | pod injection controller/config 미구현; Terraform state에 secret material 존재 |
| `route53_acm` | optional hosted-zone lookup, ACM certificate, validation records | ALB alias/application DNS record 미구현 |

현재 Terraform이 만들지 않는 영역:

- AWS Load Balancer Controller
- Kubernetes API / Worker / notification-worker / DLQ replayer workloads
- KEDA, metrics-server, Prometheus, Grafana
- External Secrets Operator 또는 Secrets Store CSI Driver
- MSK topic bootstrap과 application ACL
- ALB listener / ingress와 Route 53 ALB alias
- AMP / AMG resources
- image tag update와 Argo CD application

## Local-to-AWS Mapping

| Local validation | AWS target | Status in skeleton |
| --- | --- | --- |
| kind | Amazon EKS | cluster + node group |
| local image build/load | Amazon ECR | repository only |
| Kafka 3-broker KRaft | Amazon MSK | provisioned cluster |
| PostgreSQL HA + Pgpool | RDS PostgreSQL Multi-AZ | RDS instance |
| Kubernetes Secret | AWS Secrets Manager | secret resources |
| ingress-nginx | AWS Load Balancer Controller + ALB | design only |
| self-signed TLS | ACM + Route 53 | certificate validation only |
| Prometheus / Grafana | in-cluster or AMP / AMG | design only |
| backup CronJob | RDS backup / snapshot / PITR | RDS retention setting only |

## Application Responsibilities Preserved

- API: Kafka append 뒤 `202 Accepted`
- Worker: consumer group 기반 PostgreSQL persistence
- Ordering: domain-neutral `stream_id` key의 Kafka partition boundary; order reference adapter는 `order_id`를 stream key로 매핑
- Failure: inline retry, DLQ, replay guard
- Read model: PostgreSQL request status와 event row
- Notification: 별도 topic/consumer boundary
- Scaling signal: Worker consumer lag, persistence latency, backlog drain

PostgreSQL commit 뒤 notification publish의 신뢰성 gap도 AWS 이전만으로 해결되지 않습니다. transactional outbox 또는 동등한 recovery mechanism이 별도로 필요합니다.

## Current Dev Security Defaults

현재 source를 production-ready로 해석하면 안 되는 설정:

| Area | Current dev skeleton | Production direction |
| --- | --- | --- |
| EKS endpoint | private access enabled, public disabled by default; public은 제한 CIDR이 있을 때만 활성 | 운영 IAM / access entry와 네트워크 진입 경로 설계 |
| EKS creator access | dev blueprint bootstrap admin enabled | least-privilege access entry와 운영 role 분리 |
| MSK client auth | unauthenticated | IAM 또는 SASL/SCRAM, ACL 검토 |
| MSK client-broker transport | `TLS_PLAINTEXT` | TLS-only |
| RDS deletion protection | disabled | enabled |
| RDS final snapshot | skipped | required snapshot policy |
| Secret delivery | RDS random password와 Secrets Manager value 일치, Terraform state에도 material 존재 | IRSA + ESO/CSI, rotation, least privilege, state backend 보호 |
| Grafana credential input | Terraform variable | secret generation/rotation과 state 노출 검토 |

production hardening 완료 전 `apply` 대상은 비용이 발생하는 실험용 dev account로 제한합니다.

## Network and Ingress Target

목표 topology:

- public subnet: ALB
- private subnet: EKS managed nodes, MSK brokers
- database subnet: RDS PostgreSQL
- ACM: HTTPS certificate
- Route 53: application hostname과 ALB alias

현재 module은 VPC/subnet과 optional ACM validation까지 제공합니다. Load Balancer Controller 설치, Ingress annotation, ALB provisioning, DNS alias는 후속 단계입니다.

## Data and Event Services

### RDS PostgreSQL

현재 skeleton:

- PostgreSQL `16.14`
- Multi-AZ
- encrypted storage
- configurable backup retention
- EKS node security group에서 `5432` 접근
- generated master password와 Secrets Manager value 일치

후속 검증:

- parameter group과 connection limit
- failover와 application retry
- PITR / restore drill
- deletion protection / final snapshot
- credential rotation

### Amazon MSK

현재 skeleton:

- Amazon MSK `3.9.x` default, configurable broker count/type/storage
- private subnet 배치
- per-topic/per-broker enhanced monitoring
- in-cluster encryption
- client auth `unauthenticated`, client-broker transport `TLS_PLAINTEXT`

EKS module의 Kubernetes version default는 `1.36`입니다. Version allowlist와 AWS support 상태는 시간이 지나면 바뀌므로 배포 전 AWS release calendar를 다시 확인합니다.

후속 검증:

- TLS-only client connection
- IAM 또는 SCRAM authentication
- topic replication, `min.insync.replicas`, retention, compaction
- broker failure / rolling maintenance
- KEDA authentication과 bootstrap secret delivery

## Terraform Structure

```text
infra/terraform/
  envs/
    dev/
  modules/
    ecr/
    eks/
    msk_kafka/
    rds_postgres/
    route53_acm/
    secrets/
    vpc/
```

## Validation Path

2026-07-14 local static validation 완료:

- Terraform binary `1.15.8` official SHA256 확인
- `terraform fmt -recursive` 완료
- `terraform init -backend=false` 완료
- `terraform validate` 성공
- `.terraform.lock.hcl` 생성

AWS credential 없이 가능한 정적 검증:

```powershell
cd infra/terraform/envs/dev
terraform fmt -check -recursive ../..
terraform init -backend=false
terraform validate
```

Provider/module download에는 network access가 필요합니다. 현재 lock file은 성공한 init의 provider checksum과 `windows_amd64` / `linux_amd64` package hash를 함께 보관합니다.

AWS credential과 비용 검토 뒤 선택 실행:

```powershell
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

`plan`부터 account, region, estimated cost, destructive change, public exposure를 review합니다.

## Completion Criteria

### Blueprint validation

- `fmt`, `init -backend=false`, `validate` 통과 — 완료
- module output / secret reference contract test 통과
- example variable로 review 가능한 plan 생성 — 미실행

### Deployable dev environment

- ECR image push
- EKS workload / controller bootstrap
- MSK/RDS private connectivity 확인
- `202 Accepted` → Kafka → Worker → RDS end-to-end 검증
- KEDA consumer lag scale-out과 drain 관찰
- ALB TLS endpoint와 DNS 연결

### Production hardening

- MSK auth와 TLS-only
- EKS API access 제한
- RDS deletion/final snapshot 보호
- secret rotation과 least-privilege IAM
- restore / failover / broker disruption drill
- observability retention과 alert routing

전체 포트폴리오 우선순위는 [IMPROVEMENT_ROADMAP.md](IMPROVEMENT_ROADMAP.md)에 있습니다.
