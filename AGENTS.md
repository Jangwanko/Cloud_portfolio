# Project Context for Codex

이 파일은 새 Codex 세션이 프로젝트를 다시 처음부터 분석하지 않도록 현재 기준 사실을 고정하는 문서입니다. 작업을 시작하기 전에 이 파일을 먼저 읽고, 세부 수치가 필요할 때만 README와 docs를 확인합니다.

## Current Project Identity

- 현재 최종 포트폴리오는 쇼핑몰 주문 이후 이벤트를 Kafka로 받아 저장, 분류, 알림, 장애 격리, 재처리까지 처리하는 event-driven order pipeline입니다.
- 사용자는 결제 완료와 주문 완료 응답까지만 직접 확인하며, Kafka 처리 상태는 사용자 화면에 노출하지 않습니다.
- Kafka / Worker / DLQ / materialized cache / observability는 주문 이후 운영 이벤트 처리와 장애 대응을 위한 내부 경로입니다.
- 현재 최종 브랜치는 `master`입니다. `dev-kafka`는 Kafka 전환 작업 브랜치였고, 같은 최종 commit이 `master`에 병합되어 있습니다.
- 브랜치 운영 기준: `master`는 최종 병합 / 보관 장소이며, 실제 개발과 문서 개편의 기본 작업 브랜치는 `dev-kafka`입니다. 작업 시작 전 현재 브랜치를 확인하고, 개발성 변경은 `dev-kafka`에서 진행합니다.
- API는 PostgreSQL에 먼저 쓰지 않고 Kafka `message-ingress` topic에 append한 뒤 `202 Accepted`를 반환합니다.
- Worker consumer group `message-worker`가 Kafka partition을 consume하고 PostgreSQL HA에 비동기로 persistence합니다.
- 실패 event는 retry 후 `message-ingress-dlq`에 격리하며, replay guard와 DLQ API가 있습니다.
- 같은 stream ordering은 global ordering이 아니라 `stream_id` 기준 Kafka partition boundary와 Worker inline retry로 보장합니다.
- PostgreSQL은 최종 durable source of truth이며, DB commit 이후 snapshot을 compacted topic으로 publish합니다.
- API는 `message-request-status`, `message-snapshots`, `stream-snapshots` compacted topic을 consume해 local materialized cache를 유지합니다.
- Read fallback은 Kafka ingress event를 직접 읽지 않습니다. Worker가 PostgreSQL commit 이후 publish한 DB snapshot만 cache 원본으로 사용합니다.
- `X-Idempotency-Key`는 API hot path에서 PostgreSQL claim을 만들지 않고 Kafka payload에 포함됩니다. 최종 idempotency / deduplication은 Worker persistence 단계의 PostgreSQL state에서 처리합니다.
- Worker autoscaling은 CPU가 아니라 KEDA Kafka scaler의 consumer lag 기준입니다. API autoscaling은 CPU HPA입니다.

## Do Not Mix Redis and Kafka Results

Redis queue-first 결과와 Kafka event stream 결과를 섞지 않습니다.

Redis queue-first 성능 개선 수치:

| 단계 | 요청 수 | 평균 응답 | p95 |
| --- | ---: | ---: | ---: |
| 초기 기준 | `5,434` | `3,660ms` | `8,175ms` |
| pgpool / DB pool 조정 후 | `11,314` | `1,519ms` | `3,333ms` |
| KEDA queue depth 적용 후 | `19,528` | `811ms` | `1,954ms` |

이 수치는 Redis 기반 구조에서 CPU HPA / queue depth KEDA / hot path 튜닝 효과를 설명할 때만 사용합니다.

Kafka event stream 성능 기준선:

| 실험 | 요청 수 | 오류율 | 평균 | p95 | p99 | Worker |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1차 Kafka baseline | `31,710` | `0.00%` | `44.04ms` | `86.95ms` | `113.78ms` | KEDA 최종 `8` |
| 2차 Kafka baseline | `31,676` | `0.00%` | `44.13ms` | `80.65ms` | `103.57ms` | KEDA 최종 `4` |

