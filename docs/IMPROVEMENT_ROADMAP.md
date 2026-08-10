# Improvement Roadmap

이 문서는 현재 포트폴리오의 다음 투자 순서를 정의합니다. 완료 여부는 코드 존재보다 재현 가능한 장애 주입과 원본 증거로 판단합니다.

## Immediate Direction — 2026-08-10

현재 투자 순서:

1. **offset 장애 경계 검증**: notification batch와 core Worker의 DB commit 직후 강제 종료, consumer group rebalance, idempotent replay 확인
2. **신뢰성 gap 제거**: accepted-state read model과 notification transactional outbox 구현·장애 주입
3. **지속 가능 처리량 확정**: 15분 이상 일정 입력률에서 lag가 증가하지 않는 최대 처리량, DB pool·lock·commit latency 동시 측정
4. **복구 지점 자동화**: object storage 복제, cluster-loss 복구, scheduled dump 무결성 검사, 정기 restore drill과 RPO/RTO 기록

2026-08-10 notification batch candidate의 clean 64-stream A/B는 fixed `2`와 KEDA `2→4`를 각각 3회 실행했습니다. KEDA backlog 처리율은 `13.38%` 증가했고 평균 drain은 `12.78%` 감소했습니다. KEDA p95 평균은 `6.49%` 증가했습니다. 반복 범위는 분리됐지만 dirty local image 조건이므로 stable release baseline은 유지하지 않습니다.

완료된 선행 작업:

- API `2.1.0` 단순화 image build·non-root smoke·local cluster 동일 image rollout
- generic v2 contract, PostgreSQL read, `/ops/summary`, ordering·DB outage suite 재검증
- notification Worker 독립 KEDA 추가, core `2→4`·notification `1→2` 상한 적용
- benchmark preflight에 API·core Worker·notification Worker image 일치와 두 consumer lag `0` gate 적용
- fixed/KEDA 각 3회 clean A/B, 6개 원본과 latency trade-off 기록
- notification batch transaction, core sequence·authorization DB roundtrip 축소
- Public demo-lite 신규 서버 배포: UI `2.3.1`, API `2.1.0`, event `202`, Argo `Synced / Healthy`
- Public 저사양 Worker scaling: lag peak `828`, desired·actual replica `1→2`
- `dev-kafka` image publication validation gate와 SHA image 검증
- `demo-dev` UI `2.3.0` source·test 후보: `368 passed`

## P0 — 데이터 유실 경계와 API 계약

### 1. Kafka offset commit 안전성

- 현재 상태: 로컬 구현 완료, 배포 image 기준 crash / rebalance 장애 주입 재검증 대기
- 목표: 처리 완료 record까지만 partition offset commit
- 이유: poll batch 일부 처리 후 process crash 또는 rebalance가 발생해도 미처리 record 유실 방지
- 완료 기준:
  - batch 첫 record 처리 직후 Worker 강제 종료 실험
  - 재기동 뒤 accepted 수와 persisted 수 일치
  - missing `0`, 허용 범위를 벗어난 duplicate `0`
  - partition별 committed offset과 DB row 결과를 함께 보존

### 2. HTTP `202 Accepted` 계약 재검증

- 현재 상태: local `dev-kafka` image `d31ac14`의 OpenAPI/API `2.0.0`과 performance 원본에서 event `202` `25,378건`, 다른 event status `0`, 오류 `0.00%` 확인. append 직후 status `404` race의 accepted-state 개선은 대기
- 목표: Kafka append 성공 응답을 route, OpenAPI, 테스트에서 `202`로 고정
- 이유: 비동기 persistence 경계를 클라이언트 계약에 정확히 표현
- 완료 기준:
  - 두 event intake endpoint의 `202` contract test 통과
  - 새 이미지 기준 functional suite 재실행
  - 성능 원본에서 event status `202` 확인
  - 2026-06 status `200` 자료는 역사적 pre-contract-fix 결과로 유지
  - append 직후 Worker status row 생성 전의 짧은 `404`를 계약으로 명시하거나 accepted-state read model로 제거

### 3. DB commit 이후 notification 발행 신뢰성

