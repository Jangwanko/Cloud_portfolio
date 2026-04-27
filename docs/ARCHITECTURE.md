# 아키텍처

## 구성 요소
- API (`FastAPI`)
  - event request 수락
  - Kafka ingress topic append
  - health / readiness / metrics 노출
- Kafka
  - ingress event log
  - stream 단위 partition ordering boundary
  - DLQ topic
  - consumer group offset 관리
- Worker
  - Kafka consumer group으로 ingress topic 소비
  - PostgreSQL 영속화
  - retry / DLQ 처리
- DLQ Replayer
  - Kafka DLQ topic 소비
  - ingress topic 재주입
- PostgreSQL HA
  - `bitnami/postgresql-ha` 기반
  - pgpool 경유 접근
- Prometheus / Grafana
  - metrics 수집, alert, dashboard
- Kubernetes autoscaling
  - API CPU 기반 HPA
  - Worker KEDA Kafka lag scaling
- metrics-server
  - HPA용 resource metrics 제공
- ingress-nginx
  - 로컬 kind 환경의 ingress 진입
- Runtime Secrets
  - auth key와 운영 credential 분리
- PostgreSQL Backup / Restore
  - 수동 logical backup
  - backup본 기반 restore
  - 주 1회 backup `CronJob`

## 외부 진입
현재 로컬 검증 기준의 기본 진입점은 아래와 같습니다.

- API: `http://localhost`
- Grafana: `http://localhost/grafana`
- Prometheus: `http://localhost/prometheus/`

Service는 `ClusterIP`로 두고, 외부 요청은 `ingress-nginx`가 받아 각 서비스로 라우팅합니다. 기본 문서와 데모 경로는 HTTP 기준이며, HTTPS는 self-signed certificate 기반 TLS 종료를 확인하는 보조 경로입니다.

## 요청 처리 흐름
1. 클라이언트가 API로 event request를 보냅니다.
2. API는 요청을 바로 DB에 쓰지 않고 Kafka ingress topic에 append합니다.
3. Kafka message key는 `stream_id`를 사용합니다.
4. Worker consumer group이 partition을 나눠 소비합니다.
5. Worker가 PostgreSQL에 event를 영속화합니다.
6. 실패하면 retry를 수행합니다.
7. retry 한도를 넘기면 Kafka DLQ topic으로 이동합니다.
8. DLQ Replayer가 복구 조건이 맞으면 ingress topic으로 재주입합니다.

## Kafka 설계 선택
Kafka를 request intake 경로에 둔 이유는 단순 queue buffer보다 event stream processing 특성을 더 명확히 검증하기 위해서입니다.

- `stream_id` key 기반 partitioning으로 같은 stream의 ordering boundary를 명확히 둡니다.
- Worker는 consumer group으로 partition을 분산 소비합니다.
- 처리 성공 후 offset commit을 수행해 재처리 가능성을 유지합니다.
- DLQ도 topic으로 분리해 실패 이벤트를 보존하고 replay합니다.
- Worker scaling은 queue length가 아니라 consumer lag를 기준으로 판단합니다.

설계 선택: 이 시스템은 최소 latency보다 요청 수락 안정성과 복구 가능성을 우선합니다. Kafka event log와 Worker persistence를 거치며 일부 latency를 감수하지만, DB 장애 전파를 줄이고 replay 가능한 event 처리 경로를 확보합니다.

## 인증 / 인가
현재 최소 범위의 인증 / 인가가 적용되어 있습니다.

- 사용자 생성 시 `password_hash` 저장
- `/v1/auth/login`으로 bearer token 발급
- 주요 API는 로그인 사용자 기준으로 처리
- stream membership 검증 적용

중요한 점:
- 인증은 token payload 기준으로 처리해서 DB down 중에도 인증 경로 자체 때문에 요청이 막히지 않도록 했습니다.
- Kafka-native 완성형에서는 membership / idempotency / request status 같은 low-latency state path를 event log path와 분리해야 합니다.

## 장애 시나리오별 동작

### PostgreSQL / Pgpool 병목
- API intake는 Kafka append를 통해 persistence path와 분리됩니다.
- Worker는 DB 쓰기 실패 시 retry를 수행합니다.
- retry 한도를 넘긴 요청은 Kafka DLQ topic으로 이동합니다.
- DB recovery 후 worker와 replayer가 다시 영속화를 진행합니다.
- Kafka 모드에서는 API가 sequence를 선점하지 않고, Worker가 persistence 시점에 sequence를 배정합니다. request status도 Worker persistence path에서 갱신해 API intake가 DB hot path에 강하게 묶이지 않도록 했습니다.

### Kafka broker 장애
- API가 ingress topic에 append할 수 없으면 event intake는 실패합니다.
- readiness는 Kafka reachable 여부를 반영해 `not_ready`로 내려갈 수 있습니다.
- Worker는 topic 소비를 중단합니다.
- Kafka recovery 후 API append와 Worker consume이 정상화됩니다.