Kafka 1차/2차 비교는 Worker scaling ON/OFF 비교가 아닙니다. Pgpool HA, pool 튜닝, Worker inline retry, stream ordering 보강 후에도 Kafka append-first intake baseline이 유지되는지 확인한 결과입니다.

현재 Kafka 문서에는 Worker fixed replica와 KEDA scale-out을 직접 비교한 수치가 없습니다. 이 비교가 필요하면 Worker를 고정한 실험과 KEDA 활성 실험을 같은 조건에서 별도로 실행하고, API request count보다 consumer lag, accepted-to-persisted lag, backlog drain time을 함께 비교해야 합니다.

## Current Kafka Validation Summary

- Kafka broker: 3-broker KRaft StatefulSet, `3/3 ready`.
- Topics: `message-ingress`, `message-ingress-dlq`, `message-request-status`, `message-snapshots`, `stream-snapshots`.
- Topic settings: partitions `8`, replication factor `3`, `min.insync.replicas=2`; snapshot topics use `cleanup.policy=compact`.
- Same stream ordering: 100 events persisted as `stream_seq 1..100`.
- Latest Kafka baseline: 100 VU / 30s, `31,676` requests, error `0.00%`, p95 `80.65ms`, p99 `103.57ms`.
- Accepted-to-persisted latest p95: `7.67ms`.
- 2026-06-09 k6 rerun: 100 VU / 30s, `34,284` requests, error `0.00%`, avg `36.86ms`, p95 `66.06ms`, p99 `104.99ms`; same-stream ordering revalidated with `stream_id=30`, 100 events, `stream_seq 1..100`, ordering `pass`; async persistence sample used `stream_id=31`, 50 events persisted; Worker consumer lag reached `36394` and drained to `0` after about 14 minutes. Treat this as an intake-vs-persistence capacity signal, not a direct replacement for the stable 2nd baseline.
- Worker success path transaction tuning is applied after that rerun: message persistence and request status update share one PostgreSQL transaction; Kafka status/snapshot publish and notification enqueue happen after DB commit.
- Notification attempts are decoupled from core persistence through Kafka `message-notifications` and a separate `notification-worker`.
- Post-tuning performance suite at `2026-06-09T02:17:11+09:00`: `28,839` requests, error `0.00%`, avg `53.47ms`, p95 `108.68ms`, p99 `134.53ms`; same-stream ordering revalidated with `stream_id=34`, 100 events, `stream_seq 1..100`, ordering `pass`; async persistence sample used `stream_id=35`, accepted-to-persisted p95 `8.08ms`; Worker consumer lag reached `29204` and drained to `0` after about 10 minutes. Treat this as persistence-path improvement but not a stable intake baseline replacement because API intake throughput and p95 worsened.
- Notification path split suite at `2026-06-18T03:29:47+09:00`: `27,795` requests, error `0.00%`, avg `57.64ms`, p95 `119.28ms`, p99 `150.60ms`; same-stream ordering revalidated with `stream_id=38`, 100 events, `stream_seq 1..100`, ordering `pass`; async persistence sample used `stream_id=39`, accepted-to-persisted p95 `22.13ms`; Worker consumer lag drained to `0` after about 16 minutes; notification-worker consumer lag was `0`. Treat this as operational-boundary improvement, not a performance improvement.
- Ordering / failure injection validation: `single_no_failure`, `multi_no_failure`, `single_db_failure`, `multi_db_failure` all passed on 2026-06-08 with accepted=persisted, missing `0`, duplicate `0`, mixed payload `0`, DLQ `0`.
- Ordering / failure injection payloads: stream A uses `A001..A100`, stream B uses `B001..B100`, stream C uses `C001..C100`.
- Ordering / failure injection verifies final persistence by querying PostgreSQL `messages` rows from inside the API pod, not by trusting Kafka accept alone.
- Cache fallback validation: fresh read `source=cache`, DB down stale fallback `source=cache`, `degraded=true`.
- Local HA topology: Kafka `3`, PostgreSQL `3`, Pgpool `2`, API min `3`, Worker min `2`, DLQ replayer `1`.
- KEDA Kafka trigger: topic `message-ingress`, consumer group `message-worker`, lag threshold `100` for the local demo cluster, min replicas `2`, max replicas `8`.
- API HPA: CPU target `65%`, min replicas `3`, max replicas `8`.
- Pgpool SPOF is reduced, not fully eliminated: Pgpool has `2` replicas and PDB `minAvailable=1`, but local kind remains a single-node demo environment.
- Unit tests: `.venv\Scripts\python.exe -m pytest -q` => `65 passed` at the last documented verification.
- GitOps status at last documented verification: Argo CD `Synced / Healthy`.