- 목표: notification job 발행에 transactional outbox 또는 동등한 복구 경계 적용
- 이유: DB commit 뒤 process crash 시 Kafka 후속 event 누락 가능성 제거
- 완료 기준:
  - DB commit 직후 Kafka publish 전 강제 종료 실험
  - 재기동 뒤 notification job의 최종 발행 확인
  - 중복 발행 시 consumer idempotency 확인
  - outbox backlog, retry, terminal failure 운영 지표 제공

### 4. DLQ 현재 상태 모델

- 목표: append-only DLQ 이력과 unresolved incident 상태 분리
- 이유: 최근 log sample을 현재 backlog 또는 SLO age로 오해하는 문제 방지
- 완료 기준:
  - unresolved / replayed / blocked 상태와 전이 기록
  - 현재 unresolved depth와 oldest unresolved age 제공
  - replay 성공 뒤 resolved 상태 확인
  - partition 전체를 고려한 조회와 pagination 검증
  - replay lease 만료/최종화 실패 뒤 Kafka 중복 재발행을 주입하고 DB idempotency와 운영 로그 중복 허용 범위 확인

### 5. Generic v2 staged rollout gate

- 현재 상태: GitOps gate-false Secret `-3` / 일반 Sync migration `-2` / Worker `-1` / overlay API-true `0` wave source 구현. local `dev-kafka`에서 migration `0008`, 동일 image API/Worker rollout, API gate `true`, v2 canary persistence까지 1회 확인; 중간 단계 rollback/forward-recovery drill은 대기
- 목표: `0008` → 새 Worker 전체 rollout → API v2 공개 순서를 배포 시스템에서 강제
- 이유: 구 Worker가 v2 job을 처리하면 body preview만 저장되고 JSON `payload`/`metadata` 유실
- 완료 기준:
  - GitOps migration hook → Worker → API wave 순서 실제 sync 재현
  - 수동 local gate `false` → Worker ready → API gate `true` 재현
  - Worker image/version rollout 완료 확인
  - 구 Worker replica가 남아 있으면 v2 route/traffic 차단
  - v2 canary의 Kafka envelope, PostgreSQL row, request status JSON 일치
  - 중간 단계 rollback과 forward recovery drill 기록

### 6. API state 경계 단순화 — source·local 검증 완료

- API pod별 compacted topic full replay 제거
- request status와 event read를 PostgreSQL source of truth로 통합
- readiness에서 cache·Worker 운영 정보 제거
- Worker 운영 정보는 15초 캐시가 있는 `/ops/summary`로 분리
- current source contract, 새 image, ordering·DB outage, hot-stream 3회와 64-stream KEDA 후보 재검증 완료
- registry publication과 GitOps release는 별도 승인·배포 단계

## P1 — 용량 측정과 지속 가능 처리량

### 7. Commit-aware persistence latency

- 현재 상태: Worker가 `commit()` 반환 직후 `persisted_at`을 기록하는 histogram을 배포해 cluster query까지 확인. 첫 결과 `60s`는 당시 최대 finite bucket 포화값이라 exact p95에서 제외. `1200s`까지 확장한 bucket은 local image `9349ba9`의 `/metrics`에서 노출 확인, 새 bucket을 사용한 반복 측정 대기
- 목표: API accepted 시각부터 Worker가 DB commit 반환을 관측한 시점까지의 지연을 재현 가능하게 측정
- 이유: 과거 PostgreSQL `created_at` / row-visible proxy와 client status-observed 측정의 의미 한계 해소
- 완료 기준:
  - Worker commit 관측값 계측과 cluster 원본 보존
  - histogram 정의, 단위, clock source 문서화
  - historical row-visible proxy, current client status-observed, Worker commit-observed 지표를 동일 workload에서 비교

### 8. Fixed Worker 대 KEDA A/B 실험 — candidate 완료

- 현재 상태: notification batch candidate에서 fixed core `2`와 KEDA core `2→4`·notification `1→2` 각 3회 완료. fixed 평균 drain `222.49s`, KEDA `194.05s`; backlog 처리율 `13.38%` 증가, p95 `6.49%` 증가
- 목표: 같은 입력과 DB 조건에서 fixed replica와 lag-based KEDA 비교
- 이유: API intake 성능과 Worker persistence capacity 분리
- 완료 기준:
  - workload, partition, DB pool, image, 초기 backlog 동일
  - peak consumer lag, API queued-at-to-commit p95, drain time, DB throughput 기록
  - 최소 3회 반복과 편차 공개 — 완료
  - API request count를 Worker scaling 효과의 단독 근거로 사용하지 않음

