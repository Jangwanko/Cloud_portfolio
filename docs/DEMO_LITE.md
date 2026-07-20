# Demo Lite

- 목적: 2코어급 서버에서 API -> Kafka -> Worker -> DB 흐름 확인
- 성격: 저사양 시연 profile
- 위치: `demo-lite` 브랜치 전용
- master 기준: full local demo와 GitOps 문서 유지
- 성능 해석: full HA Kafka baseline 대체 제외
- 서비스 경계: reference event를 Kafka에 append하고 비동기 저장하는 구간 표시
- scenario: 주문·결제 lifecycle을 범용 처리 흐름의 예제로 사용

## 현재 브랜치 기준

- `master`: 최종 병합 / 보관 브랜치
- `master`의 즉시 실행 경로: `scripts/quick_start_all.ps1`
- `master`의 GitOps 기준: `k8s/gitops/overlays/local-ha`
- `master`에 없는 항목: demo-lite 배포 스크립트, demo-lite Argo CD bootstrap 스크립트, demo-lite k3s overlay

## demo-lite 브랜치 기준

- 대상 서버: Ubuntu, 2코어급 CPU, 8GB memory, 작은 disk
- 배포 방식: k3s + Argo CD + registry image
- 목적: 작은 서버에서 같은 event-driven 흐름 표시
- 제외: 3-broker Kafka HA, PostgreSQL HA failover, full performance baseline 증명

## 문서 해석

- README의 `Local Demo`: 현재 브랜치에서 바로 실행 가능한 경로
- `docs/GITOPS.md`: `master` / local-ha GitOps 기준
- `docs/TEST_RESULTS.md`: full HA 검증 결과와 Kafka baseline 기준
- demo-lite 관련 patch note: 별도 브랜치에서 진행한 저사양 데모 작업 기록
- `master` source Demo UI: `2.0.0`, generic `/v2/streams/{stream_id}/events`와 Reliable Event Processing System 정체성
- public demo-lite deployment: title `Post-Order Event Console`, Demo UI `1.4.1`, API `1.0.0`, image `e481a21`, generic v2 route 없음, event response `200`, branch/image 전용 상태
- 이번 `master` `2.0.0` 변경: public demo-lite 미배포
- README/Guide의 `2.0.0` 동작: demo-lite branch에 같은 기능과 image tag가 반영됐는지 확인한 뒤 적용
- 공개 URL 확인: UI `ver.` badge와 readiness `app_version`을 각각 기록

## 실행 전 확인

```powershell
git branch --show-current
Test-Path scripts/quick_start_all.ps1
Test-Path k8s/gitops/overlays/local-ha
```

demo-lite 서버 작업 전 확인:

```bash
git branch --show-current
git status --short
```

- 필요한 배포 스크립트와 overlay는 `demo-lite` 브랜치에서 확인
- `master`에서 해당 파일이 없으면 정상
