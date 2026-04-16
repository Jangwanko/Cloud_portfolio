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

## Access Policy
현재 운영 경로는 일반 서비스 경로와 구분하되, 포트폴리오 데모 기준으로 쉽게 접근할 수 있게 유지합니다.

- API
  - 서비스 경로로 취급합니다.
  - bearer token 기반 인증이 적용됩니다.
- Grafana
  - 운영 UI로 취급합니다.
  - 로그인 유지 상태로 노출합니다.
  - 데모에서는 접근 가능하게 두되, 실서비스에서는 별도 접근 제한이 필요합니다.
- Prometheus
  - 운영 / 관측 UI로 취급합니다.
  - 현재는 데모 편의를 위해 ingress로 직접 접근 가능하게 둡니다.
  - 실서비스에서는 내부망, VPN, basic auth, SSO 같은 제한이 필요합니다.

현재 목적:
- 운영 경로와 일반 경로를 구분하고 있다는 점을 보여줍니다.
- 동시에 면접관이 직접 Grafana / Prometheus를 확인하는 흐름은 막지 않습니다.

## Secret Handling
현재 민감한 값은 코드나 매니페스트 하드코딩 대신 Kubernetes secret로 분리합니다.

현재 분리된 값:
- `AUTH_SECRET_KEY`
- `ACCESS_TOKEN_TTL_SECONDS`
- `GRAFANA_ADMIN_USER`
- `GRAFANA_ADMIN_PASSWORD`

현재 방식:
- 로컬 kind 기준에서는 `messaging-runtime-secrets`를 생성해 주입합니다.
- 앱과 운영 UI는 이 secret를 환경변수로 읽습니다.

운영 확장 방향:
- local: Kubernetes secret
- staging / prod: 외부 secret manager 또는 배포 파이프라인 연동 secret 관리

즉 현재는 “하드코딩 제거와 실행 경로 분리”까지 적용된 상태이고, 이후 실서비스 단계에서 secret 저장소를 외부화하는 방향으로 확장할 수 있습니다.

## TLS Position
현재 ingress TLS는 local self-signed certificate 기반입니다.

현재 목적:
- 로컬에서도 HTTPS 진입과 TLS termination 구조를 보여줍니다.
- API / Grafana / Prometheus가 같은 ingress 아래에서 HTTPS로 열리는 구성을 검증합니다.

운영 확장 방향:
- local: self-signed certificate
- actual deployment: trusted certificate, `cert-manager`, 또는 cloud-managed certificate

즉 현재 TLS는 로컬 검증용 구현이고, 운영 단계에서는 신뢰된 인증서 체계로 바꾸는 것이 다음 단계입니다.

## Current Operational Position
현재 상태는 아래처럼 정리할 수 있습니다.

- runtime secret 분리: 적용됨
- 수동 PostgreSQL backup: 구현 및 검증 완료
- backup 기반 restore: 구현 및 검증 완료
- 주 1회 backup `CronJob`: HA 매니페스트에 포함 및 클러스터 적용 완료
- 운영 UI 접근 제한: 데모 친화적 수준으로 유지

## Redis Operational Scenarios
현재 Redis 관련 운영 검증은 두 가지로 나눠서 봅니다.

- complete outage
  - `scripts/test_redis_down.ps1`
  - 전체 Redis 접근 불가 시 event intake 실패와 복구 후 재수락을 확인합니다.
- single-node failover
  - `scripts/test_redis_failover.ps1`
  - Redis pod 하나 재시작 후 readiness 복구와 event intake 유지 여부를 확인합니다.

이 구분을 두는 이유:
- Redis HA + Sentinel 환경에서는 단일 pod 중단이 곧바로 전체 Redis down과 같지 않습니다.
- 따라서 outage와 failover를 별도 운영 시나리오로 보는 편이 더 정확합니다.

## Next Operational Steps
- 운영 UI 접근 정책 정리
- secret 외부화 방향 정리
- alert / incident runbook 보강
