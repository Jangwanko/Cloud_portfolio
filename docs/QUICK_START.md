# 빠른 실행

## 실행 전 준비
- Docker Desktop 또는 Docker Engine 이 실행 중이어야 합니다
- Windows PowerShell 또는 Linux bash 기준으로 실행합니다

Windows PowerShell 기준으로는 Docker Desktop만 설치하고 실행되어 있으면 됩니다. `scripts/quick_start_all.ps1`이 시작 단계에서 `scripts/bootstrap_tools.ps1`을 호출해 `kind`, `kubectl`, `helm`을 저장소의 `tools/` 아래에 자동으로 준비합니다.

자동 준비되는 Windows 도구:
- `tools/kind.exe`
- `tools/kubectl.exe`
- `tools/helm/windows-amd64/helm.exe`

이 실행 바이너리는 bootstrap 뒤 생기는 local files이며 Git에서 제외됩니다. 저장소가 의도적으로 추적하는 tool artifact는 PostgreSQL HA render 재현용 `tools/helm-cache/repository/postgresql-ha-16.3.2.tgz`입니다. 그 밖의 Helm metadata/cache와 unused chart는 추적하지 않습니다.

최초 실행에는 인터넷 연결이 필요합니다. 도구가 이미 있으면 다시 다운로드하지 않고 기존 파일을 사용합니다. 도구를 강제로 다시 받으려면 아래 명령을 먼저 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_tools.ps1 -Force
```

Docker Desktop이 실행 중이지 않거나 인터넷 연결이 없고 도구가 아직 내려받아지지 않았다면 quick start는 preflight 단계에서 중단됩니다.

## 로컬 Python
로컬 테스트와 개발은 Dockerfile / CI와 같은 Python 3.11 기준으로 맞춥니다.

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

참고:
- `.venv`는 `.gitignore`와 `.dockerignore`에 포함되어 있습니다.
- 시스템 Python 3.13은 그대로 두고, 이 저장소만 `.venv`의 Python 3.11을 사용합니다.

Windows quick start가 `tools/` 아래에 자동으로 준비하는 도구:
- `tools/kind.exe`
- `tools/kubectl.exe`
- `tools/helm/windows-amd64/helm.exe`

Linux 에서는 아래 도구가 PATH 에 있어야 합니다.
- `docker`
- `kind`
- `kubectl`
- `helm`
- `curl`
- `python3`

Ubuntu / Debian 계열 Linux 에서는 아래 명령으로 기본 도구를 설치할 수 있습니다.

```bash
bash scripts/install_linux_prereqs.sh
```

## 권장 실행 사양

현재 성능 기준선은 아래 환경에서 측정했습니다.

| 항목 | 값 |
| --- | --- |
| Host CPU | AMD Ryzen 5 5600, 6 cores / 12 threads |
| Host memory | 약 32GiB |
| Docker Desktop 노출 사양 | 12 CPU, 약 15.6GiB memory |
| Kubernetes node allocatable | 12 CPU, `16338128Ki` memory |

실행 목적별 권장 사양:

| 목적 | 권장 사양 | 비고 |
| --- | --- | --- |
| Python unit test | 2-4 threads / 4-8GiB | Kubernetes 없이 빠른 검증 |
| 기능 검증 클러스터 | 6-8 threads / 12GiB 이상 | smoke, DLQ, DB 장애 테스트 중심 |
| 현재 성능 기준선 재현 | 12 threads / 16GiB 이상 | 100 VU / 30s 기준선 |
| 여유 있는 반복 테스트 | 16 threads / 24GiB 이상 | Kafka/PostgreSQL 재시작 압박 감소 |

권장 사양보다 낮은 host에서는 전체 HA stack과 100 VU 성능 기준선을 안정적으로 재현하기 어려울 수 있습니다.

낮은 사양에서 실행할 때의 실패는 대개 리소스 부족 신호로 나타납니다. 기능 검증 전에 CPU와 memory pressure를 확인합니다.

| 구간 | 흔한 실패 형태 | 해석 |
| --- | --- | --- |
| 설치 / rollout | `timed out waiting for the condition`, `CrashLoopBackOff`, `OOMKilled` | pod가 필요한 CPU/RAM을 제때 확보하지 못함 |
| readiness | `/health/ready` timeout, `degraded`, `not_ready` | Kafka / PostgreSQL / Pgpool이 준비되기 전에 timeout 도달 |
| Kafka intake | `503`, produce timeout | Kafka broker 응답 또는 ack 지연 |
| Worker 처리 | persisted timeout, consumer lag 증가 | Worker 처리량 또는 PostgreSQL persistence path 지연 |
| DLQ 검증 | `Poison event did not reach Kafka DLQ in time` | Worker가 제한 시간 안에 실패 event를 DLQ로 보내지 못함 |
| 성능 테스트 | error rate 증가, p95/p99 threshold 실패 | 처리량 한계 또는 resource contention |

이 경우 먼저 node CPU/RAM, pod restart count, Kafka consumer lag, Pgpool/PostgreSQL 상태를 확인합니다.

로컬에서 사용하는 포트:
- `80` for ingress HTTP
- `443` for optional local TLS validation
- `9090` for Prometheus when failover alert validation runs

`scripts/quick_start_all.ps1` 실행 전에 포트 충돌을 확인하고, 충돌이 있으면 배포 전에 중단합니다.

## 한 번에 실행
전체 로컬 검증은 아래 명령 하나로 실행할 수 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1
```

