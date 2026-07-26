# Terraform AWS Migration Blueprint

로컬 `kind + Kafka + PostgreSQL HA` 검증 구조를 AWS managed architecture로 옮길 때 사용할 `Terraform` blueprint입니다. EKS / MSK / RDS / ALB / Secrets Manager의 책임 경계를 정의합니다. 실제 AWS 배포는 현재 검증 범위에서 제외합니다.

## 현재 검증 상태 — 2026-07-14

- Terraform required version: `>= 1.15.8, < 1.16.0`
- direct providers: AWS `5.100.0`, Random `3.9.0`
- root modules: VPC `5.21.0`, EKS `20.37.2`, RDS `6.13.1`
- provider selection/checksum: `envs/dev/.terraform.lock.hcl`
- Terraform `1.15.8` 공식 SHA256 확인
- `fmt -recursive`, `init -backend=false`, `validate` 통과
- `plan`, `apply`, AWS resource 생성 미실행

저장소 test는 MSK module, Kafka bootstrap secret, 별도 cache queue resource 미포함을 확인합니다.

## 디렉터리 구조

```text
infra/terraform
├─ envs/
│  └─ dev/                  # 개발/포트폴리오용 환경 진입점
└─ modules/
   ├─ ecr/                  # ECR 저장소
   ├─ eks/                  # EKS cluster + node group
   ├─ msk_kafka/            # Amazon MSK Kafka cluster
   ├─ rds_postgres/         # RDS PostgreSQL
   ├─ route53_acm/          # Route53 + ACM
   ├─ secrets/              # Secrets Manager
   └─ vpc/                  # VPC, subnet, NAT
```

## 목표 구성

이 Terraform 코드는 아래 조합을 기준으로 합니다.

- VPC
- EKS
- ECR
- RDS PostgreSQL Multi-AZ
- Amazon MSK Kafka
- Secrets Manager
- optional Route 53 + ACM

## 실행 방법

```powershell
cd infra/terraform/envs/dev
terraform fmt -check -recursive ../..
terraform init -backend=false
terraform validate
terraform plan -var-file=terraform.tfvars
```

`terraform.tfvars.example`를 복사해 `terraform.tfvars`로 사용하면 됩니다.

주의:
- `terraform plan`과 `terraform apply`는 AWS credential이 필요합니다.
- `terraform apply`는 EKS, MSK, RDS 비용이 발생할 수 있습니다.
- 포트폴리오 기본 범위는 migration blueprint와 정적 검증이며, 실제 apply는 선택 작업입니다.

## Kafka 기준

이 포트폴리오는 Kafka event stream pipeline을 기준으로 하므로 Terraform에서도 별도 cache queue 리소스를 만들지 않습니다. AWS 쪽 event log는 `modules/msk_kafka`의 Amazon MSK cluster가 담당하고, 애플리케이션은 Secrets Manager의 `${name_prefix}/kafka/bootstrap` secret에서 bootstrap endpoint를 참조하는 흐름을 전제로 합니다.
