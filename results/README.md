# Validation Evidence

이 디렉터리는 문서에 인용하는 로컬 검증 원본을 보관합니다. `latest` 파일은 마지막으로 완료된 실행의 원본이며, 새 측정이 기존 안정 기준선을 자동으로 대체한다는 뜻은 아닙니다.

## Tracked Evidence

- `kafka-performance/latest.txt`: Kafka 성능 suite의 마지막 완료 출력
- `kafka-performance/worker-ab-fixed.txt`: 2026-07-21 historical 64-stream Worker fixed `2` arm 전체 출력
- `kafka-performance/worker-ab-keda.txt`: 2026-07-21 historical 64-stream Worker KEDA `2→8` arm 전체 출력
- `kafka-performance/failed-YYYYMMDD-HHMMSS.txt`: 실패한 suite의 부분 출력. `latest.txt`를 덮어쓰지 않으며 기본 Git 추적 제외
- `ordering-failure/latest.json`: ordering / failure injection suite의 마지막 완료 결과
- `postgres-restore/latest.json`: host logical dump를 disposable database에 복원한 마지막 정합성 검증 원본
- `postgres-recovery/latest.json`: 2026-07-21 PostgreSQL 전체 재시작 뒤 sync 설정, 당시 cache fallback, outage recovery의 마지막 tracked structured summary
- 그 밖의 날짜별·중간 산출물: 로컬 보관, 기본 Git 추적 제외

fixed Worker/KEDA A/B처럼 두 원본을 함께 보존해야 하는 실행은 `run_kafka_performance_suite.ps1 -ResultFileName <name>.txt`를 사용합니다. 각 파일에 `k6_stream_count`, `worker_scaling_mode`, `fixed_worker_replicas`, source revision과 dirty 여부를 남기고, 조건이 다른 파일을 하나의 baseline으로 합치지 않습니다.

PostgreSQL restore 원본은 dump 파일 자체를 Git에 넣지 않습니다. `latest.json`에 dump size/hash, 검증 script hash, source/restore 비교값과 한계를 남깁니다.

PostgreSQL recovery JSON은 실행 시각, source/script hash, 관측값과 한계를 구조화한 추적 요약입니다. 전체 raw terminal transcript는 보관하지 않았으므로 JSON 자체를 원시 출력으로 해석하지 않습니다.

## Latest Completed Kafka Performance Suite — 2026-08-05

- 실행: `2026-08-05T22:08:55+09:00`, `dev-kafka` local merge HEAD `100efd4`, dirty worktree
- image: API·core Worker·notification Worker 모두 `messaging-portfolio:v2-core-cleanup`, API `2.1.0`
- 조건: generic v2, 100 VU / 30s, 64 streams, core KEDA `2→4`, notification KEDA `1→2`
- 초기화: local DB event state와 현재 active Kafka topic 재생성, 시작 message/notification lag `0`
- intake: total HTTP `28,672`, event `202` `28,605`, error `0.00%`, avg `53.67ms`, p95 `107.41ms`, p99 `157.78ms`
- ordering: 100 events, `stream_seq 1..100`, pass in `7.92s`
- main load: message-worker peak `25,905`, notification-worker peak `1,141`, all-pipeline drain `321.29s`, final both `0`
- HPA follow-up: message-worker peak `9,734`, notification-worker peak `1,026`, all-pipeline drain `140.52s`, final both `0`
- 판정: local source candidate의 완결성·drain 증거. 앞선 두 KEDA 반복보다 intake와 drain이 악화돼 stable baseline과 KEDA 성능 향상 주장 제외
- 원본 범위: `latest.txt`는 마지막 완료 실행 전체 출력. hot-stream 3회와 fixed/KEDA 중간 실행 수치는 `docs/TEST_RESULTS.md`에 조건·판정과 함께 기록

같은 source의 hot single-stream 3회 평균은 event `33,201`, avg `39.61ms`, p95 `76.57ms`, p99 `111.49ms`, main drain `364.62s`입니다. 제거 전 v2 recovery 후보보다 event `13.83%` 증가, p95 `24.39%` 감소, drain `28.31%` 감소했습니다. local dirty image 조건이라 stable baseline으로 승격하지 않습니다.

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
- current source 계약 검증: 2026-08-05 local image에서 OpenAPI/API `2.1.0`, event `202`, PostgreSQL read model 재확인
- 2026-06 `accepted-to-persisted`: API 수락 시각과 PostgreSQL row의 `created_at` 또는 row 조회 가능 시점을 비교한 historical proxy
- current PowerShell `accepted_to_status_observed_ms`: client가 `persisted` status를 본 시각까지, polling/network 포함
- current Worker histogram: `commit()` 반환 직후 `persisted_at`까지; 2026-07-21 query `60s`는 최대 finite bucket 경계 포화로 exact p95 해석 제외
- DB commit timestamp: tracked 2026-06 원본에서 직접 측정하지 않음
- DLQ API sample: append-only Kafka DLQ log의 최근 표본
- `oldest_sample_age_seconds`: 조회 표본의 가장 오래된 record age
- unresolved DLQ depth / current incident backlog: 별도 상태 모델 없이는 이 파일과 DLQ sample로 산출 불가
- Worker KEDA 효과: consumer lag peak, Worker commit-observed latency, backlog drain time으로 관찰. 2026-06 비교에서는 historical row-visible proxy만 사용 가능
- fixed replica 대 KEDA 직접 비교: current fixed 1회와 KEDA 3회의 drain 범위가 겹치므로 성능 우위 주장 제외

## Update Checklist

1. 실행 시각, commit, branch, cluster profile, workload 조건 기록
2. 원본 `latest` 파일 갱신
3. `docs/TEST_RESULTS.md`에 최신 결과와 안정 기준선 채택 여부 분리 기록
4. HTTP status, proxy 정의, 결측값과 제한 사항 명시
5. Redis queue-first 결과와 Kafka event-stream 결과 분리
