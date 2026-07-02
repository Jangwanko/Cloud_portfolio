# 패치 노트

Kafka Event Stream Systems 포트폴리오의 주요 구현, 검증, 튜닝 기록입니다.

## 2026-07-03 업데이트: 데모 결과 정리 제거

변경 내용:

- 데모 화면 버전 `1.3.3` 적용.
- 4번째 영역의 `결과 정리` 요약 카드 제거.
- Operations Advisor와 DB 저장 컬럼 설명은 유지.
- 요청 수, Kafka 적재, DB 저장, 총 소요시간은 운영자 이벤트 큐의 처리 현황에서만 확인.

해석:

- demo-lite에서는 대량 전송 후 별도 결과 요약보다 핵심 흐름과 운영 신호를 우선합니다.
- DB 저장 완료 확인은 유지하되, 화면 중복 요약은 줄여 Pgpool 부하와 UI 복잡도를 낮춥니다.

## 2026-07-03 업데이트: 데모 DB 저장 확인 polling 제한

변경 내용:

- 데모 화면 버전 `1.3.2` 적용.
- Kafka append 이후 DB 저장 확인 polling을 무제한 병렬에서 느린 batch 확인으로 변경.
- `POLL_CONCURRENCY=4`, `POLL_BATCH_SIZE=12`, `POLL_BATCH_DELAY_MS=3000`으로 `/v1/event-requests/{request_id}` 조회 속도 제한.
- 1000건 이상 샘플 전송 시 API / Pgpool / PostgreSQL request status 조회 폭주 완화.

해석:

- Pgpool slot 고갈은 Worker persistence만이 아니라 데모 화면의 대량 status polling에서도 발생할 수 있습니다.
- Kafka 적재와 DB 저장 확인은 분리하되, demo-lite에서는 확인 polling도 connection budget 안에서 수행해야 합니다.

## 2026-07-03 업데이트: DB connection pool transaction 정리

변경 내용:

- DB connection을 pool에 반환하기 전 rollback으로 열린 transaction 정리.
- `request_statuses` 조회 polling 후 `idle in transaction`이 짧게 누적되는 현상 완화.
- connection slot 고갈로 Pgpool health가 흔들리는 위험 감소.
- regression test로 read-only connection 반환 시 rollback 호출 확인.

해석:

- `idle in transaction`이 오래 남으면 Pgpool / PostgreSQL connection slot을 점유합니다.
- read-only SELECT도 psycopg2 기본 transaction을 열 수 있으므로 pool 반환 시 상태 정리가 필요합니다.

## 2026-07-03 업데이트: 데모 로그인 중복 생성 요청 정리

변경 내용:

- 데모 화면 버전 `1.3.1` 적용.
- 자동 운영 상태 확인 중 `/v1/users`를 반복 호출하지 않도록 변경.
- 같은 브라우저 세션에서 demo user 생성 시도는 base URL / username 기준 1회로 제한.
- PostgreSQL log에 반복되던 `users_username_key` duplicate error 노이즈 감소.

해석:

- `demo-order-user already exists`는 DB primary 다운 증거가 아니라 중복 계정 생성 시도 로그.
- readiness / DLQ status 확인은 login 중심으로 유지하고, 계정 생성은 초기 준비 단계로 제한.

## 2026-07-01 업데이트: DLQ 전체 수동 재처리와 예약 실패 정리

변경 내용:

- 데모 화면 버전 `1.3.0` 적용.
- `전체 수동 재처리` 버튼 추가.
- replay 가능한 DLQ event를 한 번에 ingress topic으로 재투입 요청.
- replay guard 도달 또는 재처리 실패 event는 사용자 확인 대상으로 남김.
- API 전송 실패 event는 `send_failed`로 표시하고 예약건수에서 제외.
- 예약건수는 아직 실행되지 않은 전송 대기 건수로 유지.

## 2026-07-01 업데이트: DLQ 수동 재처리 완료 표시

변경 내용:

- 데모 화면 버전 `1.2.1` 적용.
- `수동 재처리` 요청 후 request status를 조회해 DB 저장 완료 확인.
- Worker가 DB에 저장하면 버튼 문구를 `재처리 완료`로 변경.
- 저장 확인 전에는 `재처리 확인 중`으로 표시.

## 2026-07-01 업데이트: DLQ 상세 보기와 수동 재처리

변경 내용:

- 데모 화면 버전 `1.2.0` 적용.
- 운영 상태 확인 영역에 `DLQ 상세 보기` 버튼 추가.
- 최근 DLQ event의 `failed_reason`, `request_id`, `stream_id`, `replay_count` 확인.
- replay 가능한 event는 `수동 재처리` 버튼으로 ingress topic 재투입.
- replay guard 도달 event는 `수동 확인` 상태로 표시.
- API endpoint `POST /v1/dlq/ingress/replay` 추가.
- OpenAPI contract와 UI 정적 계약 테스트 추가.

운영 기준:

- DLQ 원본 Kafka log는 삭제하지 않음.
- 수동 재처리는 같은 payload를 `message-ingress`로 다시 넣는 복구 요청.
- blocked event는 자동/수동 replay보다 원인 확인과 데이터 보정 우선.

## 2026-07-01 업데이트: transient DB 장애 Worker retry 보장

변경 내용:

- Worker가 PostgreSQL / Pgpool transient 장애를 DLQ로 종료하지 않고 계속 retry하도록 변경.
- Kafka offset은 DB persistence 성공 후에만 commit되는 흐름 유지.
- retry backoff 상한 `INGRESS_RETRY_MAX_DELAY_SECONDS=30` 추가.
- demo-lite / full manifest에 retry max delay 환경값 명시.
- regression test로 OperationalError가 여러 번 발생해도 DLQ 이동 없이 복구 후 persisted 되는 흐름 고정.
- 데모 화면 버전을 `1.1.4`로 업데이트.

해석:

- 이 프로젝트의 핵심은 DB 장애 중 Kafka에 적재된 event가 DB 복구 후 저장되는 것.
- transient DB 장애를 DLQ로 닫으면 Kafka backlog / recovery 증거가 약해지므로 Worker retry path가 기본.

## 2026-07-01 업데이트: demo-lite DLQ replayer 복구 경로 활성화

변경 내용:

- `demo-lite`에서 `dlq-replayer`를 `0 -> 1` replica로 변경.
- DB / Pgpool 장애가 Worker inline retry 기간보다 길어 DLQ로 이동한 event를 복구 후 자동 replay 대상으로 유지.
- `dlq-replayer` resource request / limit을 저사양 서버 기준으로 축소.
- `docs/DEMO_LITE.md`, `docs/OPERATIONS.md`에 DB 복구 후 재처리 흐름 기록.

해석:

- Kafka에 적재된 event가 DB 장애 중 DLQ로 격리되더라도 DB 복구 후 ingress topic으로 재주입될 수 있음.
- DLQ replay guard에 걸린 `blocked` event는 자동 처리하지 않고 운영자가 확인.

## 2026-06-30 업데이트: demo-lite Pgpool connection budget 조정

변경 내용:

- `demo-lite` Pgpool `numInitChildren`을 `8 -> 16`으로 확대.
- Pgpool `reservedConnections`를 `1 -> 2`로 확대.
- API / Worker `DB_POOL_MAX_CONN`을 `4 -> 2`로 축소.
- Pgpool readiness 실패 원인인 `FATAL: Sorry, too many clients already`를 운영 문서에 기록.
- 저사양 서버에서는 처리량보다 Pgpool health check, login, 운영 상태 확인이 들어갈 connection 여유를 우선.

반영 방법:

- PostgreSQL / Pgpool은 Argo CD app manifest가 아니라 Helm chart bootstrap 영역.
- 서버 반영 시 `k8s/values/postgresql-lite-values.yaml`로 `helm upgrade` 실행 필요.

## 2026-06-24 업데이트: README 흥미 유도 섹션 보강

변경 내용:

- README 상단에 `What To Look For` 섹션을 추가했습니다.
- 설계, 파이프라인, 데모, 운영, 확장 관점에서 이 프로젝트를 왜 봐야 하는지 짧게 설명했습니다.
- README가 단순 문서 목차가 아니라 데모와 상세 문서로 들어가게 만드는 입구 역할을 하도록 조정했습니다.

## 2026-06-24 업데이트: README 데모 경계와 GitHub 링크

변경 내용:

- README에서 본래 검증 시스템과 저사양 `demo-lite` 실행 환경을 가볍게 분리해 설명했습니다.
- 데모 화면 운영 링크에 GitHub repository 진입점을 추가했습니다.
- 사용자에게 보이는 데모 화면 변경이므로 화면 버전을 `1.1.0`으로 올렸습니다.

## 2026-06-24 업데이트: demo-dev 개발 브랜치 분리

변경 내용:

