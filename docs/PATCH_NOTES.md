# 패치 노트

Reliable Event Processing System 포트폴리오의 주요 구현, 검증, 튜닝 기록입니다.

## 2026-07-27 demo-lite generic v2 동기화 candidate

- 2코어급 자원 경계 유지: Kafka `1`, PostgreSQL/Pgpool `1`, API/Worker `1..2`
- generic v2 API·Worker·migration·materialized cache·DLQ 기능을 저사양 overlay에 동기화
- local kind image와 public k3s registry image 경계 분리
- schema migration → Worker → API gate 순서 적용
- `demo-dev` validation·image 검증 뒤 tested tree와 image tag를 `demo-lite` 단일 release commit으로 게시
- Grafana 상단 standby 패널을 Kafka broker availability로 변경해 저사양 정상값 `1` 표시
- Demo UI `2.1.0`: 범용 envelope 증거를 Pipeline Evidence 안으로 이동해 하단 단독 카드 제거
- chart-managed PostgreSQL credential과 생성형 runtime secret 적용
- public deployment는 아직 UI `1.4.1`, API `1.0.0`; candidate 검증과 배포 완료 뒤 상태 갱신

## 2026-07-27 Grafana 현재 상태 패널 정정

- Kafka intake·PostgreSQL primary·standby 신호를 현재 시점 `min` 집계의 단일값으로 변경
- Worker 상단 신호를 Kubernetes Deployment `available/spec replicas` 가용률로 변경
- 종료된 Pod의 과거 시계열과 replica별 중복 숫자가 Stat 패널에 남는 문제 제거
- API 5xx 비율 축을 `0~100%`로 고정해 무오류 구간의 `10000%` 자동축 제거
- API·stage latency quantile 창을 `1m`에서 `5m`으로 변경해 희소 트래픽 변동 완화
- dashboard version `10`, Grafana config hash 갱신

## 2026-07-21 README Kubernetes 중심 재구성

- 포트폴리오 첫 화면을 Kubernetes·GitOps 운영 플랫폼 설계로 고정
- kind cluster, application namespace, StatefulSet·Deployment·Job, HPA·KEDA, sync wave 시각화
- API, Worker, notification, DLQ, Kafka, PostgreSQL·Pgpool, observability pod inventory와 replica·운영 목적 공개
- local Kubernetes 책임을 EKS, ECR, ALB, MSK, RDS, Secrets Manager, AMP·AMG에 대응한 표 추가
- API, Kafka buffer, Worker, KEDA, PostgreSQL, DLQ, rollout, cache, restore 관측 지점과 판단 기준 연결
- HPA·cache hydration 성능 회복, KEDA 병목 이동, namespace prune 복구를 STAR 형식으로 정리
- Redis queue-depth KEDA 전환과 Kafka Pgpool HA·inline retry 보강을 historical STAR로 분리
- Demo, generic contract, reliability, trade-off, learning, bottleneck, roadmap, 문서 지도를 핵심 경계만 남기도록 압축
- Kafka contract와 backend reliability 세부 설명을 workload·하위 문서 영역으로 이동

## 2026-07-21 푸시 전 평가 반영: 배포 게이트와 첫 화면 정리

- `dev-kafka` image publication을 `ci.yml`의 `publish-dev-kafka-image` job으로 통합하고 `needs: validate` 적용
- exact tested SHA의 candidate digest를 non-root 실행으로 검증한 뒤 12-character SHA tag로 승격
- image 발행 중 branch가 앞서간 경우 오래된 run의 overlay tag commit 차단
- 독립 `dev-kafka-image.yml` 제거, contract test로 validation dependency·digest promotion·branch advance guard 고정
- README 정체성을 이벤트 처리 workload의 Kubernetes·GitOps 운영 플랫폼으로 전환
- 상단에 API latency, consumer lag, replica, persistence 관측, drain, GitOps revision, restore 증거의 의미와 판단 한계를 표로 배치
- KEDA drain 개선과 intake 악화·notification backlog 이동을 대표 운영 판단 사례로 공개
- generic event contract를 플랫폼 검증 workload 설명으로 뒤로 이동하고 backend 기능 확장보다 배포·복구·cloud evidence를 다음 방향으로 지정
- public demo-lite 링크를 대표 v2 시연 위치에서 제외하고 legacy deployment 경계 아래로 이동
- public demo-lite v2 동기화는 current source의 검증·commit·master promotion 뒤 branch overlay 이식과 staged rollout 필요; 현재 미배포
- hot single-stream 결과를 ordering/hot-partition 한계 증거로 한정하고 64-stream fixed Worker/KEDA A/B를 분리 실행
- k6 setup에 `K6_STREAM_COUNT`를 추가하고 VU/iteration을 여러 stream에 균등 배정
- performance suite에 `keda`/`fixed` Worker mode, fixed replica 원복, 실험별 result filename 지원 추가
- 64-stream fixed `2` arm: event `22,125`, p95 `169.24ms`, message/notification peak lag `21,170`/`45`, all drain `301.42s`
- 64-stream KEDA `2→8` arm: event `20,499`, p95 `212.60ms`, message/notification peak lag `18,950`/`11,536`, all drain `261.17s`
- KEDA all drain `13.35%` 감소와 notification backlog 이동 확인; intake event `7.35%` 감소·p95 `25.62%` 증가로 stable 개선 판정 제외
- 성능 회복 후보가 historical stable baseline에 미달하므로 commit·push 계속 보류