Linux:

```bash
bash scripts/quick_start_all.sh
```

이 스크립트는 아래 작업을 포함합니다.
- kind cluster 생성
- `ingress-nginx` 설치
- `metrics-server` 설치
- application image build 및 kind load
- PostgreSQL HA / Kafka runtime 배포
- `kube-state-metrics` 설치
- KEDA 설치
- application stack 배포; manual manifest v2 gate 초기값 `false`
- API startup Alembic migration과 dual-read/dual-write Worker 기동
- Worker rollout 완료 뒤 API `GENERIC_EVENTS_V2_ENABLED=true` 전환과 API rollout
- ingress readiness 확인
- Windows PowerShell 기본 실행에서는 smoke, DB recovery, HPA scaling test 실행
- Linux bash 기본 실행에서는 smoke test 실행

PostgreSQL credential lifecycle:

- 최초 Helm install: chart가 `messaging-postgresql-ha-postgresql` Secret의 credential 생성
- upgrade: Bitnami chart lookup으로 기존 Secret 재사용
- 실행 순서: PostgreSQL install/upgrade 완료 뒤 application manifest 적용; GitOps 경로도 PostgreSQL 준비 뒤 Argo CD Application 등록
- PVC를 유지한 채 Secret만 삭제한 경우: 새 credential 자동 생성으로 덮지 않고 기존 DB credential 복구 후 Secret 복원

DB 장애 상황까지 함께 검증하려면 아래처럼 실행합니다.

```bash
RUN_FAILURE_TESTS=true bash scripts/quick_start_all.sh
```

기본 접근 URL:
- API: `http://localhost`
- Grafana: `http://localhost/grafana`
- Grafana 대시보드: anonymous Viewer로 바로 조회
- Grafana admin 계정: `messaging-runtime-secrets`에 생성, 일반 데모 조회에는 불필요
- Prometheus: `http://localhost/prometheus/`

참고:
- 기본 실행과 문서는 `http://localhost` 기준으로 봅니다.
- HTTPS는 local self-signed certificate 기반의 TLS 검증용 보조 경로이며, 브라우저에서 보안 경고가 표시될 수 있습니다.

## 예상 소요 시간
아래 시간은 최근 kind + Docker Desktop 기준 대략적인 실행 시간입니다.

| 시나리오 | 스크립트 | 일반 소요 시간 |
| --- | --- | --- |
| 전체 quick start | `scripts/quick_start_all.ps1` | 약 12-18분 |
| Linux quick start | `scripts/quick_start_all.sh` | 약 12-18분 |
| Smoke test | `scripts/smoke_test.ps1` | 약 15-30초 |
| API contract test | `scripts/test_api_contracts.ps1` | 약 15-30초 |
| Linux smoke test | `scripts/smoke_test.sh` | 약 15-30초 |
| DB recovery test | `scripts/test_db_down.ps1` | 약 1-2분 |
| Linux DB recovery test | `scripts/test_db_down.sh` | 약 1-2분 |
| Stream ordering test | `scripts/test_stream_ordering.ps1` | 약 1분 |
| Ordering / failure injection | `scripts/ordering_failure_injection.py` | 약 1-2분 |
| HPA scaling test | `scripts/test_hpa_scaling.ps1` | 약 30-45초 |
| DLQ flow test | `scripts/test_dlq_flow.ps1` | 약 1-2분 |
| DLQ replay guard test | `scripts/test_dlq_replay_guard.ps1` | 약 1-2분 |
| k6 load test | `scripts/test_k6_load.ps1` | 약 1분 |