- `demo-dev` 브랜치를 저사양 데모 기능과 문서 변경을 사람이 작업하는 개발 브랜치로 추가했다.
- `demo-lite`는 2코어 k3s 서버용 축소 데모 배포 브랜치로 정리했다.
- `demo-lite`에는 GitHub Actions image tag commit이 섞일 수 있으므로 일반 개발 작업은 `demo-dev`에서 진행한다.
- `AGENTS.md`의 브랜치 역할, 문서 공유 대상, Change Scope 규칙에 `demo-dev`를 추가했다.

## 2026-06-24 업데이트: 데모 화면 버전 기준 조정

변경 내용:

- `AGENTS.md`의 데모 화면 버전 규칙을 조정했다.
- 외형적 변경이 없는 내부 수정은 세 번째 숫자(patch)를 올린다.
- 사용자가 보는 화면이나 흐름에 변화가 있으면 두 번째 숫자(minor)를 올린다.
- 시스템이나 서비스 컨셉이 크게 바뀌는 수준이면 첫 번째 숫자(major)를 올린다.
- 대부분의 일상 변경은 세 번째 숫자 변경으로 처리한다.

## 2026-06-24 업데이트: DB 저장 구조 노출

목표:

- Kafka append 이후 Worker가 PostgreSQL에 어떤 컬럼으로 저장하는지 데모 화면에서 바로 확인할 수 있게 한다.
- 주문 이후 이벤트를 나중에 데이터 분석 파이프라인으로 확장할 수 있도록 `messages` table에 구조화 컬럼을 추가한다.

변경 내용:

- `messages` table에 `event_type`, `category`, `payment_id` 컬럼과 분석 조회용 index를 추가했다.
- Worker persistence가 Kafka payload의 `event_type`, `category`, `payment_id`를 DB row, request status, snapshot payload에 함께 반영한다.
- 데모 화면 결과 패널에 `DB 저장 컬럼` / `Stored DB Columns` 섹션을 추가했다.
- 최근 DB row 목록은 raw 데이터 노출과 화면 복잡도를 줄이기 위해 표시하지 않는다.
- 데모 UI 변경에 맞춰 화면 버전을 `1.0.2`로 올렸다.
- `docs/DEMO_GUIDE.md`에 DB storage evidence 설명을 추가했다.
- `AGENTS.md`에 README와 `docs/` 변경도 관련 브랜치에 공유해야 한다는 운영 규칙을 추가했다.

해석:

- 이제 포트폴리오 시연에서 Kafka 처리 흐름뿐 아니라, 분석 가능한 DB 저장 구조까지 한 화면에서 설명할 수 있다.
- 이 구조는 향후 batch export, CDC, warehouse load, 운영 통계 대시보드 같은 데이터 분석 파이프라인으로 이어질 수 있다.

## 2026-06-21 업데이트: 2코어 서버용 demo-lite 프로파일 추가

목표:

- 2코어 2스레드급 서버에서 포트폴리오 데모를 실행할 수 있는 축소 profile을 제공한다.
- 기존 full-ha 기준은 유지하고, 저사양 서버에서는 API -> Kafka -> Worker -> DB 흐름 시연에 집중한다.
- demo-lite 결과가 full-ha Kafka baseline과 섞이지 않도록 문서 경계를 둔다.

변경 내용:

- `demo-lite` 브랜치를 만들고 저사양 서버용 설정을 분리했습니다.
- `k8s/gitops/overlays/demo-lite` kustomize overlay를 추가했습니다.
- `k8s/values/postgresql-lite-values.yaml`을 추가했습니다.
- `scripts/quick_start_lite.ps1`를 추가했습니다.
- `scripts/deploy_lite_k3s.sh`를 추가해 2코어 Linux 서버의 k3s 배포 흐름을 분리했습니다.
- `k8s/gitops/overlays/demo-lite-k3s`와 `scripts/bootstrap_argocd_lite_k3s.sh`를 추가해 2코어 k3s 서버에서도 Argo CD가 `demo-lite` 브랜치를 직접 동기화할 수 있게 했습니다.
- `k8s/scripts/install-ha.ps1`에 `-ValuesFile` 파라미터를 추가해 HA / lite PostgreSQL values를 선택할 수 있게 했습니다.
- README, `docs/DEMO_GUIDE.md`, `docs/DEMO_LITE.md`, `docs/OPERATIONS.md`에 full-ha와 demo-lite의 차이를 정리했습니다.

demo-lite 기준:

- Kafka: `1 broker`, replication factor `1`, min ISR `1`, partitions `3`
- PostgreSQL: `1 PostgreSQL`, `1 Pgpool`
- API: min `1`, max `2`
- Worker: min `1`, max `2`
- notification-worker / dlq-replayer: `0` replica
- Prometheus / Grafana: 유지하되 resource request를 낮춤

해석:

- demo-lite는 HA 성능 증명이 아니라 저사양 서버용 시연 profile입니다.
- 장애 허용성, Kafka 3 broker, PostgreSQL HA, KEDA scale-out baseline은 full-ha profile에서 설명합니다.
- demo-lite 성능 수치는 `docs/TEST_RESULTS.md`의 Kafka baseline과 섞지 않습니다.

## 2026-06-21 업데이트: 데모 운영 신호와 문서 기준 정리

목표:

- 현재 데모 화면의 실제 동작과 README / 운영 문서의 설명을 맞춘다.
- 예약 건수, Kafka 적재, DB 저장, Worker replica 표시의 의미를 포트폴리오 시연 기준으로 고정한다.
- 롤백 기준과 문서 운영 규칙을 `AGENTS.md`에 남겨 이후 작업에서 같은 혼선을 줄인다.

변경 내용:

- README의 Local Demo 설명을 현재 화면 기준으로 갱신했습니다.
- 데모 샘플 단위를 `10개`, `100개`, `1000개`로 정리했습니다.
- Grafana 링크를 운영 overview dashboard deep link로 바꿨습니다.
- `처리량/sec` 설명을 제거하고 `총 소요시간`, Worker 현재/최대 replica 기준으로 설명했습니다.
- `docs/DEMO_GUIDE.md`를 추가해 데모 URL, 실행 절차, counter 의미, Operations Advisor, reset 동작, 인터뷰용 설명을 분리했습니다.
- `AGENTS.md`에 demo UI 변경 안전 규칙과 rollback 기준을 추가했습니다.
- `AGENTS.md`의 KEDA Kafka scaler lag threshold를 현재 local demo 기준인 `100`으로 맞췄습니다.

운영 의미:

- `예약 건수`는 전송 시작 후 `남은 예약/전체 예약`으로 표시합니다. API가 Kafka append에 성공하면 줄어듭니다.
- `Kafka 적재`는 API가 `message-ingress` topic에 append한 수입니다.
- `DB 저장`은 Worker가 PostgreSQL commit까지 완료한 수입니다.
- Worker 표시는 `현재 replica/최대 replica`입니다. 예: `2/8`, `6/8`.
- Operations Advisor는 rule-based AX 보조 영역이며 AI API를 호출하지 않습니다.
- `RESET DEMO DB`는 로컬 데모 이벤트 DB와 `message-ingress-dlq` topic을 초기화합니다.

검증:

- `.venv\Scripts\python.exe -m pytest -q`: `65 passed`

## 2026-06-19 업데이트: 운영형 데모 화면과 예약 큐 카운터 안정화

목표:

- 포트폴리오 데모가 개념 설명에 머무르지 않고, 로컬 Kubernetes 위에서 Kafka / Worker / PostgreSQL 처리 흐름을 눈으로 확인할 수 있게 한다.
- 외국인 리크루터도 볼 수 있도록 README 상단과 데모 화면에 KO / EN 전환을 제공한다.
- 샘플 이벤트를 1건, 10건, 100건 단위로 예약하고 한 번에 전송하는 흐름을 만들되, Kafka 적재와 DB 저장을 별도 지표로 보여준다.
- 운영자가 혼동하기 쉬운 예약 큐 비우기, DB 초기화, 처리 중 카운터의 의미를 화면과 문서에 고정한다.

변경 내용:

- README 상단에 로컬 데모 설치와 사용 방법을 한국어 / 영어로 추가했습니다.
- `demo/order-dashboard.html`에 KO / EN 전환을 추가하고, EN 선택 시 기본 event body도 영어 문구로 바뀌게 했습니다.
- 오른쪽 운영자 패널에 `API accepted -> Kafka appended -> Worker persisted -> DB 저장` 흐름을 단계별로 표시했습니다.
- 처리 현황을 `예약 건수`, `Kafka 적재`, `DB 저장`, `총 소요시간`, `처리량/sec`로 분리했습니다.
- `샘플 1개 추가`, `샘플 10개 추가`, `샘플 100개 추가`와 `결제 완료 / 주문 완료 이벤트 보내기` 버튼 배치를 데모 흐름에 맞춰 정리했습니다.
- 운영 로그와 이벤트 목록은 높이를 고정해 이벤트가 많아져도 화면이 끝없이 늘어나지 않게 했습니다.
- 운영 링크 영역에 `Demo event DB reset` 작업을 추가했습니다. 사용자가 `RESET DEMO DB`를 입력해야 `/v1/admin/demo/reset-events`가 실행됩니다.
- `DemoResetRequest`, `DemoResetResponse` schema와 reset API 계약 테스트를 추가했습니다.
- `Operations Advisor` 카드를 추가했습니다. 현재는 AI API를 호출하지 않고, 예약 건수 / Kafka 적재 / DB 저장 차이를 정해진 규칙으로 해석해 운영자에게 다음 확인 항목을 제시합니다.