## 2026-07-21 성능 회복: generic v2 클린 조건 3회 반복

- 첫 v2 후보 저하를 intake, cache hydration, Worker post-commit publish, benchmark 초기 상태로 분해
- compacted topic fetch response가 kafka-python 기본 network frame `1MiB`를 넘을 때 API cache consumer가 reconnect loop에 들어가는 결함 확인; fetch `50MiB`, receive frame `64MiB` 적용
- materialized cache replay의 중복 JSON 구조 검증 제거, poll batch `200→1000`; cache normalizer 경계의 전체 검증 유지
- Worker DB commit 뒤 status/snapshot/notification 발행 producer의 `linger_ms`를 `0`으로 분리해 직렬 post-commit 대기 축소
- API HPA min `3→6`, scale-up stabilization `60s`·최대 `2 pods/60s`, scale-down stabilization `120s`; full cache replay가 부하 중 동시에 늘어나는 시작 부하 억제
- benchmark suite에 모든 API pod hydration, API/Worker 최소 replica, CPU target, fresh lag `0` 연속 확인 steady-state gate 추가
- destructive local benchmark reset helper 추가: DB event state 초기화, Kafka 6개 topic 재생성, delayed deletion I/O용 `75s` quiet period, Worker/KEDA 복구
- result path 절대화, Windows evidence 교체를 `Move-Item -Force`로 수정, source dirty 여부 기록
- HPA probe pod Ready 대기와 job 종료 판정 보강; CPU current/target과 stabilization 결과 분리 출력
- 100 VU / 30s hot single-stream 클린 실행 3회: event `29,146`/`28,749`/`29,608`, 오류 모두 `0.00%`
- 3회 평균 avg `51.81ms`, p95 `101.27ms`, p99 `140.59ms`; 첫 v2 후보 대비 event `14.93%` 증가, avg/p95/p99 `23.62%`/`18.30%`/`8.17%` 감소
- main Worker drain 평균 `508.58s`, 약 `55.2 events/s`; 첫 v2 후보 약 `32.6 events/s` 대비 `69.3%` 증가
- historical stable legacy baseline에는 미달하며 API floor 변경과 dirty local image 조건 포함; stable baseline 승격 및 Git push 보류
- local unit / contract / infrastructure suite: `363 passed`

## 2026-07-21 수정: PostgreSQL 재시작 복구와 cache readiness 증거

