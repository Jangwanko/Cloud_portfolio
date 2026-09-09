# Release failure evidence

`20260905T133642Z`는 2026-09-05 실제 kind / Argo CD 실험의 성공 실행입니다.
재현 절차와 해석 범위는 [RELEASE_FAILURE_LAB.md](../../docs/RELEASE_FAILURE_LAB.md)를 참고합니다.

- `summary.json`: 실행 당시 source/image/script hash, 환경, 4단계 canary와 DB row, 최종 판정·정리 결과
- `evidence.compact.json`: 원본 28개 checkpoint에서 추출한 Argo 상태, Deployment 식별자·generation·template hash, migration Job 상태. 각 원본의 SHA-256 포함
- `migration-failure-log.json`: 의도한 `division by zero`와 실제 Alembic transaction 실패 로그
- `hashes.json`: 실행 종료 당시 원본 34개 파일의 SHA-256 목록. 원본은 모두 로컬에서 대조 통과했으며 compact projection은 이후 생성되어 이 목록에 포함되지 않음

전체 `raw/`와 개별 canary 파일은 로컬에 보관하고 Git에서는 제외합니다. Summary에 canary 결과와 검증 대상 row가 포함되어 있습니다.
실험 스크립트는 실행 시점에 uncommitted였으며 summary의 script hash가 정확한 실행본 식별자입니다.
단일 실행이며 반복 측정 평균이나 운영 SLA를 뜻하지 않습니다. Notification 검증 범위는 DB attempt 기록입니다.

초기 설정 실패도 별도 로컬 실행 디렉터리에 남겼습니다. 성공 실행에 합산하지 않습니다.

| Run | 결과 / 원인 |
| --- | --- |
| `20260905T050219Z` | FAIL: PostgreSQL image entrypoint 생략으로 UID 1001의 NSS 초기화 누락. 원인 로그 별도 보존 |
| `20260905T050523Z` | FAIL: baseline migration 실패. 당시 Job 상세 로그는 수집하지 못했으며 이후 진단 수집 보강 |
| `20260905T050738Z` | FAIL: PostgreSQL `pg_hba.conf`에 다른 Pod의 접속 허용 규칙 누락 |

Image entrypoint 유지, 검증된 Pod CIDR에 해당 DB/user의 SCRAM 인증 규칙 추가,
probe Pod에서 실제 DB 연결 사전 확인을 적용한 뒤 성공 실행을 수행했습니다.