## Important Docs

- `README.md`: interview-facing overview.
- `docs/TEST_RESULTS.md`: current validation results and measurement conditions.
- `docs/ARCHITECTURE.md`: Kafka-centered architecture, ordering boundary, autoscaling design.
- `docs/RELIABILITY_POLICY.md`: degraded / critical interpretation.
- `docs/OBSERVABILITY.md`: Grafana / Prometheus operating signals.
- `docs/RUNBOOK.md`: incident response and operational checks.
- `docs/SERVICE_REQUIREMENTS.md`: service assumptions, SLO guardrails, operational purpose.
- `docs/KAFKA_EXPERIMENT.md`: Kafka migration experiment notes.
- `docs/PATCH_NOTES.md`: change history.
- `results/kafka-performance/latest.txt`: most recent local Kafka performance suite output, when present.
- `results/ordering-failure/latest.json`: most recent ordering / failure injection result, when present.
- `k8s/gitops/base/kafka-ha.yaml`: local Kafka KRaft StatefulSet and topic bootstrap.
- `k8s/gitops/base/manifests-ha.yaml`: generated local HA application, observability, HPA/KEDA, alerting manifest.
- `portfolio/api.py`: FastAPI intake, status, DLQ, cache-first read API.
- `worker/main.py`: Kafka consumer, PostgreSQL persistence, inline retry, DLQ movement, snapshot publish.
- `portfolio/materialized_cache.py`: request status and DB snapshot local materialized cache.
- `portfolio/kafka_client.py`: Kafka producer/consumer helpers.

## Common Commands

Run tests:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Check portfolio status:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_portfolio_status.ps1
```

Run Kafka performance suite:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_kafka_performance_suite.ps1
```

Run cache read fallback validation:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test_cache_read_fallback.ps1 -SkipReset
```

Run ordering / failure injection validation:

```powershell
.venv\Scripts\python.exe scripts\ordering_failure_injection.py --scenario all --event-count 100
```

Latest ordering / failure injection result after fixing local client skew:
- `single_no_failure`: 100 accepted / 100 persisted, ordering PASS, `6.125s`.
- `multi_no_failure`: 300 accepted / 300 persisted across A/B/C streams, ordering PASS, `8.438s`.
- `single_db_failure`: 100 accepted / 100 persisted, Pgpool outage `20.828s`, recovery to completion `1.375s`.
- `multi_db_failure`: 300 accepted / 300 persisted, Pgpool outage `21.610s`, recovery to completion `1.218s`.
- Earlier `~210s` ordering/failure injection durations were invalid as performance evidence because Python `urllib` calling `http://localhost` on Windows / Docker Desktop added about 2 seconds of client-side delay per request. The script now defaults to `http://127.0.0.1` with `Host: localhost`.

## Documentation Rules