### 9. 지속 가능 용량과 backpressure

- 현재 상태: 2026-08-05 hot-stream 3회 평균 event `33,201`, peak message-worker lag `31,422`, main drain `364.62s`, 최종 lag `0` 확인. 약 `1,107 events/s` intake가 이 조건의 지속 가능한 DB persistence 처리율보다 높다는 신호이며 steady-state 한계는 미측정
- 목표: burst intake와 장시간 안정 처리 용량 구분
- 완료 기준:
  - 30초 burst와 15분 이상 steady-state workload 분리
  - lag가 계속 증가하지 않는 최대 입력률 산출
  - overload 시 수락 정책과 운영 대응 기준 정의

## P1 — 범용 계약과 호환성

### 10. 범용 event envelope 증거

- 현재 상태: generic schema/migration/Worker/API/UI 구현, local DB migration `0008`, staged rollout, v2 contract/persistence, same-stream ordering, clean hot-stream performance 3회, 64-stream A/B 1회 확인. 서로 다른 reference domain, A/B 반복, registry image 안정 성능은 대기
- 목표: `schema_version`, `event_type`, JSON `payload`, JSON `metadata`를 Kafka payload와 PostgreSQL row에서 일관되게 유지
- 완료 기준:
  - migration, persistence, API response contract test 통과
  - envelope version, object type, byte-size validation 규칙 문서화
  - `0008` → 새 dual-read/dual-write Worker 전체 rollout → API v2 순서 검증
  - v2 API + 구 Worker 조합에서 structured data fidelity가 깨지는 negative test와 deployment gate 구현
  - 서로 다른 두 reference domain의 DB 조회 결과 보존

### 11. Reference adapter와 호환 종료 기준

- 현재 상태: 주문 lifecycle을 reference scenario로 재배치하고 `/v1/orders/{order_id}/events` compatibility adapter 유지. Local `dev-kafka`의 UI/API `2.0.0` 배포와 generic v2 OpenAPI 확인 완료; route 사용량 계측과 종료 정책은 대기
- 목표: generic core와 domain adapter를 명확히 분리하고 legacy 계약의 관측 가능한 종료 조건 정의
- 완료 기준:
  - README, OpenAPI, demo의 generic v2 계약 일치
  - order adapter를 deprecated compatibility surface로 표시
  - v1/v2 route 사용량 metric과 deprecation 기간 정의
  - API v2 공개 전 구 Worker replica `0`을 확인하는 rollout gate
  - 순서대로 rollout한 뒤 legacy/v2 envelope의 missing/duplicate `0`과 `payload`/`metadata` 일치
  - event acceptance와 DB persistence 상태 분리 유지

## P2 — 배포, 보안, 재해 복구

### 12. Registry 기반 GitOps 공급망

- 현재 상태: `dev-kafka` Actions image `9349ba9`와 overlay revision `b84c379`의 local Argo `Synced / Healthy` 배포 확인. Master merge `8f5d78c`도 CI run `#55` validate/publish success, candidate digest 비루트 UID `10001` 검증, image `8f5d78c6963a` 승격, overlay bot commit `717e0ca`, provenance/SBOM 생성까지 완료. Local Argo는 `dev-kafka` 추적 유지
- 목표: commit SHA image를 registry에 발행하고 overlay tag 변경으로 배포
- 완료 기준:
  - build/push, manifest tag update, Argo CD sync 흐름 재현
  - `kubectl kustomize` 결과의 모든 app workload가 registry SHA tag 사용
  - local kind image load 경로와 GitOps 경로 분리
  - GHCR public/pull secret 정책과 Actions bot의 protected-branch 권한 확인
  - production overlay는 tag 외 image digest 고정, SBOM과 critical vulnerability gate 적용
  - master-targeted cluster에서 image pull, staged rollout, runtime contract 재검증

