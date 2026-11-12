# Terraform 인프라 자동화

실제 적용·검증 경로는 **OpenStack demo-lite**입니다.

- [실행 구성](envs/openstack-demo-lite/README.md): VM·네트워크 생성과 core demo-lite 자동 설치
- [검증 기록](envs/openstack-demo-lite/VALIDATION.md): 11개 리소스 생성·삭제·재구축, CPU 2 → 3 변경, 데이터 보존·서비스 복구·최종 plan 변경 없음
- resize용 플레이버를 추가한 현재 구성은 12개 리소스를 관리합니다.
- 기존 AWS root와 앱 배포 설정은 보존합니다. 아래 AWS 내용은 별도 확장 설계입니다.

## 별도 확장 설계: AWS Migration Blueprint

로컬 `kind + Kafka + PostgreSQL HA` 검증 구조를 AWS managed architecture로 옮길 때 사용할 `Terraform` blueprint입니다. EKS / MSK / RDS / ALB / Secrets Manager의 책임 경계를 정의합니다. 실제 AWS 배포는 현재 검증 범위에서 제외합니다.

## AWS toolchain 정적 검증 — 2026-07-14

- Terraform required version: `>= 1.15.8, < 1.16.0`
- direct providers: AWS `5.100.0`, Random `3.9.0`
- root modules: VPC `5.21.0`, EKS `20.37.2`, RDS `6.13.1`
- provider selection/checksum: `envs/dev/.terraform.lock.hcl`
- Terraform `1.15.8` 공식 SHA256 확인
- `fmt -recursive`, `init -backend=false`, `validate` 통과
- 실제 AWS endpoint 대상 `plan`, `apply`, AWS resource 생성 미실행. 아래 로컬 에뮬레이터 검증과 구분합니다.

저장소 test는 MSK module, Kafka bootstrap secret, 별도 cache queue resource 미포함을 확인합니다.

## 디렉터리 구조

외부 백업만 먼저 준비할 때는 독립 root인 [envs/backup](envs/backup/README.md)을 사용합니다.
S3 버킷과 접근·암호화·버전 설정을 관리하며 `envs/dev`의 전체 AWS 인프라를 생성하지 않습니다.

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

### 인증서·OIDC·PostgreSQL 입력

기본값은 PostgreSQL `16.14`, `acm_certificate_mode = "dns"`, `include_oidc_root_ca_thumbprint = true`입니다. PostgreSQL minor는 `db_engine_version`으로 선택하며 현재 parameter group은 `postgres16`이므로 메이저 버전은 16으로 제한합니다.

`include_oidc_root_ca_thumbprint = false`는 IRSA와 IAM OIDC provider를 유지하면서 Terraform의 issuer TLS 조회를 생략합니다. 실제 AWS에서는 IAM이 생성 시 인증서 지문을 조회하는 [공식 지원 방식](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc_verify-thumbprint.html)입니다. 에뮬레이터에서 provider 생성이 성공해도 실제 Pod의 토큰 검증·권한 부여가 검증된 것은 아닙니다.