### Worker backlog 증가
- API는 Kafka append를 통해 요청을 계속 수락할 수 있습니다.
- Worker 처리량이 ingress rate보다 낮으면 consumer lag가 증가합니다.
- KEDA Kafka scaler가 lag를 기준으로 Worker replica를 늘립니다.
- Worker replica 증가 또는 부하 감소 시 lag가 다시 줄어듭니다.

### DLQ replay
- Worker가 retry 한도를 넘긴 job을 Kafka DLQ topic에 publish합니다.
- `GET /v1/dlq/ingress`는 Kafka 모드에서 DLQ topic의 최근 메시지를 조회합니다.
- DLQ Replayer는 DLQ topic을 소비해 ingress topic으로 재주입합니다.
- replay된 event는 Worker consumer group에서 다시 처리됩니다.

## 자동 확장
현재 autoscaling은 API와 Worker가 서로 다른 기준을 사용합니다.

- API HPA
  - min replicas: `3`
  - max replicas: `8`
  - target CPU: `65%`
- Worker KEDA
  - min replicas: `2`
  - max replicas: `8`
  - trigger: Kafka consumer lag
  - consumer group: `message-worker`
  - topic: `message-ingress`
  - lag threshold: `400`

Worker를 CPU가 아니라 Kafka lag 기준으로 스케일링한 이유는, 이 프로젝트의 병목이 pure CPU보다 ingress rate와 downstream persistence 처리량의 차이에서 먼저 드러나기 때문입니다.

## 관측성
현재 관측 가능한 항목:
- API request count / latency
- API stage latency
- worker processing count / latency
- queue wait / accepted-to-persisted lag
- worker replica count / KEDA desired replicas
- Kafka health
- PostgreSQL primary / standby / replication state / replication delay
- DB / Kafka / Worker health
- Prometheus alert firing / resolution

관측 확장 포인트:
- Kafka consumer lag dashboard 통합
- Kafka broker HA topology metric
- Kafka DLQ topic depth / replay rate metric

## 백업과 복구
현재 PostgreSQL 운영 보강은 아래처럼 구성되어 있습니다.

- 수동 backup
  - `scripts/backup_postgres_k8s.ps1`
  - `pgpool` 경유 `pg_dump`
  - 결과는 로컬 `backups/`에 저장
- restore
  - `scripts/restore_postgres_k8s.ps1`
  - backup SQL 적용
  - `-Force` 필수
  - 필요 시 `-ResetSchema` 지원
- 주기 backup
  - HA 매니페스트에 `postgres-weekly-backup` `CronJob`
  - 스케줄: `0 3 * * 0`
  - cluster PVC `postgres-backups` 사용

## 운영 기준
- Kafka broker는 로컬 기준 3-broker KRaft StatefulSet입니다.
- 최신 Kafka intake baseline은 100 VU / 30초 기준 `30922` requests, error `0.01%`, p95 `92.25ms`입니다.
- 이 baseline은 `X-Idempotency-Key`를 끈 Kafka append 중심 경로입니다.
- idempotency header를 켠 경로는 PostgreSQL state-store 병목이 드러났고 별도 보강 대상입니다.
- Kafka lag / consumer group metric은 KEDA와 consumer group 상태를 기준으로 해석합니다.
- 멀티 파드 환경에서도 stream 단위 ordering boundary는 Kafka key와 partition 기준으로 유지합니다.
- 운영 UI는 로컬 포트폴리오 검증을 위해 ingress로 노출합니다.

## 신뢰성 상태 모델
Kafka-native 기준 readiness는 단순 up/down이 아니라 intake path와 persistence path를 분리해 봅니다.

### `ready`
- Kafka bootstrap reachable
- PostgreSQL writable primary reachable
- API / Worker metrics scrape 가능
- Worker consumer group running

### `degraded`
- PostgreSQL primary가 일시적으로 unavailable하지만 Kafka append path는 살아 있음
- Worker backlog 또는 consumer lag가 증가 중
- PostgreSQL standby count / replication state가 기대보다 약함
- Kafka broker는 살아 있지만 lag / replay가 증가 중

### `not_ready`
- Kafka bootstrap unreachable
- API가 ingress topic에 append할 수 없음
- PostgreSQL state path가 현재 API 계약상 필수인데 사용할 수 없음

## readiness와 alert 해석
- readiness는 현재 intake 가능 여부를 즉시 반영하며, Kafka append 가능 / PostgreSQL primary down 상태는 `degraded`로 유지합니다.
- `30초`는 readiness 유예가 아니라 alert 승격 유예입니다.
- Kafka unavailable은 intake write path 중단이므로 즉시 critical로 해석합니다.
- PostgreSQL persistence 장애는 Worker retry / DLQ replay와 함께 해석합니다.