- git commit, merge, push, PR 생성은 항상 사용자에게 먼저 확인받습니다. 테스트가 통과했더라도 확인 없이 커밋하거나 병합하지 않습니다.
- README는 포트폴리오 첫 화면 역할로 유지합니다. 모든 세부 내용을 README에 넣지 말고, 핵심 요약 / 데모 진입 / 대표 검증 결과 / 문서 지도만 남깁니다.
- README의 기본 설명과 사용법은 외국인 리크루터도 볼 수 있게 한국어와 영어를 함께 사용합니다. 전체 문서를 완전 번역하지는 않더라도, project summary, demo usage, AWS migration blueprint는 영어 문장을 같이 둡니다.
- README에서 자세한 내용을 docs로 넘길 때는 링크만 던지지 않습니다. 각 주제마다 2~4줄 요약, 왜 중요한지 한 문장, 관련 docs 링크를 함께 제공합니다.
- 세부 구현, 실험 과정, 운영 절차, 장애 대응, Terraform AWS migration blueprint는 docs 문서로 분리합니다.
- changelog, patch notes, test results, migration plan처럼 시간 흐름이 중요한 문서는 최신 항목을 위에 둡니다. 과거 기록은 아래쪽 historical section으로 보냅니다.
- `docs/PATCH_NOTES.md`는 최신 변경이 맨 위에 오도록 관리합니다.
- `docs/TEST_RESULTS.md`는 최신 검증 결과를 먼저 보여주고, 과거 baseline은 historical results로 분리합니다.
- `docs/AWS_IAC_PLAN.md`는 현재 AWS migration blueprint를 먼저 설명하고, 구현 단계와 모듈 세부 설명은 뒤에 둡니다.
- `docs/ARCHITECTURE.md`는 현재 최종 Kafka-centered 구조를 먼저 설명하고, 과거 전환 배경은 뒤쪽 또는 별도 문서로 둡니다.
- Terraform 문서는 "AWS에 이미 배포했다"가 아니라 "로컬 검증 구조를 AWS managed architecture로 이전할 수 있게 설계했다"는 migration blueprint 관점으로 씁니다.
- 문서에서 Kafka 최종 구조를 Redis에서 이름만 바꾼 것처럼 쓰지 않습니다.
- Kafka를 Kafka-only라고 과장하지 않습니다. 이 프로젝트는 Kafka-centered 구조이며 PostgreSQL state/read model을 유지합니다.
- Kafka Worker KEDA 효과를 API throughput 증가로 단정하지 않습니다. Kafka에서 Worker scaling 효과는 consumer lag, accepted-to-persisted lag, drain time으로 봅니다.
- Redis 성능 수치는 Redis 프로젝트의 이전 scaling/tuning 성과로만 설명합니다.
- Kafka 성능 수치는 append-first intake baseline과 ordering/recovery validation으로 설명합니다.
- `dev-kafka`를 현재 기본 배포 브랜치처럼 쓰지 않습니다. GitOps 기본 revision은 `master` 기준입니다.
- 문서와 답변에서 "단순히 A가 아니라 B"처럼 AI 말투가 강한 대비 문장을 피합니다. 필요하면 "A까지 포함한다", "B로 이어진다", "A를 바탕으로 B를 처리한다"처럼 자연스럽게 씁니다.

## Demo UI Rules

- 데모 화면은 포트폴리오 시연용입니다. 현업 운영자가 보는 모든 raw id를 전부 노출하기보다, 처음 보는 사람이 Kafka -> Worker -> DB 흐름을 이해할 수 있는 신호를 우선합니다.
- 샘플 예약 버튼의 현재 기준은 `10개`, `100개`, `1000개`입니다.
- `예약 건수`는 전송 시작 후 `남은 예약/전체 예약`으로 표시합니다. API가 Kafka append에 성공하면 줄어듭니다.
- `Kafka 적재`는 API가 `message-ingress` topic append를 성공시킨 수입니다.
- `DB 저장`은 Worker가 PostgreSQL commit까지 완료한 수입니다.
- `총 소요시간`은 전송 시작부터 현재 run의 DB 저장 완료까지 걸린 시간입니다.
- Worker 표시는 `현재 replica/최대 replica` 형식으로 둡니다. 예: `2/8`, `6/8`. 화면에는 `HPA 목표`처럼 여러 의미로 읽히는 표현을 쓰지 않습니다.
- Operations Advisor는 rule-based AX 보조 영역입니다. AI API를 호출하지 않고, 예약 / Kafka 적재 / DB 저장 / DLQ 신호를 정해진 규칙으로 해석합니다.
- AI 연동은 향후 별도 Worker나 operator summary 경로로 넣을 수 있습니다. 핵심 주문 처리와 persistence path에는 넣지 않습니다.
- `RESET DEMO DB`는 로컬 데모 이벤트 DB와 `message-ingress-dlq` topic을 초기화합니다. 실제 운영에서 DLQ 이력을 지우는 절차로 설명하지 않습니다.
- 데모 화면의 기능, 레이아웃, 운영 증거, 표시 문구가 바뀌면 `DEMO_UI_VERSION`과 초기 `ver.` 표시를 함께 올립니다. 버전 숫자는 화면 변경이 클러스터에 반영됐는지 확인하는 증거이므로 사소한 UI 변경이라도 누락하지 않습니다. 가벼운 수정은 세 번째 숫자(patch), 시스템의 동작이나 구조가 바뀌는 변경은 두 번째 숫자(minor), 화면/서비스가 새롭게 리뉴얼되는 수준은 첫 번째 숫자(major)를 올립니다.
- 데모 UI 변경 후에는 README, `docs/DEMO_GUIDE.md`, `docs/OPERATIONS.md`, `docs/PATCH_NOTES.md`의 설명을 함께 맞춥니다.