외부 CA에서 이미 발급한 인증서는 `acm_certificate_mode = "imported"`로 [ACM import](https://docs.aws.amazon.com/acm/latest/userguide/import-certificate.html)를 사용할 수 있습니다. 이때 `acm_certificate_body_pem`과 일치하는 `acm_certificate_private_key`가 필수이며, `acm_certificate_chain_pem`으로 체인을 전달할 수 있습니다. DNS 발급 모드는 CNAME과 검증 완료를 계속 관리하고, import 모드는 이미 발급된 인증서를 등록하므로 해당 DNS 검증 리소스를 만들지 않습니다. 기존 DNS validation state 주소는 `moved` 블록으로 이전합니다.

PEM 입력은 커밋하지 않는 변수 파일이나 비밀 입력으로 전달합니다. 민감 입력은 로그에서 가리지만 Terraform state/plan에는 private key가 포함됩니다. imported 인증서는 자동 갱신되지 않으므로 발급 CA와 사용자가 만료·갱신을 관리해야 합니다.

로컬 에뮬레이터에서는 지원 버전 `16.4`, `db_port = null`로 서비스가 배정한 로컬 포트 사용, IAM 관리 지문 옵션, 테스트용 로컬 CA가 발급한 인증서 import를 별도 구성으로 사용할 수 있습니다. AWS 기본 포트와 SG ingress는 5432입니다. EKS 공개 endpoint가 꺼져 있을 때에는 public CIDR 입력을 생략해 서비스의 비활성 기본값과 불필요한 차이가 생기지 않도록 합니다. 공개 endpoint를 켜는 경우 기존 restricted CIDR 검증을 유지합니다. 이 구성의 성공 범위는 에뮬레이터의 IaC/API workflow이며, 공개 CA의 DNS 발급이나 실제 AWS IRSA 인증 성공과 구분합니다. 에뮬레이터 endpoint·계정·전용 credential·CA 설정은 반드시 별도로 격리합니다.

### AWS Emulator 로컬 검증 — 2026-11-12

[공유용 결과와 provenance](../../results/aws-emulator/20261112/summary.json)는 원시 로그 자체가 아닌 선별 요약입니다. 원본은 로컬 `results/aws-emulator/20261112-*/`에 보관하며 로그 hash로 구분합니다. state·plan에는 합성 비밀번호와 인증서 private key가 포함되므로 공유 파일에서 제외했습니다.

- Terraform 1.15.8 / AWS Provider 5.100.0, AWS Emulator upstream `7753983c6e495ec3612695b1336bf4936fc7b7fd` / 1.0.322 기반 로컬 수정 빌드입니다. 공식 배포 바이너리와 구분하며 필요한 서비스·전이 의존성 21개를 포함합니다.
- EC2 수정은 기본 ACL의 태그 접근자 등록과 모든 프로토콜 SG 규칙의 port 식별 정규화 두 가지입니다. [패치](../../results/aws-emulator/20261112/ec2-fixes.patch), [빌드 recipe](../../results/aws-emulator/20261112/Dockerfile), [소스 준비](../../results/aws-emulator/20261112/prepare-build.py)를 보존했습니다.
- 기본 DNS 구성 plan은 89 add입니다. 로컬 import 구성 plan은 85 add, apply 성공, 최초 재계획은 2개 차이였으며 서비스 기본값 처리를 반영한 최종 재계획은 No changes였습니다. destroy 85개와 남은 관리 리소스 0개를 확인했습니다. 인프라 계약 테스트는 27 passed였습니다.
- 최초 SG/ACL 실패와 PostgreSQL 16.14 거부, `.test` 공개 인증서 거부, 일반 도메인 형태에서도 정확한 CNAME과 INSYNC 이후 ACM pending, 비로컬 EKS OIDC 조회 실패는 이전 시도의 한계로 함께 기록합니다. 최신 PASS는 imported 인증서 구성의 결과이며 원래 89개 public DNS 발급 흐름의 전체 PASS가 아닙니다.
- 실제 AWS의 IRSA/JWT·권한, public DNS 인증서 발급·신뢰·갱신, SQL/Kafka 워크로드·성능·HA는 미검증입니다. Kubernetes·GitOps·기존 DB/PVC는 변경하지 않았습니다.

고정 소스를 준비한 빌드 예시입니다. `upstream.tar.gz`는 Git 추적 제외이며 Docker build 전에 SHA256이 `2d99a991920488c894622e86b3a143ec30743b0b05309a4465dfa8fa2a755be5`인지 확인합니다.

```powershell
curl.exe -L --fail -o results/aws-emulator/20261112/upstream.tar.gz https://codeload.github.com/aws/aws-emulator/tar.gz/7753983c6e495ec3612695b1336bf4936fc7b7fd
Get-FileHash results/aws-emulator/20261112/upstream.tar.gz -Algorithm SHA256
docker build -t cloud-portfolio-aws-emulator:1.0.322-local-ec2fix results/aws-emulator/20261112
```

기록된 이미지 digest·바이너리 hash는 당시 실행의 식별자입니다. recipe는 당시 base image digest를 고정하지만 apt 의존성 등까지 재빌드의 byte 단위 동일성을 보장하지 않습니다. 첫 빌드는 큰 디스크와 컴파일 시간이 필요합니다.

실행 입력은 별도 비추적 변수 파일에 아래처럼 선택합니다. 생성한 CA가 서명한 SAN 인증서·일치하는 개인키·체인을 위 PEM 입력 세 개로 함께 전달해야 합니다. 이번 로컬 CA 인증서는 공개 CA 신뢰가 없으며, ACM은 import한 인증서를 자동 갱신하지 않습니다.

```hcl
aws_region                       = "us-west-2"
environment                      = "emulator"
route53_zone_name                = "portfolio-emulator.test"
domain_name                      = "app.portfolio-emulator.test"
db_engine_version                = "16.4"
db_port                          = null
include_oidc_root_ca_thumbprint   = false
acm_certificate_mode             = "imported"
```

에뮬레이터는 내부 네트워크에서 실행하고 Terraform을 같은 네트워크에 연결합니다. 전용 credential·CA와 account `000000000000` 제한을 설정하고 AWS provider의 각 서비스 endpoint를 에뮬레이터로 지정합니다. `init -backend=false -lockfile=readonly` → `validate` → 별도 state의 plan/apply → `plan -detailed-exitcode` → destroy 순으로 확인합니다. 실제 AWS credential을 마운트하거나 기본 state를 재사용하지 않습니다.

주의:
- `terraform plan`과 `terraform apply`는 AWS credential이 필요합니다.
- `terraform apply`는 EKS, MSK, RDS 비용이 발생할 수 있습니다.
- 포트폴리오 기본 범위는 migration blueprint와 정적 검증이며, 실제 apply는 선택 작업입니다.

## Kafka 기준

이 포트폴리오는 Kafka event stream pipeline을 기준으로 하므로 Terraform에서도 별도 cache queue 리소스를 만들지 않습니다. AWS 쪽 event log는 `modules/msk_kafka`의 Amazon MSK cluster가 담당하고, 애플리케이션은 Secrets Manager의 `${name_prefix}/kafka/bootstrap` secret에서 bootstrap endpoint를 참조하는 흐름을 전제로 합니다.