- persisted-volume PostgreSQL StatefulSet 전체 재시작 뒤 chart의 first-boot sync 설정이 재적용되지 않아 `synchronous_standby_names`가 비고 readiness가 `postgres_sync_standbys_below_minimum`으로 남는 결함 확인
- install과 DB recovery 경로에서 모든 ready PostgreSQL pod에 `synchronous_commit=on`, `synchronous_standby_names=ANY 1`을 각각 별도 `ALTER SYSTEM`으로 지속 저장하고 reload하도록 helper 추가
- rollout status가 scale-down 시점의 `0 replicas`를 보고 너무 일찍 완료될 수 있어 target spec/ready replica 명시 대기 추가
- host에서 PostgreSQL administrator password를 decode하거나 command argument에 넣지 않고 pod 내부 Secret file을 사용하도록 monitoring/sync helper 정리
- Windows PowerShell이 multiline Bash 인자의 quote를 깨뜨리는 실제 실행 오류를 확인하고 remote shell/SQL을 각각 Base64 envelope로 전달하도록 수정
- 수동 backup/restore client도 password를 host로 decode하지 않고 pod `secretKeyRef`로 주입하며, SQL을 PowerShell text pipeline으로 재인코딩하지 않도록 pod file과 `kubectl cp` 경계로 변경
- 새 host logical dump `39,433,414` bytes를 disposable database에 복원하고 10개 table row count, Alembic `0008_generic_event_envelope`, generic v2 row `33,840`, message max id/sequence 원본 일치를 확인한 뒤 임시 DB 삭제
- readiness 응답 생성 과정에서 실제 materialized cache `hydrated` 값이 누락돼 response model 기본값 `false`가 반환되는 결함 수정
- API contract에 materialized cache `ready`/`hydrated` 필드와 실제 hydration 완료 확인 추가
- preliminary console rerun `47.5s`, exit `0`: fresh cache age `0.453s`, DB-down degraded cache age `11.784s`, scale `3→0→3` 뒤 PostgreSQL `3/3`·sync/quorum standby `2`·readiness 복귀
- preliminary console DB outage rerun `45.1s`, exit `0`: DB down 중 event `202` 수락, 복구 뒤 persistence와 sync replication 복귀 확인
- tracked recovery rerun: cache fallback `45.390s`(fresh `0.112s`, DB-down `11.462s`), DB outage `43.008s`, 최종 PostgreSQL `3/3`·Pgpool `2/2`·consumer lag `0`; `results/postgres-recovery/latest.json`에 source/script hash와 한계 기록
- benchmark main/post-HPA drain 모두 전체 lag series가 fresh한 서로 다른 Prometheus scrape의 lag `0`을 연속 2회 확인, timestamp-before/after가 같은 표본만 수락, final reset 실패도 `failed-*.txt`를 먼저 기록한 뒤 원래 suite/reset 오류 전파
- benchmark 증거는 같은 directory의 임시 파일을 먼저 완성하고 기존 파일은 `File.Replace`, 신규 파일은 `File.Move`로 반영, 쓰기 실패도 별도 보관해 suite → reset → evidence-write 순서로 원래 오류 우선순위 유지
- restore `-ResetSchema`의 psql 종료 코드를 즉시 확인해 schema reset 실패 뒤 dump 적용이 이어지는 경로 차단
- reset recovery의 StatefulSet scale/rollout native exit code를 즉시 검사해 조용한 kubectl 실패 차단
- `dev-kafka` CI/image workflow success 뒤 image `9349ba9`, overlay revision `b84c379`을 Argo `Synced / Healthy`로 배포; API/core ready, cache `ready=true` / `hydrated=true`, API contract pass, consumer lag `0` 확인
- master merge `8f5d78c`와 CI run `#55` validate/publish success, candidate digest UID `10001` 검증 뒤 image `8f5d78c6963a` 승격, overlay bot commit `717e0ca` 완료; local Argo는 `dev-kafka` 추적 유지
- public demo-lite live GET 재확인: title `Post-Order Event Console`, UI `1.4.1`, API `1.0.0`, image `e481a21`, generic v2 route 없음, order event success `200`; local `2.0.0`과 배포 경계 유지
- local unit / contract / infrastructure suite: `359 passed`

## 2026-07-21 검증: generic v2 첫 성능 후보

- local `dev-kafka` revision `1439be1`, API/Worker image `d31ac14`, OpenAPI/API `2.0.0`, generic v2 gate와 시작 consumer lag `0` preflight 통과
- 100 VU / 30s hot single-stream 부하: total HTTP `25,382`, event `202` `25,378`, error `0.00%`, avg `67.83ms`, p95 `123.96ms`, p99 `153.10ms`
- same-stream 100 event ordering `stream_seq 1..100` pass, `7.93s`
- persistence sample 50/50 확인; `accepted_to_status_observed_ms` avg `79.96ms`, p95 `81.28ms`, max `2384.10ms`
- status-observed latency에 client polling/network 포함; 과거 row-visible proxy와 비교 제외
- message-worker peak lag `24,504`, notification-worker peak lag `6`, main backlog `751.76s` 뒤 모두 `0`
- HPA sanity probe가 main drain 뒤 추가한 message-worker lag `4,962`를 후속 수동 관측해 약 `160s` 내 `0` 확인
- Worker histogram query `60s`는 metric의 최대 finite bucket 경계 포화로 확인; exact p95 해석 제외
- stable legacy baseline 대비 total requests `19.87%` 감소, avg/p95 `53.70%` 증가, p99 `47.82%` 증가
- 2026-06-18 마지막 legacy raw suite 대비 total requests `8.68%` 감소, avg `17.68%`, p95 `3.92%`, p99 `1.66%` 증가
- 첫 generic v2 후보로 기록하고 stable baseline 승격 제외; fresh cluster clean state와 현재 resource 조건이 함께 달라 v2만의 인과로 단정 제외

## 2026-07-21 업데이트: v2 벤치마크 관측·검증 계약 보강

