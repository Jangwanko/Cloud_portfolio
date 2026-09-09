# 백업 전용 S3 구성

계정이 아직 없는 상태에서 준비한 독립 Terraform root입니다. `envs/dev`의 EKS/MSK/RDS/VPC를
참조하지 않으며 S3 bucket과 관련 설정 6개만 관리합니다. 실제 AWS `plan`/`apply`는 미실행입니다.

2026-09-05 검증: 공식 SHA256을 확인한 Terraform `1.15.8`, AWS provider `5.100.0`으로
`fmt -check`, `init -backend=false`, `validate` 통과. Provider lockfile을 보존했습니다.
전체 Python suite는 `654 passed`이며 실제 AWS 동작 검증을 대신하지 않습니다.

비공개 접근, ACL 비활성화, SSE-S3 AES256 암호화, versioning, HTTPS 요구를 설정합니다.
버킷 삭제는 `prevent_destroy`, 데이터 강제 삭제는 `force_destroy=false`로 막습니다.
자동 만료는 설정하지 않았으므로 저장한 백업과 이전 버전은 계속 보관됩니다.
S3 저장·요청 비용이 발생할 수 있으며 무료 사용을 전제하지 않습니다.

## 계정이 준비된 뒤

1. AWS 계정과 로컬 인증 profile을 준비합니다. 비밀키를 저장소나 채팅에 넣지 않습니다.
2. 이 폴더에서 example을 `terraform.tfvars`로 복사하고 실제 account ID와 고유 bucket name을 설정합니다.
3. 인증 profile을 선택해 계획을 검토합니다.

```powershell
$env:AWS_PROFILE = "YOUR_PROFILE"
terraform init
terraform plan
```

계정 ID guardrail은 잘못된 계정에 배포하는 것을 차단합니다. 출력되는
`backup_client_policy_json`은 별도 기존 role/permission set에 부여할 최소 object 권한 예시이며,
이 코드가 권한을 자동 연결하거나 access key를 만들지는 않습니다. Provisioner에는 S3 구성 관리 권한이 별도로 필요합니다.

계획 확인 후 실제 적용하고 `backup_destination`의 값을
[외부 백업 절차](../../../../docs/OFFHOST_BACKUP_DRILL.md)에 사용합니다.
처음 versioning을 활성화한 뒤에는 AWS 권장 전파 대기 시간인 15분 뒤 업로드를 시작합니다.
Local Terraform state와 object receipt도 호스트 밖에 보관해야 호스트 장애 뒤 관리·복구가 가능합니다.
이 폴더의 backend는 현재 local이며 state 자체를 자동으로 외부 보관하지 않습니다.

근거: [AWS provider S3 versioning](https://registry.terraform.io/providers/hashicorp/aws/5.100.0/docs/resources/s3_bucket_versioning),
[S3 versioning 전파](https://docs.aws.amazon.com/AmazonS3/latest/userguide/manage-versioning-examples.html).
