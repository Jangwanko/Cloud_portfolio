# Operations

운영 관점에서 필요한 secret, backup, restore, 운영 UI 경로를 정리한 문서입니다.

## Runtime Secrets
로컬 kind 기준 운영 보강을 위해 runtime secret를 별도로 생성합니다.

생성 스크립트:

```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/install-runtime-secrets.ps1
```

생성 대상:
- `AUTH_SECRET_KEY`
- `ACCESS_TOKEN_TTL_SECONDS`
- `GRAFANA_ADMIN_USER`
- `GRAFANA_ADMIN_PASSWORD`

특징:
- `scripts/quick_start_all.ps1`와 `k8s/scripts/setup-kind.ps1`에서 자동 실행됩니다.
- Grafana 자격증명은 더 이상 매니페스트에 하드코딩하지 않습니다.
- API / Worker / DLQ replayer가 동일 secret를 받아 인증 관련 값을 사용합니다.

## PostgreSQL Backup
로컬 HA PostgreSQL에 대해 logical backup을 생성할 수 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/backup_postgres_k8s.ps1
```

결과:
- `backups/postgres-<timestamp>.sql`

동작 방식:
- `messaging-postgresql-ha-postgresql` secret에서 DB password를 읽습니다.
- `pgpool` 서비스 경유로 `pg_dump`를 수행합니다.
- 결과를 로컬 `backups/` 디렉터리에 저장합니다.

## Weekly Backup Schedule
HA 배포에는 주 1회 PostgreSQL logical backup을 남기는 `CronJob`이 포함되어 있습니다.

- 리소스 이름: `postgres-weekly-backup`
- 스케줄: `0 3 * * 0`
- 저장 위치: cluster PVC `postgres-backups`
- 보관 정책: 최근 8개 dump만 유지

확인 예시:

```powershell
kubectl get cronjob -n messaging-app
kubectl get pvc -n messaging-app
```

참고:
- 현재 목적은 “주기 backup 설정이 포함되어 있다”는 운영 구성을 보여주는 것입니다.
- 필요하면 이후 일 단위 또는 더 짧은 주기로 쉽게 변경할 수 있습니다.

## PostgreSQL Restore
기존 logical backup을 현재 클러스터 DB에 다시 적용할 수 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/restore_postgres_k8s.ps1 `
  -BackupFile backups/postgres-20260416-163842.sql `
  -Force
```

스키마 초기화 후 복원:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/restore_postgres_k8s.ps1 `
  -BackupFile backups/postgres-20260416-163842.sql `
  -ResetSchema `
  -Force
```

주의:
- 기본값으로는 실행하지 않습니다. 반드시 `-Force`가 필요합니다.
- `-ResetSchema`를 주면 기존 `public` schema를 비운 뒤 backup SQL을 다시 적용합니다.
- 현재 목적은 disposable local cluster 기준의 운영 흐름 검증입니다.

## Demo Access
로컬 데모 기준 운영 UI 경로:
- Grafana: `http://localhost/grafana`
- Prometheus: `http://localhost/prometheus/`
- TLS Grafana: `https://localhost/grafana`
- TLS Prometheus: `https://localhost/prometheus/`

참고:
- HTTPS는 local self-signed certificate를 사용합니다.
- 브라우저에서는 보안 경고가 처음 한 번 표시될 수 있습니다.

## Current Operational Position
현재 상태는 아래처럼 정리할 수 있습니다.

- runtime secret 분리: 적용됨
- 수동 PostgreSQL backup: 구현 및 검증 완료
- backup 기반 restore: 구현 및 검증 완료
- 주 1회 backup `CronJob`: HA 매니페스트에 포함 및 클러스터 적용 완료
- 운영 UI 접근 제한: 데모 친화적 수준으로 유지

## Next Operational Steps
- 운영 UI 접근 정책 정리
- secret 외부화 방향 정리
- alert / incident runbook 보강