## 선택 실행
운영 alert wiring까지 별도로 확인하려면:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_operational_alerts.ps1 -SkipReset
```

추가로 검증하는 항목:
- `MessagingDlqEventsIncreasing`
- `MessagingDlqReplayBlocked`
- `MessagingDeploymentUnavailableReplicas`

## 별도 부하 테스트
Kafka performance suite 는 기능 검증과 분리해서 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_kafka_performance_suite.ps1
```

이전 benchmark 데이터를 제거한 비교 측정은 아래 명령을 사용합니다. `-CleanBenchmarkState`는 local event/request/idempotency/notification 데이터를 지우고 current Kafka topic을 재생성하므로 disposable local cluster에서만 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_kafka_performance_suite.ps1 -CleanBenchmarkState
```

이 suite는 아래 순서로 실행됩니다.

- 선택한 경우 local DB benchmark state와 Kafka topic 초기화, delayed log deletion quiet period 대기
- Kubernetes runtime 상태 확인
- API/Worker 최소 replica, API CPU, readiness, fresh consumer lag `0` steady-state 확인
- same-stream ordering 보장 검증
- Kafka async persisted-status client 관측 지연 측정
- k6 Kafka intake load 측정
- HPA / metrics sanity 확인
- `results/kafka-performance/latest.txt`에 최신 결과 저장

현재 `async persistence` 출력의 `accepted_to_status_observed_ms`는 API `queued_at`부터 client가 `persisted` status를 관측할 때까지입니다. 200ms polling interval과 network delay를 포함하므로 Worker commit histogram과 구분합니다. 2026-06 원본의 `accepted-to-persisted`는 PostgreSQL row `created_at`/row-visible proxy이고 event status `200`은 `202 Accepted` 계약 명시 전 historical evidence입니다.

Ordering / failure injection 검증:

```powershell
.venv\Scripts\python.exe scripts\ordering_failure_injection.py --scenario all --event-count 100
```

- single stream `A001..A100`
- multi stream `A001..A100`, `B001..B100`, `C001..C100`
- Pgpool 장애 주입 중 accepted / persisted / missing / duplicate / mixed payload / DLQ / duration 확인
- 결과는 `results/ordering-failure/latest.json`에 저장됩니다
- Windows / Docker Desktop 로컬 측정 왜곡을 줄이기 위해 기본값은 `http://127.0.0.1` 연결과 `Host: localhost` header를 함께 사용합니다.

개별 k6 test 만 실행하려면 아래 명령을 사용합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_k6_load.ps1
```

참고:
- 이 테스트의 목적은 performance 측정입니다. health check 범위는 제외합니다.
- `test_k6_load.ps1` 기본값은 `single500` profile, 100 VU, 10초입니다
- `run_kafka_performance_suite.ps1` 기본값은 100 VU, 30초입니다
- `-CleanBenchmarkState`는 destructive local reset이며 내부 helper가 `-ConfirmDataLoss`를 명시해 실행합니다.
- k6는 backlog와 latency spike를 만들 수 있으므로 장애 검증 뒤, reset 후 마지막에 실행합니다.

## 권장 테스트 순서
현재 클러스터 상태만 빠르게 확인하려면 아래 스크립트를 먼저 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_portfolio_status.ps1 -SkipArgoCd
```

이 스크립트는 테스트 데이터를 만들지 않고 Kubernetes, API readiness, Prometheus scrape, kafka-exporter, KEDA 상태를 읽어 운영 상태를 요약합니다. `quick_start_gitops.ps1`로 Argo CD까지 설치한 profile에서는 `-SkipArgoCd`를 제거합니다.

서비스 전체 흐름을 순서대로 점검하려면 [SERVICE_PROCESS_CHECKLIST.md](SERVICE_PROCESS_CHECKLIST.md)를 따릅니다.

