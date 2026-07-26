# Demo Lite

`demo-lite`는 2코어급 서버에서 현재 event workload를 시연하기 위한 축소 Kubernetes profile입니다. API → Kafka → Worker → PostgreSQL 흐름, lag 기반 Worker 확장, Grafana 관측, DLQ/replay를 유지합니다. 단일 broker와 단일 PostgreSQL을 사용하므로 HA·failover·full performance 증거로 해석하지 않습니다.

## 버전과 배포 상태

| 대상 | 현재 상태 |
| --- | --- |
| `master` source | Demo UI `2.0.0`, API `2.0.0`, generic v2 |
| `demo-dev` candidate | Demo UI `2.2.0`, API `2.0.0`, generic v2, 저사양 overlay |
| public `demo-lite` | UI `2.1.0`, API `2.0.0`, generic v2, event `202` |

`demo-dev` UI `2.2.0` candidate는 아직 공개 서버의 상태가 아닙니다. 공개 URL이 새 image와 GitOps revision으로 전환되고 UI badge, readiness `app_version`, Kafka·DB 동시 진행률, Worker peak 표시를 확인한 뒤 public 상태를 갱신합니다.

## 유지하는 기능

- generic event contract: `POST /v2/streams/{stream_id}/events`
- Kafka append 후 `202 Accepted`
- Worker의 PostgreSQL 비동기 persistence
- stream ordering, retry, DLQ, replay guard
- request status와 snapshot materialized cache
- Kafka lag 기반 Worker KEDA `1..2`
- CPU 기반 API HPA `1..2`
- Prometheus, Grafana, kube-state-metrics, kafka-exporter
- schema migration Job과 GitOps sync wave

## 줄이는 자원

| 구성 | demo-lite | full local-ha에서 생략되는 증거 |
| --- | ---: | --- |
| Kafka | `1` broker, `3` partitions, RF `1`, min ISR `1` | broker quorum과 replica 장애 내성 |
| PostgreSQL | `1` pod | standby와 primary failover |
| Pgpool | `1` pod | Pgpool replica 가용성 |
| API | `1..2` | full profile 처리량 비교 |
| Worker | `1..2` | `2..8` scale-out 범위 |
| notification-worker | `0` | notification attempt 비동기 처리 |
| DLQ replayer | `1` | 유지 |
| Prometheus / Grafana | 각 `1` | 유지 |

저사양 결과는 기능 시연과 운영 신호 확인에만 사용합니다. full HA Kafka baseline, PostgreSQL failover, RPO/RTO, 대규모 KEDA 결과를 대체하지 않습니다.

## Overlay 경계

- 로컬 kind: `k8s/gitops/overlays/demo-lite`
  - application image `messaging-portfolio:local`
  - Secret의 v2 gate `false`
  - quick start가 migration과 Worker rollout 뒤 API gate를 `true`로 전환
- 공개 k3s: `k8s/gitops/overlays/demo-lite-k3s`
  - GHCR immutable SHA image
  - Secret의 기본 gate `false`
  - API container에만 gate `true`
  - migration wave `-2` → Worker wave `-1` → API wave `0`

공개 k3s overlay의 image tag와 `APP_VERSION`은 CI validation에 성공한 `demo-dev` SHA로 함께 갱신합니다. CI는 image를 먼저 검증하고 `demo-dev`의 tested tree와 해당 tag를 단일 release commit으로 `demo-lite`에 기록합니다. Argo CD는 새 manifest와 구 image가 섞인 중간 revision을 보지 않습니다.

## 로컬 실행

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_lite.ps1
```

실행 순서:

1. local image build와 kind load
2. chart-managed PostgreSQL credential로 단일 PostgreSQL/Pgpool 설치
3. Kafka와 topic bootstrap 완료 대기
4. schema migration Job 완료 대기
5. Worker rollout 완료 대기
6. API의 `GENERIC_EVENTS_V2_ENABLED=true` 전환
7. API readiness와 smoke test 확인

## 공개 k3s 운영

- 개발 브랜치: `demo-dev`
- 배포 브랜치: `demo-lite`
- Argo CD path: `k8s/gitops/overlays/demo-lite-k3s`
- bootstrap: `scripts/bootstrap_argocd_lite_k3s.sh`
- 수동 설치 진단: `scripts/deploy_lite_k3s.sh`
- profile 확인/전환: `scripts/reconcile_profile_k3s.sh`

`demo-dev` push는 먼저 공통 CI validation을 실행합니다. 성공한 revision만 candidate image build, non-root 실행 확인, digest promotion, `demo-lite` 단일 release commit 단계로 진행합니다. `demo-lite`는 사람의 개발 commit이나 branch merge 경로로 갱신하지 않습니다.

## 배포 완료 판정

- GitHub Actions `validate`와 `publish-demo-lite-image` 성공
- Argo CD `Synced / Healthy`
- API/Worker image가 동일 immutable SHA
- Demo UI `ver. 2.2.0`
- readiness `app_version`이 배포 SHA와 일치
- generic event POST `202`
- Kafka broker `1`, Worker availability `1`, consumer lag 최종 `0`
- PostgreSQL 저장 결과와 same-stream ordering 확인
