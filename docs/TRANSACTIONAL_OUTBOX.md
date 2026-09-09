# Notification Transactional Outbox

2026-09-05 `dev-kafka` source candidate입니다. Worker의 PostgreSQL commit 직후 process crash로
notification 발행이 빠질 수 있던 경계를 DB에 저장하는 발행 의도로 보완했습니다.
공개 image/runtime 승격과 성능 baseline 재측정은 아직 하지 않았습니다.

## 처리 계약

- Migration `0009_notification_outbox`: message별 유일한 outbox row, payload, 생성 시각, retry 시각/횟수, published_at
- Core Worker: event·request status·outbox를 같은 transaction에 기록. Outbox insert가 실패하면 event commit도 실패
- Publisher: `WORKER_MODE=outbox`, 한 row를 `FOR UPDATE SKIP LOCKED`로 선택하고 Kafka ACK까지 잠금을 유지
- ACK 후 published_at과 attempt 횟수를 commit. 실패 시 row를 유지하고 1~60초 capped exponential backoff 적용
- Kafka ACK 후 DB commit 전 crash는 재발행 가능. 기존 `notification_attempts.message_id` unique constraint로 DB 중복 억제
- 완료 row는 재처리 시 발행 의도가 다시 생성되지 않도록 유지. 현재 자동 삭제·보관 정책 없음
- 과거 message 전체를 자동 backfill하지 않음. 이미 발생한 과거 notification 누락을 소급 복구했다고 해석하지 않음

보장 범위는 **at-least-once 발행과 DB attempt의 중복 억제**입니다. Exactly-once나 외부 이메일/SMS 발송 성공을
뜻하지 않습니다. Notification 순서는 별도 보장하지 않으며 core event의 stream_seq ordering 경계는 유지합니다.
DB transaction 안에서 Kafka ACK를 기다리므로 publisher당 DB connection/row lock이 점유됩니다.
현재 배포 기본은 publisher 1개이며 동시 2개를 격리 실험했습니다. 처리량 우위는 아직 주장하지 않습니다.

## 실제 장애 실험

Run `20260905T144053Z`, result **PASS**.
전체 local suite `658 passed`, 변경된 infrastructure contract `27 passed`, Prometheus `promtool`
규칙 24개와 scrape config 검증을 통과했습니다. Kustomize render도 통과했습니다.
Local image `messaging-portfolio:outbox-candidate`, digest
`sha256:bc1d2280acb2dd1f6ebefe271252ba9186a6e4d7cd5060c7a186f95ed4d1118c`.
Source checkout `fc59535d7d21a90aa904df5fc4b0fcd7ea40a6f0`에 uncommitted 변경을 포함했으며
실제 코드 hash는 [summary](../results/outbox-failure/20260905T144053Z/summary.json)에 보존했습니다.

| 주입 지점 | 실제 결과 |
| --- | --- |
| Event/status/outbox commit 반환 직후 `os._exit(71)` | event 1개, pending intent 1개 보존. Core Kafka offset commit 전 종료 후 재처리 가능 |
| Publisher send 함수 오류 | pending 유지, retry 횟수·다음 시각 기록. Broker outage 자체는 이 arm에서 주입하지 않음 |
| 실제 Kafka ACK 직후 `os._exit(72)` | 완료 표시 전 transaction rollback. 재시작 후 같은 job 재발행, DB attempt는 1개 |
| Outbox INSERT 후 DB commit 전 `os._exit(73)` | 해당 event·outbox·status 미확정, Kafka 재처리 후 저장 복구 |
| Pending 10개에 publisher 2개 | 최종 pending 0, 추가 notification 중복 없음 |

