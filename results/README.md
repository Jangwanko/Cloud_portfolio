# Validation Evidence

이 디렉터리는 문서에 인용하는 로컬 검증 원본을 보관합니다. `latest` 파일은 마지막으로 완료된 실행의 원본이며, 새 측정이 기존 안정 기준선을 자동으로 대체한다는 뜻은 아닙니다.

## Tracked Evidence

- `kafka-performance/latest.txt`: Kafka 성능 suite의 마지막 완료 출력
- `ordering-failure/latest.json`: ordering / failure injection suite의 마지막 완료 결과
- 그 밖의 날짜별·중간 산출물: 로컬 보관, 기본 Git 추적 제외

## Interpretation Rules

- 2026-06 성능 출력의 event 응답 `200`: HTTP `202 Accepted` 계약을 코드에 명시하기 전 수집한 역사적 증거
- 2026-06 request shape는 legacy/order contract이며 generic v2 route·serialization·validation 성능 증거가 아님
- 현재 계약 검증: 새 빌드에서 `202` 응답과 OpenAPI schema를 별도 재실행해 확인
- 2026-06 `accepted-to-persisted`: API 수락 시각과 PostgreSQL row의 `created_at` 또는 row 조회 가능 시점을 비교한 historical proxy
- current PowerShell `accepted_to_status_observed_ms`: client가 `persisted` status를 본 시각까지, polling/network 포함
- current Worker histogram: `commit()` 반환 직후 `persisted_at`까지; 아직 새 cluster 원본 없음
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