## GitOps / Deployment Rules

- Argo CD는 코드 변경 자체를 배포하지 않습니다. 컨테이너 안에 들어가는 Python code, HTML, static file 변경은 반드시 registry image build/push와 Git manifest의 image tag 변경으로 이어져야 클러스터에 반영됩니다.
- `messaging-portfolio:local`은 local kind 또는 수동 bootstrap 전용 이미지입니다. Argo CD 자동 배포 경로에서는 GHCR/ECR 같은 registry image와 commit SHA 기반 tag를 사용합니다.
- GitOps 자동 반영은 `git push -> image build/push -> kustomize image tag commit -> Argo CD sync` 순서로 설명합니다. 이 순서를 생략하고 "push하면 바로 반영된다"고 쓰지 않습니다.
- 브랜치별 배포 역할을 섞지 않습니다.
  - `demo-lite`: 2코어 k3s 서버용 축소 데모 브랜치입니다.
  - `dev-kafka`: 실제 개발/검증용 Argo CD 브랜치입니다.
  - `master`: 최종 병합 및 배포 기준 브랜치입니다.
- 특정 브랜치에서 image tag workflow를 추가하거나 수정할 때는 먼저 해당 브랜치의 Argo CD `targetRevision`, overlay path, 실제 배포 클러스터를 확인합니다.
- `demo-lite`의 설정을 `dev-kafka`나 `master`에 그대로 복사하지 않습니다. 공통 원칙만 옮기고, overlay path와 배포 대상에 맞게 조정합니다.
- 배포 자동화 변경 후에는 `kubectl kustomize <overlay>`로 app workloads가 registry image tag로 렌더링되는지 확인합니다.
- GitHub Actions가 image tag commit을 다시 push하는 브랜치는 원격이 자동으로 앞서갈 수 있습니다. push rejected가 나면 먼저 `git pull --rebase origin <branch>`로 Actions commit을 통합하고 다시 push합니다.
- GHCR package가 private이면 클러스터가 image를 pull하지 못합니다. 데모 서버는 public GHCR package를 기본으로 보고, private registry를 쓰는 경우에는 imagePullSecret을 별도로 문서화합니다.

## Demo UI Operating Rules

- 데모 UI는 운영 증거를 과장하지 않습니다. Kafka append와 DB persistence는 다른 단계이므로 항상 별도 카운터로 표시합니다.
- `예약 건수`는 Kafka append 성공 시 감소합니다. `DB 저장`은 Worker가 PostgreSQL commit까지 완료했을 때 증가합니다.
- 일부 이벤트가 전송 실패하거나 DB 저장 확인이 끝나지 않았으면 결과 상태를 `완료`로 표시하지 않습니다. `일부 미확인` 또는 같은 의미의 상태로 닫습니다.
- Operations Advisor는 readiness와 DLQ뿐 아니라 남은 예약, Kafka 적재 수, DB 저장 수의 불일치도 확인해야 합니다. 미확인 이벤트가 남아 있으면 `정상`이 아니라 확인 필요 상태로 표시합니다.
- 운영 링크는 `localhost`를 하드코딩하지 않습니다. 현재 API Base URL 또는 접속 origin을 기준으로 생성합니다.
- batch 전송 로직은 일부 실패 때문에 전체 UI 상태가 무한 `처리 중`에 남지 않도록 종료 상태를 명시적으로 정리합니다.

