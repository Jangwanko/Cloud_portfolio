# Demo Lite

## Terraform 실환경 검증 — OpenStack, 2026-09-25

OpenStack에서 Terraform으로 VM·네트워크 등 11개 리소스를 생성·삭제하고,
빈 환경에서 데모라이트 서비스를 자동 재구축했습니다. 이후 기존 VM의 CPU를
2 → 3 vCPU로 변경하고 데이터 보존과 서비스 복구까지 검증했습니다.

| 검증 | 실제 결과 |
| --- | --- |
| 인프라 수명주기 | 11개 생성 → 11개 삭제 → 빈 state → 11개 재생성 |
| 자동 설치 | 단일 실행 명령으로 k3s → DB·Kafka → migration → Worker → API → 이벤트 검사 |
| 기존 VM 사양 변경 | 플레이버 1개 생성, VM 1개 in-place 수정, 삭제 0개 |
| 데이터 보존 | 변경 전 이벤트를 동일 request ID로 조회; type·payload·metadata 일치 |
| 변경 후 처리 | 새 이벤트 HTTP 202 → persisted → 조회 성공 |
| 상태 일치 | 재구축 후와 resize 후 모두 terraform plan 종료 코드 0 |
| 최종 사양 | 3 vCPU / RAM 4 GiB / 디스크 40 GiB; VM ID와 Floating IP 유지 |

검증 대상은 기존 OpenStack 위에 생성한 단일 VM의 core demo-lite 구성입니다.
사양 변경 중 서비스 재시작이 있었고 정상 복구됐습니다. 무중단·HA·Git 기반 CI/CD
검증으로 해석하지 않습니다. 초기 실행기의 PowerShell·SSH 대기 문제를 수정한 뒤,
두 번째 빈 환경 구축은 수동 보정 없이 통과했습니다.

[실행 구성](../infra/terraform/envs/openstack-demo-lite/README.md) · [검증 기록](../infra/terraform/envs/openstack-demo-lite/VALIDATION.md)

## 이전 public/source 상태 — 2026-08-24

| 대상 | 상태 |
| --- | --- |
| Public `demo-lite` runtime | image `7489ab270995`, UI `2.4.0`, API `2.1.0`, readiness `ready` |
| Public scaling evidence | `message-worker` lag peak `828`, HPA desired·actual replica `1→2` |
| `demo-dev` candidate | UI `2.4.0`, API `2.1.0`, tests `359 passed`, static Investigation replay와 7일 retention |
| `master` source | UI `2.3.1`, API `2.1.0`, merge `cab7647`, full local HA profile |
| Fresh k3s bootstrap | image `207d7b90813a`, UI `2.3.1`, API `2.1.0`, Argo CD `Synced / Healthy`, 외부 domain 재연결 대기 |

Public runtime과 branch source는 UI badge, readiness `app_version`, runtime image로 각각 확인합니다. Branch의 최신 commit만으로 public 배포 완료를 판단하지 않습니다.

## 목적과 범위

- 대상: Ubuntu, 2코어급 CPU, 8GB memory, 제한된 disk
- 배포: k3s, Argo CD, registry image
- 흐름: API → Kafka → Worker → PostgreSQL
- 시나리오: 범용 event contract 위의 주문·결제 reference data
- 제외: 3-broker Kafka HA, PostgreSQL HA failover, full-profile 성능 baseline

Demo Lite 결과는 저사양 배포와 lag 기반 확장 동작의 증거입니다. Full local HA의 broker·standby 구성과 성능 수치를 대체하지 않습니다.

현재 candidate는 full source의 API cache·snapshot topic 3개 제거와 PostgreSQL read model을 그대로 사용합니다. 저사양 profile은 Kafka broker `1`, partition `3`, replication factor `1`, PostgreSQL single instance, core Worker `1→2`, notification Worker fixed `1`로 제한합니다. notification job을 쌓아 두지 않도록 최소 consumer `1`을 유지합니다.

## Storage retention

- PostgreSQL logical dump: `Asia/Seoul` 일요일 03:00 생성, 7일 경과 파일 삭제, latest 8 secondary cap
- Completed backup Job: `ttlSecondsAfterFinished=604800`
- Kafka ingress·DLQ·notification: 7일, partition별 `128MiB`, segment `32MiB`
- Prometheus: 7일, block `512MB`, `emptyDir` `768MiB`
- PostgreSQL event row: 자동 삭제 제외. demo reset 또는 별도 data lifecycle 정책으로 관리
- Pod log: kubelet size rotation 사용. host journald 상한은 서버 bootstrap 운영 설정으로 관리

7일 기준은 demo workload가 만든 운영 부산물의 로컬 디스크 점유를 제한합니다. PostgreSQL durable state와 object-storage backup 정책은 별도 경계입니다.

## Profile 차이

| 구성 | Full local HA | Demo Lite |
| --- | ---: | ---: |
| Kafka | `3` brokers | `1` broker |
| PostgreSQL | primary `1` + standby `2` | single instance |
| Pgpool | `2` | `1` |
| API | `6→8` | `1→2` |
| Core Worker | `2→4` | `1→2` |
| Notification Worker | `1→2` | fixed `1` |
| 목적 | HA·복구·성능 실험 | 공개 시연과 저사양 운영 |

## UI 버전 경계

- Public UI `2.1.0`: generic v2 envelope와 `202`, Pipeline Evidence 내 저장 증거
- Current UI `2.4.0`: 기존 진행률·Worker panel과 actual `local-ha` recorded Investigation의 static replay
- API `2.1.0`: `/v2/streams/{stream_id}/events`, PostgreSQL request status·event read, persistence summary, `/ops/summary`

Candidate 기능은 `demo-dev` 검증과 release workflow를 거쳐 `demo-lite`에 게시된 뒤 public 기능으로 기록합니다.

## Release 경계

1. `demo-dev` source와 test 확정
2. CI validation 성공
3. 검증된 commit으로 image build
4. 동일 commit과 image tag를 `demo-lite` release commit에 기록
5. Argo CD sync
6. UI badge, readiness, generic event `202`, Worker scaling 확인

Schema release 순서는 migration → Worker → API gate입니다. API만 먼저 교체하는 배포는 허용하지 않습니다.

## 확인

Public:

- Demo UI: `https://vm118.js-banjiha.cloud/demo/order-dashboard.html`
- Swagger: `https://vm118.js-banjiha.cloud/docs`
- Readiness: `https://vm118.js-banjiha.cloud/health/ready`
- Grafana: `https://vm118.js-banjiha.cloud/grafana/d/messaging-portfolio-overview/reliable-event-processing-operations-overview?orgId=1&refresh=5s`

Local branch:

```powershell
git branch --show-current
git status --short
```

`master`에는 full local HA 실행 경로를 유지합니다. Demo Lite overlay와 배포 script의 기준 source는 `demo-dev`와 release branch에서 확인합니다.