최종 event 12개, persisted status 12개, outbox 12개, DB notification attempt 12개,
Kafka notification 13개입니다. ACK 경계에서 의도한 1개 재발행을 실제 topic 조회로 확인했습니다.
Event request ID·stream_seq·schema_version·payload·metadata 및 notification payload 일치를 검증했습니다.
원본 `messaging-app` workload/PVC/Application spec hash는 전후 일치했고 실험 리소스는 정리했습니다.
처음 `20260905T141701Z` 실행은 probe의 `/service` import 경로 누락으로 실패했으며 성공 결과에 합산하지 않습니다.

실험은 단일 kind node의 별도 namespace에서 실제 Kafka/PostgreSQL을 사용했습니다.
Fault code는 별도 ConfigMap probe 프로세스에만 적용했고 제품 코드에는 kill 스위치를 추가하지 않았습니다.
Process exit 경계의 검증이며 노드 손실·DB HA failover·장시간 부하·외부 채널 성공의 증거는 아닙니다.

## 재현

```powershell
docker build -t messaging-portfolio:outbox-candidate .
# kind에 이미지를 적재한 뒤 digest 참조도 containerd에 등록합니다.
.venv\Scripts\python.exe scripts/outbox_failure_lab.py --context kind-messaging-ha --image docker.io/library/messaging-portfolio@sha256:ACTUAL_LOADED_DIGEST
```

Runner는 기존 release lab의 격리 dependency bootstrap을 사용하고 baseline chart만 실행합니다.
새 image의 migration을 적용하며, 과거 release failure lab의 synthetic migration `0009_release_lab`은 실행하지 않습니다.
Raw checkpoint는 로컬 `results/release-failure/<run ID>/`에도 남습니다. Outbox 전용 phase 증거는
`results/outbox-failure/<run ID>/`에 기록합니다.

## 배포와 운영

1. Migration Job wave `-2`로 additive table 생성
2. 새 core Worker와 publisher wave `-1`, API wave `0` 진행
3. Migration 성공 후 새 Worker가 기록한 pending row가 drain되는지 확인

Migration 이전 새 Worker 배포는 허용하지 않습니다. 구 Worker와 혼재하는 rollout 구간에서는 구 Worker의
직접 publish 경로가 남으므로 모든 이벤트가 outbox 보호를 받는다고 주장하지 않습니다.
Worker image를 이전 버전으로 되돌릴 때에는 DB table을 유지하고 새 publisher를 남겨 pending을 drain해야 합니다.
Downgrade는 pending 의도 삭제를 막기 위해 명시적으로 거부합니다.
GitOps overlay는 아직 과거 published image를 참조하므로 현재 dirty manifest를 그대로 운영 cluster에 적용하지 않습니다.
CI로 새 image를 게시하고 migration·Worker·publisher의 image 정합성을 확인한 뒤 승격해야 합니다.

Prometheus의 `outbox-publisher` scrape와 다음 지표를 추가했습니다.

- `messaging_outbox_pending`: DB pending 수; 여러 publisher를 함께 조회하면 중복 합산 대신 `max` 사용
- `messaging_outbox_oldest_pending_seconds`: 가장 오래된 미발행 intent의 나이
- `messaging_outbox_published_total`: ACK와 완료 transaction commit 횟수
- `messaging_notification_publish_failures_total`: 재시도 대상으로 유지한 send failure 횟수
- `MessagingOutboxPendingOld`: oldest age >60초가 2분 지속되면 warning
- `MessagingOutboxPublisherUnavailable`: scrape 가능한 publisher 부재 또는 모든 관측 relay의 transaction 오류가 2분 지속되면 warning

완료되지 않는 row는 `last_error`의 예외 종류·attempts·next_attempt_at을 조회해 확인합니다. Payload/접속 정보는
error 문자열로 저장하지 않습니다. 자동 terminal drop은 없으며 장기 실패 격리 정책은 후속 과제입니다.
Publisher probe는 현재 metrics TCP 수준이며 실제 진행 상태는 backlog와 health metric으로 함께 확인해야 합니다.
Benchmark reset은 publisher도 중지한 뒤 DB/topic을 초기화합니다. 기존 성능 수치는 outbox 성능으로 재사용하지 않습니다.
