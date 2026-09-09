# 클러스터 외부 백업·복원 실험

## 현재 상태: 로컬 리허설 PASS, 외부 저장소 대기

2026-09-05 실제 PostgreSQL 스키마와 합성 v2 이벤트 10개로 custom-format dump를 생성했습니다.
원본 실험 namespace를 삭제한 뒤 새 namespace의 PostgreSQL에 복원했습니다.
11개 테이블의 행 수·행 내용 SHA-256, sequence 4개, column 정의, Alembic version이 일치했습니다.
Dump는 `25,147 bytes`, restore command `0.297s`, Pod 생성부터 검증까지 `8.921s`입니다.
합성 데이터의 단일 소규모 실행이며 production RTO/RPO나 전체 서비스 복구 시간으로 사용하지 않습니다.
원본·복원 실험 리소스 cleanup은 완료했습니다.
손상·덮어쓰기·복원 불일치 차단 테스트 14개를 포함한 전체 local suite는 `654 passed`입니다.

**외부 업로드·재다운로드는 미실행입니다.** 사용 가능한 버킷과 로컬 인증 프로필이 아직 지정되지 않았습니다.
저장소가 없는 경우를 위해 [백업 전용 S3 Terraform 구성](../infra/terraform/envs/backup/README.md)을 준비했습니다.
기존 AWS 전체 인프라와 독립된 구성이며 실제 계정의 plan/apply는 수행하지 않았습니다.
같은 호스트의 dump로 수행한 이번 결과는 호스트 장애 복구 증거가 아닙니다.
근거: [로컬 리허설 결과](../results/offhost-backup/20260905-local-rehearsal.json).

## 실행 순서

애플리케이션 이미지와 분리된 host-only 의존성을 설치합니다.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-backup.txt
```

새 경로에서 준비하거나, 이번에 생성한 `backups/offhost-drill-20260905`를 사용합니다.
`prepare`는 기존 release lab으로 synthetic event를 생성하며 기존 `messaging-app` DB에 쓰지 않습니다.

```powershell
.venv\Scripts\python.exe scripts/postgres_backup_drill.py --context kind-messaging-ha prepare --directory backups/offhost-drill-NEW
```

아래 `PROFILE`, `REGION`, `BUCKET`은 사용자가 지정한 실제 값으로 대체합니다.
덤프와 원본 검증 manifest를 모두 외부에 보관합니다. 비밀키를 명령 인자로 전달하지 않습니다.

```powershell
.venv\Scripts\python.exe scripts/object_storage_backup.py --profile PROFILE --region REGION upload --file backups/offhost-drill-20260905/postgres.dump --bucket BUCKET --prefix portfolio-backup-drill --receipt backups/offhost-drill-20260905/dump-receipt.json
.venv\Scripts\python.exe scripts/object_storage_backup.py --profile PROFILE --region REGION upload --file backups/offhost-drill-20260905/manifest.json --bucket BUCKET --prefix portfolio-backup-drill --receipt backups/offhost-drill-20260905/manifest-receipt.json
.venv\Scripts\python.exe scripts/object_storage_backup.py --profile PROFILE --region REGION download --receipt backups/offhost-drill-20260905/dump-receipt.json --output backups/offhost-drill-20260905/downloaded.dump
.venv\Scripts\python.exe scripts/object_storage_backup.py --profile PROFILE --region REGION download --receipt backups/offhost-drill-20260905/manifest-receipt.json --output backups/offhost-drill-20260905/downloaded-manifest.json
.venv\Scripts\python.exe scripts/postgres_backup_drill.py --context kind-messaging-ha restore --dump backups/offhost-drill-20260905/downloaded.dump --manifest backups/offhost-drill-20260905/downloaded-manifest.json --output backups/offhost-drill-20260905/remote-restore.json
```

Receipt의 bucket/key/version과 digest는 호스트 밖의 복구 기록에도 보관해야 합니다.
실제 외부 검증 완료 판정에는 두 upload/download receipt와 restore 결과를 함께 확인해야 합니다.
Restore 도구 자체는 입력 파일의 다운로드 출처를 알 수 없으므로 `external_storage_verified=false`를 유지합니다.

## 저장소 계약

- 기존의 비공개 버킷 사용. 도구는 버킷 생성·ACL 변경·원격 삭제를 수행하지 않습니다.
- Host와 다른 장애 영역의 저장소를 선택합니다. 같은 PC의 MinIO는 외부 장애 영역 증거가 아닙니다.
- SDK의 로컬 profile/credential chain 사용. 최소 권한은 지정 prefix의 `s3:PutObject`, `s3:GetObject`, version 사용 시 `s3:GetObjectVersion`입니다.
- `If-None-Match: *`, 고유 object key, `AES256` server-side encryption, SHA-256 전송 검증을 요구합니다. 암호화 정책이 KMS를 강제하는 버킷은 현재 도구와 별도 조정이 필요합니다.
- Versioning된 버킷이면 응답 version ID로 다운로드합니다. Versioning이 없으면 특정 버전 보존을 주장하지 않습니다.
- S3 호환 저장소는 HTTPS endpoint와 위 API 계약의 지원을 확인해야 합니다. 미지원이면 실패하며 검증을 우회하지 않습니다.
- 5 GiB 이하 단일 PUT 전용입니다. 자동 스케줄·retention·실패 alert·PITR은 아직 구현하지 않았습니다.

## 검증 범위

업로드 응답 checksum 확인 후 실제 다운로드 바이트의 SHA-256/size를 대조합니다.
손상 파일은 최종 다운로드 경로에 공개하지 않으며 복원 전에도 source manifest의 dump fingerprint를 다시 확인합니다.
복원 대상은 임의의 기존 DB 주소를 받지 않고 매번 새 namespace의 network listener가 없는 PostgreSQL Pod를 생성합니다.
Cleanup은 namespace UID와 소유 label이 일치할 때만 수행합니다. 원본 SQL/dump 내용은 진단 로그에 출력하지 않습니다.

이번 source는 quiescent synthetic workload이며 dump 전후 manifest 일치를 확인했습니다.
동시 쓰기가 있는 운영 DB의 일관된 비교에는 별도 snapshot coordination이 필요합니다.
PostgreSQL dump만으로 Kafka 미처리 이벤트, offset, Secret, 인프라를 복원하지는 않습니다.
실제 cluster-loss·host-loss 훈련과 지속적인 RPO 측정은 외부 왕복 검증 다음 단계입니다.

참고: [PostgreSQL logical dump의 일관성](https://www.postgresql.org/docs/17/backup-dump.html),
[S3 checksum 검증](https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity.html).
