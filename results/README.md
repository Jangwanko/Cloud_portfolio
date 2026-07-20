# Validation Evidence

이 디렉터리는 문서에 인용하는 로컬 검증 원본을 보관합니다. `latest` 파일은 마지막으로 완료된 실행의 원본이며, 새 측정이 기존 안정 기준선을 자동으로 대체한다는 뜻은 아닙니다.

## Tracked Evidence

- `kafka-performance/latest.txt`: Kafka 성능 suite의 마지막 완료 출력
- `kafka-performance/failed-YYYYMMDD-HHMMSS.txt`: 실패한 suite의 부분 출력. `latest.txt`를 덮어쓰지 않으며 기본 Git 추적 제외
- `ordering-failure/latest.json`: ordering / failure injection suite의 마지막 완료 결과
- `postgres-restore/latest.json`: host logical dump를 disposable database에 복원한 마지막 정합성 검증 원본
- 그 밖의 날짜별·중간 산출물: 로컬 보관, 기본 Git 추적 제외

PostgreSQL restore 원본은 dump 파일 자체를 Git에 넣지 않습니다. `latest.json`에 dump size/hash, 검증 script hash, source/restore 비교값과 한계를 남깁니다.

## Latest Completed Suite — 2026-07-21

- 실행: `2026-07-21T03:37:34+09:00`, `dev-kafka` revision `1439be1`, API/Worker image `d31ac14`
- 조건: generic v2, 100 VU / 30s, 한 hot stream, Kafka 한 partition 집중
- intake: total HTTP `25,382`, event `202` `25,378`, error `0.00%`, avg `67.83ms`, p95 `123.96ms`, p99 `153.10ms`
- ordering: 100 events, `stream_seq 1..100`, pass in `7.93s`
- status sample: 50/50 persisted, status-observed avg `79.96ms`, p95 `81.28ms`, max `2384.10ms`; polling/network 포함
- lag: message-worker peak `24,504`, notification-worker peak `6`, main drain `751.76s`, final both `0`
- HPA follow-up: suite의 main drain 뒤 생성된 추가 message-worker lag `4,962`, 수동 관측 약 `160s` 내 `0`
- 판정: 첫 generic v2 후보, stable baseline 미채택; fresh cluster clean state를 포함한 단일 실행으로 인과관계 판단 제외

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
