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

## PostgreSQL Monitoring Role
PostgreSQL HA 설치 후 `k8s/scripts/install-ha.ps1`는 `portfolio` 사용자에게 `pg_monitor` 역할을 부여합니다.

이 권한은 `pg_stat_replication`을 읽기 위한 PostgreSQL 내장 읽기 전용 모니터링 역할입니다. API는 이 정보를 사용해 standby의 `state`, `sync_state`, replication lag를 Prometheus metric으로 노출합니다.

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
logical backup을 현재 클러스터 DB에 적용할 수 있습니다.

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
- `-ResetSchema`를 주면 `public` schema를 비운 뒤 backup SQL을 적용합니다.
- 현재 목적은 disposable local cluster 기준의 운영 흐름 검증입니다.

## Demo Access
로컬 데모 기준 운영 UI 경로:
- Grafana: `http://localhost/grafana`
- Grafana login: `ID admin` / `Password 1q2w3e4r`
- Prometheus: `http://localhost/prometheus/`

참고:
- 기본 운영 문서와 데모 경로는 `http://localhost` 기준입니다.
- HTTPS는 local self-signed certificate 기반의 TLS 검증용 보조 경로이며, 브라우저에서 보안 경고가 처음 한 번 표시될 수 있습니다.

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

로컬 검증에서는 Kubernetes Secret을 사용하고, 운영형 환경에서는 외부 secret manager로 확장할 수 있습니다.

## TLS Position
현재 ingress TLS는 local self-signed certificate 기반입니다.

현재 목적:
- 로컬에서도 TLS termination 구조를 확인할 수 있게 합니다.
- API / Grafana / Prometheus가 같은 ingress 아래에서 열리는 구성을 HTTP 기준으로 운영하고, 필요할 때만 HTTPS로 TLS 동작을 검증합니다.

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
- 운영 UI 접근 제한: 로컬 검증 기준으로 접근 가능하게 유지

## Kafka Operational Scenarios
현재 event intake는 Kafka append 성공을 write-path 수락 기준으로 봅니다.

- Kafka bootstrap 또는 topic append가 불가능하면 API는 새 write request를 fail-fast로 거절합니다.
- Worker는 Kafka consumer group으로 ingress topic을 처리하고, 재시도 한계를 넘은 event는 DLQ topic으로 이동합니다.
- Worker autoscaling은 KEDA Kafka lag 기준으로 동작합니다.

## Kafka persistence 정책
- Kafka는 accepted write를 순서 있는 commit log로 보관합니다.
- 최종 영속 저장소는 PostgreSQL입니다.
- 같은 stream은 같은 Kafka key를 사용해야 하며, 같은 key는 같은 partition에 들어가므로 partition 내부 순서가 유지됩니다.
- PostgreSQL 영속화 이전 구간의 내구성은 Kafka topic replication factor와 `acks` 정책에 의해 결정됩니다.

## Kafka fail-fast 정책
- Kafka bootstrap unreachable이면 write path failure로 봅니다.
- Kafka topic append 실패도 write path failure로 봅니다.
- Kafka 장애 동안에는 API가 새 write request를 계속 받지 않고 fail-fast 상태로 응답합니다.
- 즉, enqueue 불가 상태를 soft failure가 아니라 write path failure로 취급합니다.

## readiness / alert 운영 기준
- readiness는 `ready`, `degraded`, `not_ready`를 즉시 반영합니다.
- replica count와 standby count는 degraded 판단 기준으로 사용합니다.
- PostgreSQL degraded는 primary write 불가, standby 부족, replication state 불안정, replication lag 상승을 포함합니다.
- PostgreSQL primary loss 중에도 Kafka append path가 살아 있으면 API readiness는 `degraded`입니다.
- 로컬 데모에서는 async streaming standby를 정상 ready 상태로 봅니다.
- `30초`는 alert 승격 유예이며 readiness 지연에는 사용하지 않습니다.
- Kafka outage와 PostgreSQL primary loss는 즉시 critical로 봅니다.

자세한 상태 모델과 응답 예시는 [RELIABILITY_POLICY.md](RELIABILITY_POLICY.md)에서 함께 관리합니다.

## 운영 확장 포인트
- 운영 UI 접근 정책 강화
- secret 외부화 방향 정리
- alert / incident runbook 보강
