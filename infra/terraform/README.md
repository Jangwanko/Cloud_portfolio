# Terraform Layout

AWS에 배포할 때 사용할 `Terraform` 구조입니다.

## Structure
```text
infra/terraform
├─ envs/
│  └─ dev/                  # 개발/포트폴리오용 환경 진입점
└─ modules/
   ├─ ecr/                  # ECR repository
   ├─ eks/                  # EKS cluster + node group
   ├─ rds_postgres/         # RDS PostgreSQL
   ├─ route53_acm/          # Route53 + ACM
   ├─ secrets/              # Secrets Manager
   └─ vpc/                  # VPC, subnet, NAT
```

## First Target
이 Terraform 코드는 아래 조합을 기준으로 합니다.
- VPC
- EKS
- ECR
- RDS PostgreSQL Multi-AZ
- Amazon MSK or self-managed Kafka on EKS
- Secrets Manager
- optional Route 53 + ACM

## How To Start
```powershell
cd infra/terraform/envs/dev
terraform init
terraform plan -var-file=terraform.tfvars
```

`terraform.tfvars.example`를 복사해 `terraform.tfvars`로 사용하면 됩니다.

## 실행 결과
로컬 테스트 목적으로 `terraform plan` 검증 완료.
실제 AWS 리소스 생성은 비용 절감을 위해 plan 단계까지만 포함.
