# Validation Evidence

이 디렉터리는 문서에 인용하는 로컬 검증 원본을 보관합니다. `latest` 파일은 마지막으로 완료된 실행의 원본이며, 새 측정이 기존 안정 기준선을 자동으로 대체한다는 뜻은 아닙니다.

## Tracked Evidence

- `kafka-performance/latest.txt`: Kafka 성능 suite의 마지막 완료 출력
- `kafka-performance/worker-ab-fixed.txt`: 64-stream, Worker fixed `2` arm 전체 출력
- `kafka-performance/worker-ab-keda.txt`: 64-stream, Worker KEDA `2→8` arm 전체 출력
- `kafka-performance/failed-YYYYMMDD-HHMMSS.txt`: 실패한 suite의 부분 출력. `latest.txt`를 덮어쓰지 않으며 기본 Git 추적 제외
- `ordering-failure/latest.json`: ordering / failure injection suite의 마지막 완료 결과
- `postgres-restore/latest.json`: host logical dump를 disposable database에 복원한 마지막 정합성 검증 원본
- `postgres-recovery/latest.json`: PostgreSQL 전체 재시작 뒤 sync 설정, cache fallback, outage recovery의 마지막 tracked structured summary
- 그 밖의 날짜별·중간 산출물: 로컬 보관, 기본 Git 추적 제외

fixed Worker/KEDA A/B처럼 두 원본을 함께 보존해야 하는 실행은 `run_kafka_performance_suite.ps1 -ResultFileName <name>.txt`를 사용합니다. 각 파일에 `k6_stream_count`, `worker_scaling_mode`, `fixed_worker_replicas`, source revision과 dirty 여부를 남기고, 조건이 다른 파일을 하나의 baseline으로 합치지 않습니다.

PostgreSQL restore 원본은 dump 파일 자체를 Git에 넣지 않습니다. `latest.json`에 dump size/hash, 검증 script hash, source/restore 비교값과 한계를 남깁니다.

PostgreSQL recovery JSON은 실행 시각, source/script hash, 관측값과 한계를 구조화한 추적 요약입니다. 전체 raw terminal transcript는 보관하지 않았으므로 JSON 자체를 원시 출력으로 해석하지 않습니다.

## Latest Completed Kafka Performance Suite — 2026-07-21

- 실행: `2026-07-21T08:59:26+09:00`, `dev-kafka` HEAD `d3fd475`, dirty worktree, local API/Worker image `perf-v16`
- 조건: generic v2, 100 VU / 30s, 한 hot stream, API min `6`, ingress producer `1`/pod, Worker min `2`
- 초기화: local DB event state와 6개 Kafka topic 삭제·재생성, Kafka delayed deletion quiet period `75s`
- steady state: API `6/6`, Worker `2/2`, 모든 API cache hydrated, fresh message/notification lag `0`
- intake: total HTTP `29,612`, event `202` `29,608`, error `0.00%`, avg `50.21ms`, p95 `90.51ms`, p99 `134.78ms`
- ordering: 100 events, `stream_seq 1..100`, pass in `8.06s`
- status sample: 50/50 persisted, status-observed avg `40.21ms`, p95 `43.03ms`, max `459.12ms`; polling/network 포함
- lag: message-worker peak `28,488`, notification-worker peak `20`, main drain `511.88s`, final both `0`
- HPA follow-up: message-worker peak `8,847`, notification-worker peak `28`, `172.80s` 뒤 모두 `0`
- 3회 반복 평균: event `29,168`, avg `51.81ms`, p95 `101.27ms`, p99 `140.59ms`, main drain `508.58s`
- 판정: 첫 v2 후보보다 세 번 모두 event/avg/p95/p99 개선; historical stable legacy baseline과 registry/GitOps 배포 검증에는 미달해 stable baseline 미채택
- 원본 범위: `latest.txt`는 3회차 전체 출력. 1·2회차 완료 수치는 `docs/TEST_RESULTS.md`에 기록했으며 별도 전체 raw 파일은 보존하지 않음

## Multi-stream Worker A/B Candidate — 2026-07-21

