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
- KEDA Kafka trigger: topic `message-ingress`, consumer group `message-worker`, lag threshold `400`, min replicas `2`, max replicas `8`.
- API HPA: CPU target `65%`, min replicas `3`, max replicas `8`.
- Pgpool SPOF is reduced, not fully eliminated: Pgpool has `2` replicas and PDB `minAvailable=1`, but local kind remains a single-node demo environment.
- Unit tests: `.venv\Scripts\python.exe -m pytest -q` => `60 passed` at the last documented verification.
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

- README는 포트폴리오 첫 화면 역할로 유지합니다. 모든 세부 내용을 README에 넣지 말고, 핵심 요약 / 데모 진입 / 대표 검증 결과 / 문서 지도만 남깁니다.
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