## Demo Lite Boundary

- `demo-lite`는 저사양 서버에서 API -> Kafka -> Worker -> DB 흐름을 보여주는 profile입니다. HA/failover/성능 baseline 증명으로 설명하지 않습니다.
- `demo-lite` PostgreSQL은 단일 primary 기준입니다. primary 연결 실패는 standby failover를 의미하지 않으며, 단일 primary 복구 대기와 Kafka backlog / Worker retry 관점으로 설명합니다.
- full HA, failover, 성능 baseline은 `local-ha` / full-ha 문서와 테스트 결과에서 설명합니다.
- demo-lite에서 발견한 운영 경험은 문서화하되, master로 옮길 때는 "일반 운영 원칙"과 "저사양 서버 전용 제약"을 분리합니다.

## Change Scope Rules

- `AGENTS.md`는 브랜치별 취향 문서가 아니라 모든 주요 브랜치가 공유하는 운영 기준입니다. `master`, `dev-kafka`, `demo-lite` 중 한 브랜치에서 AGENTS.md를 바꾸면 같은 변경을 나머지 주요 브랜치에도 cherry-pick해 일관성을 유지합니다.
- README와 `docs/` 문서도 브랜치별 임시 메모가 아니라 포트폴리오 설명과 운영 기준을 공유하는 문서입니다. 어느 브랜치에서든 문서를 변경했다면 변경 의도, 적용 범위, demo-lite 전용 여부를 확인하고 `master`, `dev-kafka`, `demo-lite` 중 관련 브랜치에 cherry-pick 또는 동일 패치로 공유합니다.
- 문서 변경을 다른 브랜치에 공유할 때는 설정 값을 그대로 복사하지 않습니다. 공통 설명은 공통 문서에 반영하고, 브랜치 전용 제약은 `docs/DEMO_LITE.md`처럼 대상 문서에 분리해서 씁니다.
- AGENTS.md에 demo-lite 전용 제약을 추가하더라도 전체 운영 규칙과 demo-lite 전용 경계를 분리해서 씁니다. 특정 브랜치만의 임시 상태를 전체 규칙처럼 쓰지 않습니다.
- 사용자가 "demo-lite에서 한 것처럼"이라고 말해도 설정을 그대로 복사하지 않습니다. 먼저 대상 브랜치 역할, Argo CD targetRevision, overlay path, 실제 배포 클러스터를 확인합니다.
- 저사양 편의 방식, 수동 image import, local-only workaround를 GitOps 자동 배포의 기본 방식처럼 설명하지 않습니다.
- 문서화할 때 demo-lite 전용 현상과 전체 시스템 원칙을 분리합니다. 전체 원칙은 `docs/GITOPS.md`, `docs/OPERATIONS.md`, `docs/ARCHITECTURE.md`로 올리고, demo-lite 제약은 `docs/DEMO_LITE.md`에 둡니다.

## Rollback Rules

- 사용자가 "롤백", "실행취소", "이전 상태", "N번 전"이라고 말하면 새로 비슷하게 재코딩하지 않습니다. 먼저 현재 `git status`, 최근 commit, 작업 diff를 확인하고 어느 변경을 되돌릴지 특정합니다.
- uncommitted 변경은 해당 변경 범위만 되돌립니다. unrelated user change는 건드리지 않습니다.
- committed 변경은 대상 commit이 명확할 때 `git revert` 또는 명시된 baseline으로의 선택적 되돌리기를 우선 검토합니다. `git reset --hard`, 전체 `git checkout -- .`, `git clean` 같은 광범위한 삭제성 명령은 사용자가 명확히 승인한 경우에만 씁니다.
- 사용자가 "4번째 패널 전", "5번 전"처럼 UI 기준을 말하면, 최근 patch notes / commit log / diff에서 그 기준점을 먼저 찾아 설명한 뒤 되돌립니다.
- rollback 요청 중에는 기능 개선을 함께 섞지 않습니다. 요청한 상태로 되돌린 뒤 별도 수정이 필요하면 그 다음 단계에서 처리합니다.