- 공통 조건: dirty source image `perf-v17`, API `6`, 100 VU / 30s, 64 streams, clean DB/topic, 시작 lag `0`
- fixed `2`: event `22,125`, p95 `169.24ms`, peak message/notification lag `21,170`/`45`, all-pipeline drain `301.42s`
- KEDA `2→8`: event `20,499`, p95 `212.60ms`, peak message/notification lag `18,950`/`11,536`, all-pipeline drain `261.17s`, final Worker `8`
- 판정: drain `13.35%` 감소와 notification backlog 이동 확인. KEDA arm intake 악화와 단일 실행 조건 때문에 stable baseline 미채택

## Latest PostgreSQL Restore Drill — 2026-07-21

- 실행: `2026-07-21T04:41:27+09:00`, `dev-kafka` base revision `1439be1`, 검증 script SHA256 기록
- 원본: host workspace의 Git-ignored logical dump `39,433,414` bytes, SHA256 `26019e88...93c4`
- 복원 대상: 같은 local PostgreSQL HA cluster의 disposable database
- 결과: Alembic `0008`, 10개 table count, generic v2 row `33,840`, max message id `33,840`, max stream sequence `25,378` 원본 일치
- 한계: 전체 host/cluster loss, object storage 사본, 정기 integrity check, 자동 RPO/RTO 측정 미검증

## Latest PostgreSQL Restart Recovery Drill — 2026-07-21

- 실행: `2026-07-21T05:01:59+09:00`, `dev-kafka` source revision `fde9a24`, 관련 script SHA256 기록
- sync recovery: 모든 StatefulSet ordinal에 `synchronous_commit=on`, `ANY 1` 지속 적용; primary `postgresql-0`, sync/quorum standby `2`
- cache fallback: `45.390s`, fresh cache age `0.112s`, DB-down degraded cache age `11.462s`, scale `3→0→3` 뒤 ready
- DB outage: `43.008s`, DB down 중 event 수락과 복구 뒤 persistence 확인
- 최종 상태: PostgreSQL `3/3`, Pgpool `2/2`, API ready, message/notification lag `0`, backup PVC `Bound`
- 한계: primary promotion과 node/AZ 장애 미검증; 상세값은 `postgres-recovery/latest.json`

## Interpretation Rules

- 2026-06 성능 출력의 event 응답 `200`: HTTP `202 Accepted` 계약을 코드에 명시하기 전 수집한 역사적 증거
- 2026-06 request shape는 legacy/order contract이며 generic v2 route·serialization·validation 성능 증거가 아님
- 현재 계약 검증: 2026-07-21 새 빌드에서 OpenAPI `2.0.0`과 event `202` 재확인
- 2026-06 `accepted-to-persisted`: API 수락 시각과 PostgreSQL row의 `created_at` 또는 row 조회 가능 시점을 비교한 historical proxy
- current PowerShell `accepted_to_status_observed_ms`: client가 `persisted` status를 본 시각까지, polling/network 포함
- current Worker histogram: `commit()` 반환 직후 `persisted_at`까지; 2026-07-21 query `60s`는 최대 finite bucket 경계 포화로 exact p95 해석 제외
- DB commit timestamp: tracked 2026-06 원본에서 직접 측정하지 않음
- DLQ API sample: append-only Kafka DLQ log의 최근 표본
- `oldest_sample_age_seconds`: 조회 표본의 가장 오래된 record age
- unresolved DLQ depth / current incident backlog: 별도 상태 모델 없이는 이 파일과 DLQ sample로 산출 불가
- Worker KEDA 효과: consumer lag peak, Worker commit-observed latency, backlog drain time으로 관찰. 2026-06 비교에서는 historical row-visible proxy만 사용 가능
- fixed replica 대 KEDA 직접 비교: 동일 조건 A/B 실험 전까지 수치 주장 제외

## Update Checklist

1. 실행 시각, commit, branch, cluster profile, workload 조건 기록
2. 원본 `latest` 파일 갱신
3. `docs/TEST_RESULTS.md`에 최신 결과와 안정 기준선 채택 여부 분리 기록
4. HTTP status, proxy 정의, 결측값과 제한 사항 명시
5. Redis queue-first 결과와 Kafka event-stream 결과 분리
