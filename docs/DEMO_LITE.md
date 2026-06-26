# Demo Lite

- 목적: 2코어급 서버에서 API -> Kafka -> Worker -> DB 흐름 확인
- 성격: 저사양 시연 profile
- 위치: `demo-lite` 브랜치 전용
- master 기준: full local demo와 GitOps 문서 유지
- 성능 해석: full HA Kafka baseline 대체 제외

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