### 13. Terraform plan과 production hardening

- 현재 상태: EKS private endpoint default, ECR immutable tag, RDS secret consistency 구현; Terraform `1.15.8` / direct provider / root module pin과 lock file 추가; local `fmt -recursive`, `init -backend=false`, `validate` 통과; review 가능한 plan과 MSK/RDS production hardening 대기
- 목표: AWS migration skeleton을 검증 가능한 blueprint로 강화
- 완료 기준:
  - `terraform fmt -check`, `init -backend=false`, `validate` 지속 통과
  - AWS account/region/cost/destructive change를 검토할 수 있는 plan 통과
  - MSK TLS-only/authentication, EKS private access path와 least-privilege access entries, RDS deletion protection/final snapshot 정책 반영
  - remote state encryption/locking/access policy와 Terraform state에 남는 generated secret의 접근 경계 검토
  - controller와 Kubernetes workload 설치 범위 명시

### 14. 복구 훈련 확대

- 현재 완료: namespace prune 전환 결함 뒤 Namespace desired state와 `Prune=false` 보호 적용, fresh PostgreSQL 재설치, backup Job/PVC 확인. Host logical dump `39,433,414` bytes를 같은 local cluster의 disposable DB에 수동 복원해 10개 table count, Alembic `0008`, generic v2 row `33,840`, max id/sequence 일치 확인 후 임시 DB 삭제
- 남은 범위: 정기 restore drill, cluster/namespace lifecycle과 분리된 object storage 사본, cluster-loss 복구, 측정된 RPO/RTO 원본
- 목표: single-node local demo 범위를 넘어 복구 절차 검증
- 완료 기준:
  - multi-node broker/worker/node disruption 시나리오
  - local host/cluster lifecycle과 분리된 object storage backup 사본
  - dump size/checksum 검증과 backup 실패 alert
  - 정기 PostgreSQL restore drill과 측정된 RPO/RTO 원본 기록
  - consumer rebalance 및 partition imbalance 실험
  - runbook 단계와 실제 복구 시간 일치

### 15. Workload network isolation

- 목표: namespace 안의 lateral movement와 불필요한 egress 축소
- 완료 기준:
  - default-deny ingress/egress NetworkPolicy 적용
  - API → Kafka/Pgpool, Worker → Kafka/Pgpool, observability scrape 등 필요한 흐름만 허용
  - DNS, ingress controller, Argo CD, backup 경로 회귀 테스트
  - Kafka application authentication과 topic별 producer/consumer ACL 적용; self-consistent forged snapshot 주입이 거부되는지 검증

### 16. Observability durability와 notification path

- 목표: 관측 시스템 장애와 알림 전달 실패를 별도 운영 경계로 관리
- 완료 기준:
  - Prometheus/Grafana persistent storage 또는 외부 managed backend 결정
  - Alertmanager receiver, routing, inhibition, delivery failure 검증
  - `notification-worker` lag series가 실제 배포에서 수집되는지 확인
  - 관측 stack 장애가 core intake/persistence에 전파되지 않는지 검증

### 17. Major dependency migration

- 현재 상태: patch/minor 범위의 Python, Helm, CI kubectl, RDS PostgreSQL pin은 최신 호환 버전으로 정렬. Kafka client, pytest, GitHub Actions, AWS provider와 Terraform module은 major version 차이를 확인했으며 일괄 변경하지 않음
- 목표: major upgrade를 기능 변경과 분리하고 contract, integration, 배포 plan 증거를 갖춘 단위로 진행
- 완료 기준:
  - `kafka-python 3` 전환 뒤 producer/consumer, manual offset commit, rebalance, DLQ/replay contract와 실제 Kafka integration test 통과
  - `pytest 9` 전환 뒤 전체 suite와 warning-as-error 검증 통과
  - GitHub Actions major upgrade 뒤 CI test, image publish, overlay bot commit을 권한이 제한된 dry run에서 검증
  - AWS provider 6과 VPC/EKS/RDS module major를 같은 migration branch에서 `terraform init -upgrade`, `validate`, review 가능한 `plan`으로 검증
  - provider/module state drift, deprecated argument, replacement·삭제 resource를 확인하고 승인 없는 apply 제외