버그 수정:

- `운영자 이벤트 큐 비우기`라는 이름이 실제 동작과 다르게 보였기 때문에 `전송 전 예약 비우기`로 바꿨습니다.
- 버튼 동작을 "아직 Kafka로 보내지 않은 `reserved` 이벤트만 취소"로 정리했습니다.
- 이미 시작한 작업은 취소하지 않고 Kafka 적재 / DB 저장까지 계속 추적합니다.
- 처리 중 버튼을 누르면 이전 비동기 polling이 현재 화면 카운터를 덮어쓰는 문제가 있어 `uiSession`으로 화면 세션을 분리했습니다.
- 예약 리스트만 사라지고 예약 건수가 유지되는 문제를 고쳤습니다. 취소된 예약 수만큼 `queueStats.queued`와 `runTarget`을 함께 줄입니다.
- 1건 차이 버그를 고쳤습니다. API 전송 직전에 `event.status = "sending"`으로 바꿔, 이미 전송 중인 1건이 `전송 전 예약 비우기` 대상에 들어가지 않게 했습니다.
- API 전송 자체가 실패하면 `sending` 이벤트를 다시 `reserved`로 돌려 재시도 가능한 예약으로 남깁니다.

검증:

- `.venv\Scripts\python.exe -m pytest -q`: `64 passed`
- Docker image: `messaging-portfolio:local` 재빌드 완료
- kind 클러스터 반영: `tools\kind.exe load docker-image messaging-portfolio:local --name messaging-ha`
- API rollout: `kubectl rollout restart deployment/api -n messaging-app` 후 정상 완료
- readiness: `http://localhost/health/ready` 기준 `status=ready`, Kafka reachable, PostgreSQL primary reachable
- 배포된 데모 HTML에서 `event.status = "sending"`, `cancelPendingReservations`, `pending reservation skipped` 반영 확인

해석:

- 이 업데이트는 Kafka 처리 성능 개선보다 포트폴리오 시연성과 운영 의미 전달을 강화한 변경입니다.
- 화면 카운터는 사용자 주문 완료 응답이 아니라 운영자 관점의 내부 처리 흐름을 보여줍니다.
- `예약 건수`는 아직 DB 저장 완료 전인 데모 예약 / 진행 중 작업의 남은 수를 의미합니다.
- `Kafka 적재`와 `DB 저장`을 분리해 API append 성공과 Worker persistence 완료가 같은 단계가 아니라는 점을 보여줍니다.
- `전송 전 예약 비우기`는 시작 전 예약 취소입니다. 이미 시작한 Kafka / Worker 작업을 취소하는 기능은 아닙니다.
- `Operations Advisor`는 AX 확장 지점을 보여주기 위한 rule-based 단계입니다. 향후 별도 AI Worker가 같은 운영 신호를 소비해 더 풍부한 요약을 생성할 수 있지만, 핵심 persistence path에는 들어가지 않습니다.

## 1차 실험: Kafka 이벤트 스트림 기준선

목표:

- API request intake를 Kafka ingress topic 중심으로 구성한다.
- Worker consumer group이 Kafka partition을 소비해 PostgreSQL HA에 비동기로 영속화한다.
- `stream_id`를 Kafka message key로 사용해 같은 stream 이벤트가 같은 순서 보장 경계에 들어가도록 한다.
- Kafka DLQ topic과 DLQ Replayer로 실패 이벤트의 복구 경로를 만든다.
- 기본 기능, DLQ, readiness, autoscaling, 성능 기준선을 한 번에 검증한다.

구현 범위:

- FastAPI event request API
- Kafka ingress topic: `message-ingress`
- Kafka DLQ topic: `message-ingress-dlq`
- Worker consumer group: `message-worker`
- 3-broker KRaft Kafka StatefulSet
- topic partitions `8`, replication factor `3`, `min.insync.replicas=2`
- API CPU HPA
- Worker KEDA Kafka lag scaler
- Prometheus / Grafana observability
- PostgreSQL HA + Pgpool persistence path

검증 결과:

- Kafka broker rollout: 통과
- Kafka topic bootstrap: 통과
- API Kafka intake: 통과
- Worker consume and PostgreSQL persist: 통과
- Smoke test: 통과
- API contract test: 통과
- Kafka DLQ flow: 통과
- PostgreSQL 장애 시 degraded readiness 시나리오: 통과
- Unit tests: 통과
- k6 Kafka intake 기준선: 통과

