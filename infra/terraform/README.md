# Terraform 구성

AWS에 배포할 때 사용할 `Terraform` 구조입니다.

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

## 1차 목표 구성
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
terraform init
terraform plan -var-file=terraform.tfvars
```

`terraform.tfvars.example`를 복사해 `terraform.tfvars`로 사용하면 됩니다.

## 검증 상태
현재 저장소에서는 Terraform 코드의 구조와 Kafka 기준 정합성을 테스트로 검증합니다.

- `.venv\Scripts\python.exe -m pytest -q`
- Terraform 관련 테스트는 MSK module, Kafka bootstrap secret, 별도 cache queue 리소스 미포함을 확인합니다.

현재 로컬 환경에는 Terraform CLI가 설치되어 있지 않아 `terraform fmt`, `terraform validate`, `terraform plan`은 아직 실행하지 않았습니다. 실제 AWS 리소스 생성도 비용 절감을 위해 기본 범위에 포함하지 않습니다.

## Kafka 기준
이 포트폴리오는 Kafka event stream pipeline을 기준으로 하므로 Terraform에서도 별도 cache queue 리소스를 만들지 않습니다. AWS 쪽 event log는 `modules/msk_kafka`의 Amazon MSK cluster가 담당하고, 애플리케이션은 Secrets Manager의 `${name_prefix}/kafka/bootstrap` secret에서 bootstrap endpoint를 참조하는 흐름을 전제로 합니다.