- 새 consumer group의 미사용 Kafka partition을 kafka-exporter가 lag `-1`로 노출하는 동작 확인
- 실제 양수 backlog가 `-1` sentinel에 상쇄되지 않도록 partition별 `clamp_min(..., 0)` 적용 후 합산하도록 status script, benchmark suite, Prometheus alert, Grafana panel 통일
- benchmark preflight에 workload rollout, API/Worker image 일치, generic v2 gate, OpenAPI `2.0.0`, 시작 consumer lag `0` 검증 추가
- k6 전체 check 성공 threshold 추가, suite의 threshold 무시 제거, Job timeout 명시 실패 처리
- 실패한 suite는 별도 `failed-*.txt`에 기록하고 마지막 성공 `latest.txt` 보존
- Worker `messaging_event_persist_lag_seconds`의 5분 p95를 benchmark 결과에 추가
- 비회원 stream read의 existence-oracle 방지 `404` 정책과 배포 계약 스크립트 기대값 정렬
- Windows PowerShell 5.1의 IE parser 의존 `Invoke-WebRequest`가 headless 환경에서 `NullReferenceException`을 내지 않도록 운영 스크립트 전체에 `-UseBasicParsing` 적용
- Prometheus/Grafana source config SHA-256 축약 hash를 pod template에 연결해 ConfigMap 변경이 실제 process rollout으로 이어지도록 보강

## 2026-07-21 업데이트: Argo CD Namespace prune 방지

- 기존 GitOps revision이 추적하던 `messaging-app` Namespace가 새 desired state에서 빠지면서 automated prune 대상이 되는 전환 결함 확인
- Namespace 삭제가 PostgreSQL/Pgpool, runtime secret, backup PVC 등 namespace-scoped bootstrap 리소스까지 연쇄 삭제하는 영향 확인
- 같은 kind cluster에서 PostgreSQL/Pgpool clean reinstall; 삭제된 in-cluster local demo row와 backup PVC는 복구 불가, 성능 suite는 새 DB clean state에서 실행
- reinstall 뒤 수동 `postgres-weekly-backup` Job `Completed`와 `/backups/postgres-20260720-190134.sql` 생성 확인; 새 `postgres-backups` PVC `Bound`, 이 시점의 restore는 미실행이었으며 후속 disposable restore 결과는 최신 항목에 기록
- `k8s/gitops/base/namespace.yaml`을 desired state에 유지하고 `argocd.argoproj.io/sync-options: Prune=false` 적용
- Namespace lifecycle을 application rollout과 분리하는 contract test 및 GitOps 운영 규칙 추가

## 2026-07-21 업데이트: 기존 DB의 generic v2 migration 호환성 수정

- `master`를 개발·검증 브랜치 `dev-kafka`에 병합하고 generic v2 staged GitOps manifest 반영
- 기존 cluster의 Alembic `version_num VARCHAR(32)`가 긴 `0006` revision ID를 저장하지 못해 migration wave가 중단되는 문제 확인
- `0006` migration이 revision 갱신 전에 `alembic_version.version_num`을 `VARCHAR(64)`로 확장하도록 수정
- migration 실패 transaction rollback과 wave 차단을 확인해 기존 DB `0004`, 구 Worker/API, Kafka event 상태 보존
- 회귀 테스트에 version column 확장 계약 추가

## 2026-07-14 업데이트: 범용 이벤트 처리 시스템 정체성 전환

목표:

- 포트폴리오 핵심을 `Kafka 기반 고신뢰 이벤트 처리 시스템`으로 재정의
- 주문·결제 lifecycle을 범용 event contract의 reference scenario로 분리
- 기존 client, Kafka offset, database state, 배포 리소스를 깨뜨리지 않는 expand-contract 적용

변경 내용:

- 범용 `POST /v2/streams/{stream_id}/events` 추가: client는 `event_type`, JSON `payload`, JSON `metadata`를 보내고 API가 `schema_version=2` 부여
- Worker가 legacy body/order envelope와 새 generic envelope를 함께 읽고 PostgreSQL row, request status, snapshot에 범용 필드 반영
- Alembic `0008`에서 `messages`에 `schema_version`, `payload`, `metadata` column 추가, legacy `body`/`category`/`payment_id` backfill, JSON object·schema version range·v2 generic event type/envelope constraint 검증
- `0008` downgrade는 legacy column으로 정확히 재구성할 수 없는 schema/payload/metadata row가 하나라도 있으면 중단해 구조화 데이터 손실을 방지
- `/v1/orders/{order_id}/events`를 order reference compatibility adapter로 유지
- legacy body-only stream route와 `body`, `category`, `payment_id`를 기존 client·historical evidence 호환 경계로 유지
- GitOps rollout 순서 고정: `messaging-env` Secret wave `-3` → 일반 Sync migration Job wave `-2` → dual-read/dual-write Worker wave `-1` → API wave `0`
- base/app Secret gate `false` 유지, `local-ha` overlay가 API container에만 `true` override 추가; migration Job의 runtime secret 의존 제거
- 비대칭 호환 경계 공개: 구 Worker가 v2 job을 처리하면 legacy body preview만 저장하고 JSON `payload`/`metadata`는 보존하지 못함
- 수동 local rollout gate 추가: app manifest `GENERIC_EVENTS_V2_ENABLED=false`, Worker 준비 뒤 quick start가 API env를 `true`로 변경
- v2 read alias 추가: request status와 event list; 인증·stream 생성은 공유 `/v1` resource API 유지
- generic envelope에 finite JSON, NUL 제외, payload/metadata 각 최대 64단계 container depth와 byte-size 검증 적용
- HTTP body를 JSON parsing 전 기본 `1 MiB`로 제한하고 declared/chunked oversize request를 `413`으로 종료
- username/password/stream/event/DLQ 입력의 NUL·lone surrogate를 거부하고 JWT segment·claim·signature·길이 경계를 fail-closed 처리
- non-local의 기본값·known placeholder·빈 값·32-byte 미만 auth secret을 readiness와 business API에서 차단
- schema startup 전 business API 직접 호출을 `503`으로 차단하고 cache fallback은 DB availability 예외에만 허용
- manual/automatic DLQ replay identity와 counter를 exact integer/PostgreSQL BIGINT 범위로 고정하고 overflow·형 변환을 거부
- Worker가 ingress route·stream id·Kafka record key 일치를 검증하고 decode/depth/key 위반을 terminal invalid DLQ로 격리
- invalid ingress DLQ는 broker-limit poison을 피하도록 bounded diagnostic(size, SHA-256, 1KiB base64 preview)만 보존
- Worker idempotency를 actor-scoped key로 격리하고 같은 owner·stream의 완전한 legacy response만 승계; malformed cached response는 정상 persistence로 복구
- request status owner alias 충돌·누락·비-object 상태를 fail-closed 처리하고 Demo UI가 저장된 payload/metadata 전체를 재검증
- request status write owner를 DB conditional upsert와 Kafka publish gate 양쪽에서 고정하고 request_id message identity 충돌을 terminal 거부
- notification payload를 PostgreSQL BIGINT/metadata/preview 경계로 normalize하고 실제 persisted message target이 있을 때만 attempt 기록
- materialized cache가 compacted topic key와 request/event/stream payload identity·owner·membership schema를 검증한 뒤 반영
- materialized cache consumer가 group offset을 공유하지 않고 각 API pod에서 모든 partition을 beginning부터 replay하며, startup 시 캡처한 initial end offset 도달 뒤 `hydrated` / `ready` gate를 여는 실제 경계 문서화
- stream read는 initial hydration 전 cache 사용 제외, DB 정상 시 PostgreSQL membership과 latest sequence watermark에 연속으로 일치하는 fresh snapshot만 사용, DB 장애 시 hydrated membership/message cache가 함께 있을 때만 degraded fallback
- `snapshot consumer group lag` 표현 제거: 현재 미구현인 pod별 position/end-offset/remaining record/hydration duration custom metric으로 개선 범위 조정
- request/message별 unique key 증가를 compaction만으로 제한할 수 없는 replay growth 공개; retention, DB bootstrap+Kafka changelog, per-stream latest-page snapshot을 완료 기준이 있는 roadmap 항목으로 추가
- Demo UI `2.0.0`: `Reliable Event Processing Console` 정체성, order lifecycle `Reference Scenario` 표시, generic v2 event 전송, envelope persistence evidence 표시
- README, Demo Guide, Operations, Architecture, Service Requirements에서 generic core와 reference adapter 경계 정렬
- `message-*` Kafka topic, `message-worker` consumer group, `messaging-app` namespace, `rooms`/`messages` table 같은 물리 식별자 유지

신뢰성 표현:

- 포함: per-stream partition ordering, inline retry, explicit offset commit, PostgreSQL idempotent persistence, DLQ/replay, 관측·복구 검증
- 제외: exactly-once, partition 간 global ordering, 모든 장애에서의 무손실, production SLA
- 남은 gap: DB commit 이후 status/snapshot/notification best-effort publish와 unresolved DLQ state model

버전과 배포 경계:

- 현재 `master` source 예상 조합: Demo UI `2.0.0`, API `2.0.0`
- 호환 범위의 runtime/tool pin 정렬: Python `3.11.15`, Helm `3.21.3`, CI kubectl `1.36.2`, RDS PostgreSQL `16.14`
- Kafka client, pytest, GitHub Actions, AWS provider와 Terraform module의 major upgrade는 일괄 변경에서 제외하고 별도 contract/integration/plan 검증 항목으로 이관
- Docker Python base를 tag+digest로 고정하고, master publish는 candidate digest를 비루트 실행 검증한 뒤 같은 digest에 release/bootstrap tag를 부여하도록 변경
- stream 미존재·actor 미존재·membership 누락·request identity 충돌의 Worker 외부 실패 문구를 통일하고 상세 원인은 내부 log에만 기록; 동기 read/read-receipt API도 resource 미존재와 비회원을 같은 `404`로 처리
- Linux prerequisite가 기존 kind/kubectl/Helm의 실제 버전을 pin과 비교한 뒤 불일치 시 교체하도록 보강하고, `.sh`는 `.gitattributes`에서 LF로 고정
- Terraform provider lock에 `windows_amd64`와 CI용 `linux_amd64` checksum을 함께 기록하고 격리된 Terraform `1.15.8` 환경에서 fmt/init/validate 재통과
- 2026-07-14 local live 확인: Demo UI `1.1.0`, event response `200`
- 문서에 등록된 public demo-lite: branch/deployment 전용 Demo UI `1.4.1`, API image `e481a21`, event response `200`
- 이번 `master` source 변경: local live와 public demo-lite 모두 미배포
- 기존 2026-06 성능 수치와 아래 historical order-domain 기록: 당시 contract와 실행 결과 그대로 보존
- generic v2 performance: 미실행; legacy/order baseline을 v2 측정값으로 재사용 제외

검증 상태:

- `.venv\Scripts\python.exe -m pytest -q`: `351 passed`
- Python compileall과 dependency consistency check 통과
- 새 `202` performance, staged cluster rollout, migration/canary evidence는 아직 재실행 전
- 2026-07-14 감사의 `115 passed`는 이 정체성 전환 이전 baseline으로 유지

## 2026-07-14 업데이트: 전체 정합성 감사와 신뢰성 경계 보강

감사 범위:

- API contract, Kafka Worker 처리, PostgreSQL persistence, DLQ, readiness, observability, GitOps, AWS blueprint, 검증 원본 전수 대조
- 현재 구현과 README / docs / `.env.example`의 앞뒤가 맞지 않는 표현 정리
- 기존 작성 중이던 Worker autoscaling 역사와 판단 기준 유지

변경 내용:

- event intake 성공 계약을 `202 Accepted`로 명시하고 2026-06 status `200` 원본을 pre-contract-fix historical evidence로 분리
- Worker poll batch의 성공 record 단위 offset commit과 실패 partition seek-back 경계 보강
- validation rejection을 성공 처리량과 분리해 `rejected` 결과로 기록
- `event_type`, `category`, `payment_id`의 Kafka payload, PostgreSQL row, status, snapshot persistence 경계 정합화
- notification publish를 DB commit 이후 best-effort 단계로 명시하고 transactional outbox gap 공개
- 2026-06 PowerShell `accepted-to-persisted` 수치를 DB row `created_at` / row-visible proxy로 정정
- 현재 PowerShell output을 `accepted_to_status_observed_ms`로 변경해 polling/network 포함 client 관측 지연임을 명시
- Worker histogram을 PostgreSQL `commit()` 반환 직후 `persisted_at` 기준으로 변경하고 post-commit publish 시간과 분리
- DLQ list / summary를 append-only Kafka log sample로 정정하고 unresolved depth / SLO age 표현 제거
- `.env.example`에서 Redis-era 변수를 제거하고 현재 PostgreSQL / Kafka / snapshot / notification 설정으로 교체
- `results` 최신 원본을 Git 추적 대상으로 전환하고 provenance guide 추가
- README를 실제 서비스 경계, 안정 baseline, 마지막 raw suite, 현재 limitation 중심으로 재구성
- `docs/IMPROVEMENT_ROADMAP.md`에 P0~P2 우선순위와 측정 가능한 완료 기준 추가
- AWS Terraform skeleton의 구현 범위와 production hardening gap을 문서에 명시
- Demo UI `1.2.1`: 운영 refresh 30/60초, token 재사용, stream persistence summary 3초 polling
- Demo UI `1.2.1`: `send_failed` / 일부 미확인 종료 상태, 구조화 DB 컬럼 evidence panel
- Demo UI `1.2.1`: authenticated user-filtered DLQ recent log detail과 manual replay
- Demo UI `1.2.1`: 좁은 운영 패널에서 reset confirmation 문구가 잘리지 않도록 위험 작업 control을 한 열로 정렬
- DLQ API: `scope=recent_log_sample`, `user_filtered=true`, `oldest_sample_age_seconds`로 의미 고정
- DLQ Replayer: poll batch마다 PostgreSQL reachability 재확인, 성공/terminal record만 explicit offset commit
- Prometheus: headless DNS discovery로 API/Worker/notification-worker/DLQ Replayer replica별 scrape, required target missing과 notification lag alert 추가
- master GitOps: 전체 validation 뒤 GHCR 12-character SHA image 발행, overlay bot commit, Argo CD 추적 경계 구현
- AWS blueprint: EKS private endpoint default와 제한 CIDR validation, ECR immutable tag, RDS generated secret consistency 보강
- Terraform `1.15.8` 공식 SHA256 검증, required version / provider / root module pin 정렬, lock file 생성, local fmt/init/validate 성공; plan/apply/AWS 배포는 미실행으로 분리
- runtime secret: Windows/Linux Grafana 고정 admin password 제거, random secret 생성과 평문 출력 제외
- PostgreSQL HA: committed password 제거, chart-managed Secret 생성·upgrade 재사용·PVC 유지 시 credential recovery 경계 명시
- PostgreSQL HA: chart가 무시하던 `numSynchronousReplicas` 제거, fresh-boot container env를 `ANY 1 of 2 standbys`로 정렬; persisted-volume restart 보장은 2026-07-21 runtime helper로 후속 보강
- application / Alembic: 과거 로컬 DB 기본 암호 제거, runtime Secret 또는 명시적 연결 설정만 사용
- 미배포 legacy `observer` 제거, 현재 운영 surface를 Demo UI / Grafana / Prometheus로 정리
- repository hygiene: tracked local binaries/Helm metadata/unused Redis chart 제거, PostgreSQL HA vendored chart archive만 의도적 유지

