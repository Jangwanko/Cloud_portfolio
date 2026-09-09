# Isolated Argo CD Release Failure Lab

## 2026-09-05 실제 실행 결과

Run `20260905T133642Z`: **PASS**. Baseline → migration failure → rollback → forward recovery
각 단계 10개, 총 40개 v2 event의 `202`, request status, PostgreSQL 저장과 순서 `1..40`,
구조화 payload/metadata, notification attempt 40개를 확인했습니다. 누락·중복은 0입니다.
실패 시 Worker/API Deployment UID·generation·template이 유지됐고, DB는 `0008`과 marker table 부재를 유지했습니다.
수정 배포 후 `0009_release_lab`과 marker row 1개를 확인했습니다.

Rollback 요청부터 canary 검증까지 `14.641s`, 수정 배포 요청부터 검증까지 `22.125s`였습니다.
이는 격리된 단일 실행의 작업 소요시간이며 downtime·production RTO 측정값이 아닙니다.
기존 workload/PVC/Application spec 비교는 일치했고 실험 리소스 정리는 완료했습니다.
추가된 실험 검증을 포함한 전체 local suite는 `640 passed`입니다.

근거: [실행 summary](../results/release-failure/20260905T133642Z/summary.json),
[28개 관측 지점](../results/release-failure/20260905T133642Z/evidence.compact.json),
[실패 SQL 로그](../results/release-failure/20260905T133642Z/migration-failure-log.json).
초기 환경 구성 실패 3회와 raw 보존 범위는 [증거 안내](../results/release-failure/README.md)에 기록했습니다.

이력서에는 다음과 같이 표현할 수 있습니다.

> 격리된 Kubernetes 환경에서 Argo CD migration 실패를 주입해 후속 Worker/API 배포 차단과
> PostgreSQL DDL rollback을 검증했습니다. 이전 release 복원 및 수정 migration 재배포 후
> 이벤트 40개의 저장·순서·데이터 일치를 확인하고 재현 스크립트와 운영 증거를 문서화했습니다.

Kubernetes·DevOps 지원용 배포 실패·복구 증거를 수집하는 로컬 실험입니다.
실제 Argo CD controller, Kafka, PostgreSQL, 게시된 API·Worker image를 사용합니다.

## 검증 범위

| 단계 | Desired fixture | 검증 대상 |
| --- | --- | --- |
| Baseline | Helm `0.1.0`, DB `0008` | 실제 migration, Worker/API 준비, v2 event 10개 저장 |
| Migration failure | Helm `0.2.0`, 의도적으로 실패하는 `0009` | transactional DDL rollback, Worker/API wave 차단, 기존 release event 10개 추가 저장 |
| Rollback | Helm `0.1.0` | DB `0008` 유지, Argo `Synced / Healthy`, event 10개 추가 저장 |
| Forward recovery | Helm `0.3.0`, 수정된 additive `0009` | migration → Worker → API, marker table/row, event 10개 추가 저장 |

실패 fixture의 `0009_release_lab`은 전용 DB에 `release_lab_marker` table과 row를
생성한 뒤 `SELECT 1 / 0`으로 실패합니다. 실패 이후 Alembic version은 `0008`,
marker table은 부재여야 합니다. 정상 fixture는 같은 additive revision에서 해당
실패 statement만 제거합니다. 실제 제품 migration 파일은 변경하지 않습니다.

Worker/API 배포 차단은 Deployment UID·generation·pod template hash·release label
일치와 ready/available replica로 확인합니다. Canary는 HTTP `202`, request status
`persisted`, PostgreSQL row의 request ID·sequence·event type·payload·metadata,
notification attempt 수를 함께 확인합니다.

## 실행과 격리

```powershell
.venv\Scripts\python.exe scripts\release_failure_lab.py --context kind-messaging-ha --run
```

- `--run` 없는 호출: cluster write 없음
- 실행 대상: 명시적 `kind-*` context, 새 `release-lab-*` namespace
- Argo 범위: 실험명과 일치하는 Application·AppProject, 해당 namespace만 destination 허용
- workload: 실험용 PostgreSQL `1`, Kafka `1`, API `1`, core/notification Worker 각 `1`
- storage: 실험 전용 `emptyDir`; 기존 PVC·DB·topic 참조 없음
- secret: 매 실행 생성, namespace 내부 전달, chart·증거 파일에 값 기록 제외
- source: namespace 내부 HTTP Helm fixture repository, version별 chart SHA-256 보존
- image: 기본값은 게시 image `54ee42a2fb29`의 digest; 현재 checkout의 관련 source와 비교
- cleanup: 이 실행이 생성한 resource의 UID·소유 label 검증 후 Application·AppProject·namespace 정리
- 기존 `messaging-app`: workload/PVC/Application spec hash를 전후 비교

실험은 기존 kind node와 control plane의 자원을 공유합니다. Namespace는 관리 범위를
분리하며 네트워크 보안 격리를 증명하지 않습니다. 동시 작업과 host 재기동은 피합니다.

## 증거와 해석

`results/release-failure/<UTC run ID>/`에 단계별 상태, canary 결과, migration log,
최종 summary와 파일 hash를 보존합니다. 초기화 실패는 `FAIL`로 남기고 성공 원본으로
대체하지 않습니다. 보고서의 소요시간은 operator의 복구 요청부터 canary 검증 완료까지로,
서비스 downtime 또는 production RTO와 구분합니다.

이 실험은 Argo CD release ordering과 transactional schema failure를 검증합니다.
Git push → CI build/publication → Git manifest 변경의 전체 전달 경로, 서로 다른 제품
image 사이의 호환성, node/AZ 장애, DB primary promotion, 지속 부하 성능은 별도 범위입니다.
Rollback 시 DB는 아직 `0008`이며, 이미 commit된 schema를 downgrade하는 실험은 포함하지
않습니다. 정상 복구 후 네 구간의 저부하 canary 총 40개를 확인하며, 연속 무중단을 주장하지
않습니다.

## 근거 문서

- [Argo CD sync waves](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/)
- [Argo CD Helm source](https://argo-cd.readthedocs.io/en/stable/user-guide/helm/)
- [kubectl을 통한 Argo sync 요청](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-kubectl/)