전체 검증을 순서대로 실행하려면 아래 스크립트를 사용합니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_recommended_tests.ps1
```

이 순서는 아래 원칙을 따릅니다.

- correctness / 장애 정책 검증을 먼저 수행합니다.
- k6 부하 테스트는 reset 후 맨 마지막에 수행합니다.
- k6 이후 final reset을 수행해 Kafka backlog / DB 상태를 정리합니다.

수동 실행 순서:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/reset_k8s_state.ps1
powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1 -SkipReset
powershell -ExecutionPolicy Bypass -File scripts/test_api_contracts.ps1 -SkipReset
powershell -ExecutionPolicy Bypass -File scripts/test_stream_ordering.ps1 -SkipReset
powershell -ExecutionPolicy Bypass -File scripts/test_db_down.ps1 -SkipReset
powershell -ExecutionPolicy Bypass -File scripts/reset_k8s_state.ps1
powershell -ExecutionPolicy Bypass -File scripts/run_kafka_performance_suite.ps1 -SkipReset
powershell -ExecutionPolicy Bypass -File scripts/reset_k8s_state.ps1
```

## 개별 시나리오
Smoke test:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1
```

API contract:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_api_contracts.ps1
```

DB outage and recovery:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_db_down.ps1
```

Stream ordering:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_stream_ordering.ps1 -EventCount 100
```

Kubernetes autoscaling:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_hpa_scaling.ps1
```

참고:
- API는 CPU HPA를 사용합니다.
- Worker는 KEDA Kafka lag scaling을 사용합니다.

DLQ flow:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_dlq_flow.ps1
```

DLQ replay guard:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_dlq_replay_guard.ps1
```

## GitOps 빠른 실행
Argo CD 요구사항을 보여주기 위한 GitOps bootstrap script도 포함되어 있습니다. master `local-ha` overlay는 CI bot commit 뒤 GHCR의 12-character SHA tag를 사용합니다.

전제:
- 이 저장소가 클러스터에서 접근 가능한 Git remote 에 push 되어 있어야 합니다
- master push의 CI validation과 GHCR image publish 완료
- Actions bot이 `k8s/gitops/overlays/local-ha/kustomization.yaml`의 image tag를 갱신한 commit까지 remote에 반영
- GHCR package public 또는 cluster `imagePullSecret` 준비

실행:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_gitops.ps1 `
  -RepoUrl https://github.com/<your-account>/<your-repo>.git `
  -Revision master
```

`dev-kafka` 개발 클러스터를 확인하려면 `-Revision dev-kafka`로 실행합니다. CI의 `validate`를 통과한 `publish-dev-kafka-image` job이 candidate digest를 검증하고 GHCR commit-SHA image로 승격합니다. branch가 여전히 같은 revision을 가리킬 때만 `k8s/gitops/overlays/local-ha/kustomization.yaml`의 tag commit을 push하며, Argo CD가 그 변경을 sync합니다.

이 흐름은 아래를 수행합니다.
- local cluster bootstrap
- remote committed overlay의 registry image/tag 해석과 manifest 접근 사전 확인
- HA PostgreSQL / Kafka runtime 설치
- Argo CD 설치
- `k8s/gitops/overlays/local-ha` 를 가리키는 `Application` 생성
- generic v2 sync 순서: gate `false`인 `messaging-env` Secret wave `-3` → 일반 Sync migration Job wave `-2` → Worker wave `-1` → `local-ha` overlay가 API에 gate `true`를 넣는 wave `0`
- 기본 실행은 committed overlay 추적; 명시적 image repository/tag를 함께 넘긴 경우에만 Kustomize override 적용
- readiness 확인과 smoke test 실행

PostgreSQL은 Argo CD Application 등록 전에 Helm으로 install/upgrade합니다. application의 `DB_PASSWORD`는 `messaging-postgresql-ha-postgresql` Secret의 `password` key를 참조합니다. upgrade는 기존 Secret을 재사용하며, PVC가 남은 상태에서 Secret이 유실되면 credential recovery가 선행되어야 합니다.

주의:

- public repository 기본 실행: remote revision의 CI bot SHA tag commit 추적
- private repository 또는 의도적 고정 배포: `-ImageRepository <ghcr-path> -ImageTag <tag>`를 함께 지정
- private GHCR: preflight용 `docker login ghcr.io`와 cluster pull용 `imagePullSecret` 필요
- registry 없이 local image만 사용하는 경로: `quick_start_all.ps1`
