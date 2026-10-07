# Terraform 인프라 자동화

실제 적용·검증 경로는 **OpenStack demo-lite**입니다.

- [OpenStack 실행 구성](envs/openstack-demo-lite/README.md): VM·네트워크 생성과 core demo-lite 자동 설치
- [실환경 검증 기록](envs/openstack-demo-lite/VALIDATION.md): 11개 리소스 생성·삭제·재구축, CPU 2 → 3 변경, 데이터 유지, 서비스 복구, 최종 plan 변경 없음
- 사양 변경용 플레이버가 추가된 현재 Terraform 관리 리소스는 12개입니다.
- 작업 브랜치는 기존 demo-dev이며, 기존 AWS 구성은 별도 root로 보존합니다.
- master 공유 범위는 OpenStack 구성 폴더와 검증 설명이며, 기존 앱 배포 설정은 각 브랜치에서 유지합니다.

## 별도 확장 설계: AWS Migration Blueprint

로컬 `kind + Kafka + PostgreSQL HA` 검증 구조를 AWS managed architecture로 옮길 때 사용할 `Terraform` blueprint입니다. 현재 목적은 실제 운영 배포 완료가 아니라, EKS / MSK / RDS / ALB / Secrets Manager로 책임이 어떻게 이전되는지 보여주는 것입니다.

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

## 검증 상태

현재 저장소에서는 Terraform 코드의 구조와 Kafka 기준 정합성을 테스트로 검증합니다.

- `.venv\Scripts\python.exe -m pytest -q`
- Terraform 관련 테스트는 MSK module, Kafka bootstrap secret, 별도 cache queue 리소스 미포함을 확인합니다.
- Terraform required version: `>= 1.15.8, < 1.16.0`
- direct providers: AWS `5.100.0`, Random `3.9.0`
- root modules: VPC `5.21.0`, EKS `20.37.2`, RDS `6.13.1`
- provider selection/checksum: `envs/dev/.terraform.lock.hcl`

2026-07-14 기준 공식 SHA256을 확인한 Terraform `1.15.8`로 `fmt -recursive`, `init -backend=false`, `validate`를 실행해 통과했습니다. `terraform plan`과 `terraform apply`는 실행하지 않았고, 실제 AWS 리소스도 생성하지 않았습니다.

## Kafka 기준

이 포트폴리오는 Kafka event stream pipeline을 기준으로 하므로 Terraform에서도 별도 cache queue 리소스를 만들지 않습니다. AWS 쪽 event log는 `modules/msk_kafka`의 Amazon MSK cluster가 담당하고, 애플리케이션은 Secrets Manager의 `${name_prefix}/kafka/bootstrap` secret에서 bootstrap endpoint를 참조하는 흐름을 전제로 합니다.