검증 원칙:

- 최종 local suite: `.venv\Scripts\python.exe -m pytest -q` → `115 passed`
- Python compileall, dependency check, Alembic single head, PowerShell/Bash syntax, Kustomize, Helm, Prometheus, Terraform validate, Docker build/non-root 실행 검증 통과
- read-only live check: API/core workload ready, Argo CD `OutOfSync / Healthy`, `notification-worker` lag series 미검출; 이번 source 변경은 미배포 상태로 분리
- HTTP `202` 성능 수치는 새 build rerun 전까지 미기재
- stable Kafka intake baseline은 2차 `31,676` 결과 유지
- 사용자 화면 변경 반영, Demo UI version `1.2.1`

## Historical updates

아래 항목은 당시 branch와 구현 상태를 기록한 역사입니다. 이후 변경으로 보강된 내용은 위 최신 항목과 현재 architecture 문서를 우선합니다.

역사적 성능 항목의 `event status 200`은 현재 `202 Accepted` route 계약 이전 원본입니다. 과거 `accepted-to-persisted` 값은 PostgreSQL row `created_at` / row-visible proxy이며 DB commit timestamp 측정값이 아닙니다.

## 2026-06-27 업데이트: 핵심 문서 불렛형 정리

변경 내용:

- `AGENTS.md`: 불렛 문서 끝맺이 기준 추가. `~합니다`형보다 `~ 확인`, `~ 대기`, `~ 분리`, `~ 유지`형 우선
- `README.md`: 포트폴리오 입구 역할에 맞춰 핵심 요약, 데모, 검증, 문서 지도를 불렛형으로 재정리
- `docs/DEMO_GUIDE.md`: 데모 URL, 실행 순서, 카운터 의미, reset 동작 중심 정리
- `docs/GITOPS.md`: 목적, 구성 요소, sync 전략, 확인 명령 중심 정리
- `docs/AWS_IAC_PLAN.md`: AWS managed service mapping과 Terraform 구조 중심 정리
- `docs/SERVICE_REQUIREMENTS.md`, `docs/ARCHITECTURE.md`, `docs/RELIABILITY_POLICY.md`: 서비스 기준, 구조 경계, readiness 판단 기준 불렛형 정리
- `docs/OBSERVABILITY.md`, `docs/RUNBOOK.md`, `docs/OPERATIONS.md`, `docs/METRICS_REFERENCE.md`: 운영 신호, 장애 절차, 지표 해석 문장 축약
- `docs/KAFKA_EXPERIMENT.md`: `단순히` 표현 제거, Kafka append path 분리 기준 직접 표현

## 2026-06-24 업데이트: README 소개 문구 톤 조정

변경 내용:

- `AGENTS.md`에 영어 문서와 답변의 상투적인 부정-대조 구문을 금지하는 규칙을 추가했습니다.
- README의 `What To Look For` 문구에서 AI가 쓴 듯한 분류형 표현을 줄였습니다.
- 설계 / 파이프라인 / 운영 설명을 더 직접적인 데모 안내 문장으로 바꿨습니다.
- "받았다"와 "저장됐다"를 구분하는 지점을 README 상단에서 바로 보이게 했습니다.
- Operations Advisor를 규칙 기반 위험 및 해결 알림으로 설명해 데모에서 봐야 할 핵심 포인트를 더 분명하게 했습니다.

## 2026-06-24 업데이트: README 흥미 유도 섹션 보강

변경 내용:

- README 상단에 `What To Look For` 섹션을 추가했습니다.
- 설계, 파이프라인, 데모, 운영, 확장 관점에서 이 프로젝트를 왜 봐야 하는지 짧게 설명했습니다.
- README가 데모와 상세 문서로 이어지는 포트폴리오 입구 역할을 하도록 조정했습니다.

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

기록 범위:

- 당시 변경은 `demo-dev` / `demo-lite` 계열에 먼저 적용
- `master`의 현재 구조화 persistence 반영은 2026-07-14 감사 항목에서 기록

목표:

- Kafka append 이후 Worker가 PostgreSQL에 어떤 컬럼으로 저장하는지 데모 화면에서 바로 확인할 수 있게 한다.
- 당시 order reference event를 분석 파이프라인으로 확장할 수 있도록 `messages` table에 구조화 컬럼을 추가한다.

변경 내용:

- `messages` table에 `event_type`, `category`, `payment_id` 컬럼과 분석 조회용 index를 추가했다.
- Worker persistence가 Kafka payload의 `event_type`, `category`, `payment_id`를 DB row, request status, snapshot payload에 함께 반영한다.
- 데모 화면 결과 패널에 `DB 저장 컬럼` / `Stored DB Columns` 섹션을 추가했다.
- 최근 DB row 목록은 raw 데이터 노출과 화면 복잡도를 줄이기 위해 표시하지 않는다.
- 데모 UI 변경에 맞춰 화면 버전을 `1.0.2`로 올렸다.
- `docs/DEMO_GUIDE.md`에 DB storage evidence 설명을 추가했다.
- `AGENTS.md`에 README와 `docs/` 변경도 관련 브랜치에 공유해야 한다는 운영 규칙을 추가했다.

해석:

- 포트폴리오 시연 한 화면에서 Kafka 처리 흐름과 분석 가능한 DB 저장 구조를 함께 설명할 수 있다.
- 이 구조는 향후 batch export, CDC, warehouse load, 운영 통계 대시보드 같은 데이터 분석 파이프라인으로 이어질 수 있다.

## 2026-06-21 업데이트: 2코어 서버용 demo-lite 프로파일 추가

목표:

- 2코어 2스레드급 서버에서 포트폴리오 데모를 실행할 수 있는 축소 profile을 제공한다.
- 기존 full-ha 기준은 유지하고, 저사양 서버에서는 API -> Kafka -> Worker -> DB 흐름 시연에 집중한다.
- demo-lite 결과가 full-ha Kafka baseline과 섞이지 않도록 문서 경계를 둔다.

변경 내용:

- `demo-lite` 브랜치를 만들고 저사양 서버용 설정 분리
- demo-lite 전용 kustomize overlay, PostgreSQL lite values, quick start, k3s 배포 스크립트 분리
- demo-lite k3s 서버에서도 Argo CD가 `demo-lite` 브랜치를 동기화하는 경로 추가
- `k8s/scripts/install-ha.ps1`에 `-ValuesFile` 파라미터 추가
- README, `docs/DEMO_GUIDE.md`, `docs/DEMO_LITE.md`, `docs/OPERATIONS.md`에 full-ha와 demo-lite 차이 정리
- 현재 `master`에서는 demo-lite 전용 파일을 실행 경로로 보지 않고, 브랜치 전용 기록으로 해석

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
- `Kafka 적재`와 `DB 저장`을 분리해 API append 성공과 Worker persistence 완료의 단계 차이를 보여줍니다.
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

Worker 스케일링 변경:

- 최초 기준: API와 Worker 모두 CPU 사용률 기반 HPA
- Redis 단계: Worker를 queue depth 기반 KEDA로 전환
- Kafka 단계: queue depth 기준을 Kafka consumer lag 기준으로 교체
- 현재 trigger: topic `message-ingress`, consumer group `message-worker`
- 현재 local demo 기준: lag threshold `100`, Worker min `2`, max `8`

변경 이유:

- CPU가 낮아도 DB connection, lock, commit 대기 중에는 미처리 이벤트가 증가
- CPU 사용률로는 Kafka ingress rate와 Worker persistence rate의 차이를 직접 확인하기 어려움
- consumer lag로 Worker가 아직 처리하지 못한 이벤트 수 확인
- API는 CPU HPA 유지, Worker는 업무 backlog에 맞춘 별도 확장 기준 적용

검증 기준:

- API 요청 수보다 consumer lag 증가와 감소 추이 우선 확인
- accepted-to-persisted latency와 backlog drain time 함께 확인
- KEDA desired replica와 실제 Worker replica 비교
- fixed Worker와 KEDA의 직접 성능 비교 실험으로 해석하지 않음

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
- DB commit 이후 snapshot은 `message-snapshots` / `stream-snapshots` compacted topic으로 발행하고, API는 DB authorization과 sequence watermark로 검증한 read 및 DB 장애 fallback에 local materialized cache를 사용한다.
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