1차 성능 기준선:

- 부하 프로필: `single500`
- 동시 사용자: `100`
- 실행 시간: `30s`
- idempotency header: 비활성화
- 순차 검증 이벤트 수: `100`
- 순차 검증 결과: `stream_seq 1..100`
- 전체 HTTP 요청 수: `31710`
- event status 200: `31706`
- event status 503: `0`
- 오류율: `0.00%`
- 평균 latency: `44.04ms`
- p95 latency: `86.95ms`
- p99 latency: `113.78ms`
- 비동기 수락 latency 평균 / p95 / 최대: `55.68ms` / `65.83ms` / `86.55ms`
- accepted-to-persisted 평균 / p95 / 최대: `7.51ms` / `8.04ms` / `10.92ms`
- API HPA 최종 replica: `8`
- Worker KEDA 최종 replica: `8`

1차에서 확인한 한계:

- Pgpool이 `1 replica`라 PostgreSQL HA 앞단의 단일 장애점으로 남아 있었다.
- 초기 진단 구현에서 idempotency header를 켠 부하에서는 PostgreSQL state-store path가 API hot path에 들어와 Pgpool 압박과 `503`이 발생했다.
- Worker가 transient persistence failure를 만나면 실패 이벤트를 Kafka tail로 재발행할 수 있어, 같은 stream의 뒤 이벤트가 앞 이벤트를 추월할 가능성이 있었다.

## 2차 실험: Pgpool HA와 엄격한 stream 순서 보장

목표:

- Pgpool 단일 장애점을 줄인다.
- Pgpool replica 증가가 PostgreSQL connection pressure로 이어지지 않도록 pool 값을 낮춘다.
- 같은 stream 안에서는 앞 이벤트가 실패해도 뒤 이벤트가 먼저 영속화되지 않도록 순서 보장을 강화한다.
- 보강 후 같은 순차 보증 테스트와 성능 suite를 다시 실행한다.

구현 변경:

- Pgpool `replicaCount`: `1 -> 2`
- Pgpool PDB 추가: `minAvailable=1`
- PostgreSQL PDB 명시: `minAvailable=2`
- Pgpool `numInitChildren`: `128 -> 64 -> 32` for local kind memory stability
- Pgpool `maxPool`: `4 -> 2`
- Pgpool `childMaxConnections`: `200 -> 100`
- Pgpool `reservedConnections`: `2 -> 4`
- Pgpool idle/lifetime timeout 추가
- Worker retry 방식을 Kafka tail 재발행에서 inline retry로 변경
- 같은 Kafka offset에서 retry/backoff를 수행한 뒤 성공 또는 DLQ 처리 후 offset commit
- performance suite에 같은 stream 순차 보증 테스트 포함
- k6 summary에 p99 latency 출력 추가

2차 검증 결과:

- Pgpool deployment: `2/2` ready
- Pgpool PDB: `minAvailable=1`
- PostgreSQL StatefulSet: `3/3` ready
- PostgreSQL PDB: `minAvailable=2`
- readiness: `ready`
- Kafka bootstrap reachable: `true`
- PostgreSQL primary reachable: `true`
- PostgreSQL standby count: `2`
- 같은 stream 순차 보증: 통과
- Unit tests: `58 passed`
- k6 Kafka intake 기준선: 통과

2차 성능 기준선:

- 실행 시각: `2026-04-28T02:40:29+09:00`
- 부하 프로필: `single500`
- 동시 사용자: `100`
- 실행 시간: `30s`
- idempotency header: 비활성화
- 순차 검증 이벤트 수: `100`
- 순차 검증 결과: `stream_seq 1..100`, body 순서 일치
- 전체 HTTP 요청 수: `31676`
- event status 200: `31672`
- event status 503: `0`
- 오류율: `0.00%`
- 평균 latency: `44.13ms`
- p95 latency: `80.65ms`
- p99 latency: `103.57ms`
- 비동기 수락 latency 평균 / p95 / 최대: `53.34ms` / `63.59ms` / `75.22ms`
- accepted-to-persisted 평균 / p95 / 최대: `7.29ms` / `7.67ms` / `8.14ms`
- API HPA 최종 replica: `6`
- Worker KEDA 최종 replica: `4`

2차 해석:

- Pgpool을 2개로 늘리면서도 pool 폭을 낮춰 DB connection pressure를 제어했다.
- 같은 stream 순서 보장은 Kafka partition key만으로 끝나지 않고, Worker failure handling까지 함께 맞아야 한다는 점을 확인했다.
- inline retry는 같은 partition의 뒤 이벤트를 막기 때문에 엄격한 순서 보장에는 유리하다.
- 대신 앞 이벤트가 오래 막히면 같은 stream 경계의 뒤 이벤트도 함께 대기한다. 이 trade-off는 순서 보장을 선택한 결과다.
- 최신 baseline에서는 Pgpool HA 보강 후에도 `503` 없이 100 VU / 30s를 통과했다.

## 2026-06-09 재실행: 정합성 재확인과 backlog drain 관측

목표:

- 현재 클러스터에서 Kafka append-first intake baseline이 크게 흔들리지 않는지 확인한다.
- 같은 실행 안에서 same-stream ordering과 async persistence completion을 다시 확인한다.
- 부하 직후 Worker consumer lag가 얼마나 쌓이고, KEDA max scale 이후 얼마나 걸려 drain되는지 본다.

실행 명령:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_kafka_performance_suite.ps1
```

검증 결과:

- 전체 HTTP 요청 수: `34284`
- event status 200: `34280`
- event status 503: `0`
- 오류율: `0.00%`
- 평균 latency: `36.86ms`
- p95 latency: `66.06ms`
- p99 latency: `104.99ms`
- same-stream ordering: `stream_id=30`, 100 events, `stream_seq 1..100`, ordering `pass`
- async persistence sample: `stream_id=31`, 50 events persisted
- accepted-to-persisted p95: `73.50ms`
- 부하 직후 Worker consumer lag: `36394`
- drain 경로: `36394 -> 33274 -> 23563 -> 11971 -> 0`
- Worker KEDA max replica: `8`
- 최종 drain: 약 14분 후 consumer lag `0`

해석:

- API intake latency와 요청 수는 개선됐지만, Worker consumer lag가 크게 쌓여 drain time이 새 튜닝 후보로 드러났다.
- 이 결과는 기존 2차 baseline을 대체하지 않고, API intake와 Worker persistence capacity를 분리해서 봐야 한다는 운영 신호로 기록한다.
- Worker scaling 효과는 API throughput 증가로 단정하지 않고 consumer lag, accepted-to-persisted latency, backlog drain time으로 판단한다.

## 2026-06-09 튜닝: Worker success path transaction 통합

목표:

- Worker replica가 max `8`까지 늘어도 backlog drain에 시간이 걸린 원인 후보 중 하나인 message 1건당 DB commit 비용을 줄인다.
- message persistence와 request status update를 같은 PostgreSQL transaction boundary로 묶는다.
- notification attempt 기록은 핵심 persistence transaction에서 분리한다.
- DB commit 이후에만 Kafka request status와 DB snapshot topic publish를 수행해 read cache 원본이 committed row 기준이라는 계약을 유지한다.

변경 내용:

- `persist_ingress_job()`을 추가해 Worker success path를 통합했습니다.
- 기존 `persist_message()` 내부 SQL을 cursor 기반 helper로 분리했습니다.
- `request_statuses` upsert는 cursor 기반 `upsert_request_status()`를 사용해 같은 transaction에 포함했습니다.
- 이후 `notification_attempts` insert는 `message-notifications` topic과 별도 `notification-worker` 처리로 분리했습니다.
- Kafka `message-request-status`와 `message-snapshots` publish는 commit 이후에 수행합니다.

검증:

- success path fake DB test에서 commit `1`회를 확인했습니다.
- `.venv\Scripts\python.exe -m pytest -q`: `60 passed`

Post-tuning performance suite:

- 실행 시각: `2026-06-09T02:17:11+09:00`
- same-stream ordering: `stream_id=34`, 100 events, `stream_seq 1..100`, ordering `pass`
- async persistence sample: `stream_id=35`, 50 events persisted
- 전체 HTTP 요청 수: `28839`
- event status 200: `28835`
- event status 503: `0`
- 오류율: `0.00%`
- 평균 latency: `53.47ms`
- p95 latency: `108.68ms`
- p99 latency: `134.53ms`
- accepted-to-persisted p95: `8.08ms`
- 부하 직후 Worker consumer lag: `29204`
- drain 경로: `29204 -> 23597 -> 15111 -> 6893 -> 0`
- Worker KEDA max replica: `8`
- 최종 drain: 약 10분 후 consumer lag `0`

해석:

- Worker persistence lag와 drain time은 개선됐습니다.
- API intake request count와 p95 latency는 악화됐습니다.
- 따라서 transaction 통합은 persistence path에는 효과가 있지만, 전체 k6 intake 기준선 개선으로는 아직 부족합니다. notification path는 별도 topic/worker로 분리했으며, 다음 측정은 API/Kafka publish path 영향과 notification-worker backlog를 분리해서 봐야 합니다.

## 현재 운영 기준선

현재 기준으로 이 프로젝트는 다음 구조를 기본값으로 둡니다.

- API는 Kafka ingress topic에 append하고 `202 Accepted`를 반환한다.
- Kafka는 ingress와 DLQ transport를 담당한다.
- Worker는 Kafka consumer group으로 partition을 소비한다.
- Worker success path는 message persistence와 request status update를 하나의 PostgreSQL transaction으로 처리한다.
- notification attempt 기록은 `message-notifications` topic과 별도 `notification-worker`가 처리한다.
- 같은 stream은 `stream_id` key를 통해 같은 Kafka partition ordering boundary에 들어간다.
- Worker는 persistence 실패 시 같은 offset에서 inline retry를 수행해 같은 stream의 뒤 이벤트가 앞지르지 못하게 한다.
- PostgreSQL HA는 최종 durable source of truth 역할을 맡는다.
- DB commit 이후 snapshot은 `message-snapshots` / `stream-snapshots` compacted topic으로 발행하고, API는 local materialized cache를 cache-first read에 사용한다.
- Pgpool은 2 replica로 구성하고 PDB와 보수적인 pool 값을 사용한다.
- kafka-exporter로 broker count, topic partition, `message-worker` consumer lag를 직접 관측한다.
- 핵심 운영 API는 FastAPI response model과 OpenAPI schema test로 계약을 고정한다.
- AWS IaC 골격은 EKS + RDS PostgreSQL + Amazon MSK + Secrets Manager 기준으로 정렬한다.

## 2026-06-18 튜닝: notification path 분리

목표:

- 알림 기록 실패가 핵심 message persistence transaction을 rollback시키지 않도록 분리한다.
- Worker success path는 message persistence와 request status update만 같은 transaction으로 처리한다.
- 알림은 DB commit 이후 `message-notifications` topic으로 넘기고 별도 `notification-worker`가 처리한다.

변경 내용:

- `KAFKA_NOTIFICATION_TOPIC=message-notifications`와 `KAFKA_NOTIFICATION_CONSUMER_GROUP=notification-worker` 설정을 추가했습니다.
- Kafka topic bootstrap에 `message-notifications` topic을 추가했습니다.
- `publish_notification_job()`과 `build_notification_consumer()`를 추가했습니다.
- `notification-worker` Deployment / Service를 추가했습니다.
- Prometheus scrape job과 `check_portfolio_status.ps1`에 `notification-worker`를 추가했습니다.

검증:

- `.venv\Scripts\python.exe -m pytest -q`: `60 passed`
- `scripts\check_portfolio_status.ps1`: `Portfolio status check passed`
- `notification-worker` readiness: `1/1`
- `up{job="notification-worker"}=1`
- `message-worker consumer_lag=0`
- `notification-worker consumer_lag=0`

Performance suite:

- 실행 시각: `2026-06-18T03:29:47+09:00`
- same-stream ordering: `stream_id=38`, 100 events, `stream_seq 1..100`, ordering `pass`
- async persistence sample: `stream_id=39`, 50 events
- 전체 HTTP 요청 수: `27795`
- event status 200: `27791`
- event status 503: `0`
- 오류율: `0.00%`
- 평균 latency: `57.64ms`
- p95 latency: `119.28ms`
- p99 latency: `150.60ms`
- accepted-to-persisted p95: `22.13ms`
- Worker KEDA max replica: `8`
- message-worker lag: 약 16분 후 `0`
- notification-worker lag: `0`

해석:

- notification path 분리는 성능 개선보다 장애 격리 개선입니다.
- 알림 기록 실패가 핵심 persistence transaction을 망가뜨리지 않는 구조가 됐습니다.
- 반면 이번 성능 suite에서는 API intake와 accepted-to-persisted latency가 개선되지 않았습니다.
- 다음 튜닝 후보는 Worker DB write throughput, Kafka consumer batch 처리, PostgreSQL lock/commit 비용 분리 측정입니다.

## 남은 튜닝 항목

- idempotency-enabled write load에서 Worker deduplication과 Kafka append-first 계약을 재검증
- Pgpool replica별 connection usage와 PostgreSQL `max_connections` 예산 계산
- DLQ topic depth / replay rate 전용 Grafana panel 강화
- 장시간 500+ VU capacity profile 측정
- multi-node Kubernetes 기준 anti-affinity / topology spread 검증
