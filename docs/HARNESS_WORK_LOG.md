# Codex Harness 사용 기록

이 기록은 사람이 정한 범위에서 agent가 사용한 context, 검증 Gate, 실패 피드백과 남은 판단을 보존합니다. [작업 규칙](AI_ENGINEERING_WORKFLOW.md)을 실제 유지보수에 적용한 사례이며 생산성·비용 개선을 입증하는 비교 실험은 아닙니다.

## H001 — 2026-09-11 문서와 게시 상태 정합성

### Problem / Constraints

- 사람의 결정: 포트폴리오를 운영 판단·검증과 Codex Harness 두 축으로 마감; 문서 정합성을 우선 처리
- 시작 상태: clean `master` `cea699d`; 문서 작업은 `dev-kafka` `9f3f2f9`로 전환
- 불일치: README의 notification 직접 발행 그림, Outbox 문서의 이미지 미게시 표현, 이전 source/image를 current로 표시한 표
- 허용 범위: 문서·관련 문서 contract 검사 수정과 로컬 검증
- 제외: 제품 코드·아키텍처 변경, runtime 배포·부하·장애 주입, commit/push/merge, 성능 baseline 승격
- 기대 결과: source 구현·이미지 게시·runtime 관측을 구분하고, 마감 범위와 후속 과제를 명시

### Context / Evidence

- 공통 지침: `AGENTS.md`, `AI_ENGINEERING_WORKFLOW.md`
- 필요한 문서: 한·영 README, `ARCHITECTURE.md`의 처리 경로, `TRANSACTIONAL_OUTBOX.md`, `TEST_RESULTS.md`의 current status, `GITOPS.md`의 전달 계약, `IMPROVEMENT_ROADMAP.md`의 우선순위
- 검사 대상: `tests/test_portfolio_readiness.py`, workflow 문서 링크와 변경 파일
- Git 근거: `5e8addf` → dev image `5e8addfb10d0` → bot `9f3f2f9`; master merge `7440525` → image `74405259cefd` → bot `cea699d`
- 원격 read-only 재확인: [dev CI](https://github.com/Jangwanko/Cloud_portfolio/actions/runs/34306893410), [master CI](https://github.com/Jangwanko/Cloud_portfolio/actions/runs/34307137049), 모두 `success`
- Runtime 재조회 없음; 이전 local-ha/public demo 관측을 최신 이미지 rollout 증거로 사용하지 않음

### 사전 정의한 Gate

| Gate | 완료 기준 | 결과 |
| --- | --- | --- |
| G1 문서 정합성 | Outbox 경로와 transaction 경계, 게시/runtime 구분, 변경 문서 로컬 링크 정상 | 통과 |
| G2 관련 contract | portfolio readiness 테스트 통과; 이전 문구를 강제하면 현재 의미에 맞게 수정 | 통과 |
| G3 회귀 | 현재 checkout 전체 pytest 실행 결과 기록 | 통과 |
| G4 범위 | diff check 통과, 문서와 관련 검사 외 제품 변경 없음 | 통과 |

### 측정 범위와 사람의 판단

- 관측 단위: 이번 사용자 요청 이후 하나의 유지보수 작업; 앞선 리팩터링·게시 작업과 중단된 시작 turn은 집계 제외
- agent_turns: 미측정 — 표준화된 session turn export 미수집
- human_interventions: `0` — 측정 시작 후 검증 완료까지 추가 수정 지시 없음; 시작 요청과 reasoning 설정 변경은 집계 제외. 대화에서 수동 집계한 값
- context_scope: 위 파일·section과 Git/CI metadata; 전체 과거 실험 원본과 무관한 소스 재분석 제외
- repeated_analysis: 미측정 — 자동 read trace 미수집; 구현 확인에 필요한 재조회와 낭비성 재분석을 동일시하지 않음
- token_usage / cost: 미측정 — 신뢰할 usage export 미확보; 사용자가 말한 medium 설정을 모델·비용 실측으로 해석하지 않음
- first_pass_gate: G1~G4 최초 실행 모두 통과; Gate 실패 `0`, 실패로 인한 수정·재시도 `0`
- regression: 실행한 전체 suite에서 검출 `0`; 실제 runtime·장기 부하까지 회귀 부재를 주장하지 않음
- 사용자 검토: 후속 메시지 “좋아 다음”으로 다음 단계 진행 수락 확인; commit/push/merge 승인으로 해석하지 않음

### 실행 결과와 다음 판단

| Gate | 실행 / 관측 결과 |
| --- | --- |
| G1 | 변경 문서의 Markdown 로컬 링크·anchor 114개 검사, 누락 0; 한·영 그림의 Outbox publisher 경로와 직접 발행 화살표 제거 확인; Outbox의 게시/runtime 분리 확인 |
| G2 | `.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_portfolio_readiness.py` → `32 passed in 1.05s` |
| G3 | `.venv\Scripts\python.exe -X utf8 -m pytest -q` → `658 passed in 22.75s` |
| G4 | `git diff --check` 통과, diff 검토; 문서 10개(신규 기록 포함)와 기존 문서 검사 파일 1개만 변경 |

결과는 이번 tool 출력에서 수동 전사했습니다. 자동 session telemetry와 원본 pytest 로그 파일은 수집하지 않았으므로 세밀한 agent 비용·turn 분석 자료로 사용하지 않습니다.

기존 README contract의 Outbox 문장은 로컬 candidate 상태를 강제했습니다. 게시 사실을 반영한 새 문장으로 검사 기대값을 G2 실행 전에 수정했고, runtime rollout 미확인 조건은 유지했습니다. 이를 Gate가 잡은 실패 사례로 기록하지 않습니다.

사람이 정한 마감 방향에 따라 README의 두 축, Harness 설계 이유, 필수/선택/후속 작업 구분을 문서화했습니다. Runtime 배포·새 기능·commit/push/merge는 수행하지 않았습니다. 사용자는 후속 메시지 “좋아 다음”으로 다음 단계 진행을 수락했습니다. 다음 사례는 게시 이미지의 runtime 상태를 읽기 전용으로 확인하는 작업입니다. 다른 주요 브랜치 공유는 해당 Git 작업 요청 시 처리합니다.

## H002 — 2026-09-11 게시 이미지 runtime 읽기 전용 점검

### Problem / Constraints

- 사람의 결정: H001 뒤 다음 단계 진행
- 범위: context·접속 가능 여부 확인 후 Argo revision·실행 imageID·migration·Worker/Outbox 상태 조회
- 보존: H001의 미커밋 변경; 제품 코드·Git 상태 변경 없음
- 제외: Docker/cluster 시작·재생성, Argo sync, rollout, 데이터 reset, 이벤트 쓰기, 장애 주입
- Context: root `AGENTS.md`, H001 결과, `OPERATIONS.md` Local Status Check; 대상은 명시적으로 `kind-messaging-ha`

### 검사 결과

| 단계 | 기대 | 실제 | 판정 |
| --- | --- | --- | --- |
| Context 확인 | local-ha context 식별 | current context `kind-messaging-ha` | 통과 |
| Kubernetes 연결 | nodes 조회 성공 | `kubectl --context kind-messaging-ha get nodes --request-timeout=10s` exit 1, API 연결 거부 | 환경 사전조건 실패 |
| 원인 확인 | Docker Linux backend 상태 식별 | `docker version`은 client만 반환하고 Linux engine pipe 없음; Docker Desktop/backend 프로세스 조회 결과 없음 | 엔진 접속 불가 확인 |
| Argo revision·runtime imageID | source/image와 runtime 비교 | Kubernetes 접속 불가로 조회하지 않음 | 미실행 |
| Migration·Worker/publisher·pending | 실제 배포와 처리 상태 확인 | 선행 조건 미충족 | 미실행 |

### Failure feedback / Decision

- 실패 Gate: Kubernetes 연결 사전조건
- Expected: API 접근 가능, 이후 workload 조회
- Actual: `connectex: No connection could be made because the target machine actively refused it.`
- 보조 로그: Docker API `dockerDesktopLinuxEngine` pipe를 찾을 수 없음
- 지켜야 할 invariant: 접속 불가를 Outbox 배포 실패나 source-runtime 불일치로 해석하지 않음; 이미지 게시 성공을 runtime 검증 성공으로 대체하지 않음
- 다음 수정 범위: Docker Desktop/기존 kind 환경을 사용할 수 있게 한 뒤 같은 context의 연결 점검부터 재개
- 시작 경계: 엔진을 시작하면 기존 container 및 GitOps reconciliation이 재개될 수 있으므로 이번 읽기 전용 점검에서 자동 시작하지 않음
- 기록한 사전조건 실패 1회, 원인 확인 1회; 무의미한 연결 재시도와 application 수정 없음

### 측정과 한계

- first_pass: 환경 사전조건 최초 실행 실패; 제품 검증 Gate는 미실행
- human_interventions: 시작 요청 이후 이 기록까지 추가 지시 `0` (수동 집계)
- agent_turns / repeated_analysis / token_usage / cost: 미측정; 자동 session telemetry 미수집
- regression: 판정 불가; 이번 작업은 pytest 대신 read-only 환경 확인과 문서 diff check 수행
- 도구 출력은 위 표에 수동 요약; credential·전체 kubeconfig 출력 및 보존 없음
- 완료 상태: 환경 사전 점검과 실패 피드백 기록 완료; 게시 이미지 runtime 검증은 대기

### H002 재개 — Docker 시작 후 runtime 관측

사용자가 Docker Desktop을 직접 시작하고 점검 재개를 요청했습니다. H002 환경 개입은 누적 1회(사용자 엔진 시작)이며, 최초 사전조건 실패를 성공으로 덮어쓰지 않습니다.

- Docker engine `29.7.2`, Kubernetes node `Ready`; 연결 사전조건 재검증 통과
- Docker pipe의 sandbox 접근 거부 1회는 동일 read-only 명령을 승인된 권한으로 재실행해 해결; 제품 실패로 집계하지 않음
- 최초 Argo status는 이전 `fc59535` / `Synced / Healthy`; 재시작 직후 기존 status와 Pod 준비 상태가 달라 완료 증거로 채택하지 않음
- Controller 재개 뒤 `dev-kafka`의 `9f3f2f9`를 감지해 자동 sync 시작; agent가 sync/apply/rollout을 실행한 것은 아님
- 대상은 dev 이미지 `5e8addfb10d0`; master 이미지 `74405259cefd`를 이 cluster의 기대값으로 사용하지 않음
- 새 migration Job은 `5e8addfb10d0`이며 Alembic `0008 → 0009_notification_outbox` 실행 로그 확인; 성공/commit 완료는 미확인
- 기존 API/core/notification Worker는 `54ee42a2fb29`, imageID `sha256:a2a834aba68848835216cffa99c00ecf52117484abec6af325cceca4c3200dd6` 유지; Outbox publisher Pod/Deployment 없음
- Argo 최종 관측: `OutOfSync / Missing`, operation `Running`, migration Job healthy 대기
- API readiness: `degraded`, `postgres_sync_standbys_below_minimum`, sync standby `0`; grace 만료 후에도 같은 상태

[필드를 제한한 runtime snapshot](harness/H002-runtime.json)은 context·Argo 상태·앱/DB Pod imageID와 readiness를 보존합니다. 관측 시각은 host UTC `2026-09-10T20:49:04+00:00`(한국 시각 9월 11일)이며 workload 상태는 이 시점의 snapshot입니다. Secret·환경변수·전체 Application manifest는 저장하지 않았습니다.

DB 진단은 기존 helper의 credential 취급 방식을 읽고, helper 전체 실행 없이 `SELECT`만 수행했습니다. Primary의 `synchronous_commit=on`, `synchronous_standby_names=ANY 1 (...)` 유지와 `pg_stat_activity`의 `SyncRep` 대기를 확인했습니다. Replication 연결은 `pg_basebackup`의 backup/streaming·async 상태였고 standby Pod 두 개는 로그상 rejoin/cloning 중이었습니다. 따라서 readiness의 standby_count를 정상 준비된 standby 수로 해석하지 않습니다.

DB 동기 standby 부재가 migration 대기의 원인 후보입니다. 대기 session과 migration process의 직접 연결까지는 검증하지 않았습니다. 제품 SQL 오류나 migration 결함으로 확정하지 않으며, rollback·sync 완화·PVC 삭제·DB 재시작을 수행하지 않았습니다.

조회 중 PowerShell JSONPath quoting 오류 1회는 JSON 파싱 방식으로 수정했습니다. 이는 조회 도구 오류이며 DB 장애나 application Gate 실패와 분리합니다. 재개 후 전체 pytest는 실행하지 않았습니다(제품 코드 변경 없음). JSON parse와 `git diff --check`로 기록을 확인했습니다.

결론: 환경 연결은 복구됐으나 게시 이미지 rollout은 migration 단계 대기입니다. pending drain·소규모 이벤트 검증은 미실행이며 다음 판단은 기존 데이터와 동기 복제 계약을 보존하는 PostgreSQL standby 복구 범위입니다.

### H002 완료 — 자체 복구 후 runtime Gate

- 사용자 승인: 기존 데이터·동기 복제를 유지하는 PostgreSQL 복구 진행. 추가 architecture 변경이나 baseline 승격은 없음.
- Mutation 직전 재조회에서 standby 자체 복구와 migration 완료를 확인했습니다. 계획했던 startup probe/rolling partition patch는 실행하지 않았습니다. Primary 재시작·sync 완화·PVC 삭제·수동 Argo sync 모두 없음.
- 실패 원인 증거: standby clone 도중 liveness 실패/Killing 이벤트와 exit 137 반복. 이후 동일 설정으로 두 standby가 streaming/quorum에 도달했습니다. 재발 방지책은 아직 적용·검증하지 않았습니다.
- Gate 통과: Argo `9f3f2f9` Synced/Healthy/Succeeded, migration `0009_notification_outbox`, 대상 app Pod 전부 dev image `5e8addfb10d0`/Ready, PostgreSQL sync quorum 2, readiness ready.
- `smoke_test.ps1 -SkipReset` 통과: event 1건 DB persistence, 해당 outbox published 1행·attempt 1회, notification attempt 1건, 전체 pending 0. 검증 fixture 보존, 기존 데이터 reset 없음.
- 도구 실패: PowerShell execution policy로 첫 smoke 실행 차단; 새 프로세스에 한정한 Bypass로 실행. 재실행 wrapper의 Stop quoting 오류는 별도로 기록하며 실제 smoke 출력과 DB assertion을 함께 확인했습니다. 첫 실패에서 생성된 null snapshot은 성공 결과로 교체했습니다.
- [완료 runtime snapshot](harness/H002-runtime-recovered.json), [smoke/DB assertion 결과](harness/H002-smoke.json). 최초 실패 snapshot도 보존합니다.
- first_pass: 전체 H002는 실패 후 통과. 원래 환경 precondition 실패를 지우지 않습니다. 측정한 Docker 시작 개입 1회와 별도 복구 범위 승인 1회를 기록합니다. Token·전체 turn 수·비용·효율 향상은 미측정입니다.
- Decision: 게시된 dev 이미지의 local-ha 기본 runtime Gate 완료. Public demo 배포나 성능/HA baseline 승격으로 확대 해석하지 않습니다.

문서 상태 변경 후 `tests/test_portfolio_readiness.py`: `32 passed in 0.30s`; `git diff --check` 통과. 제품 코드 변경이 없어 전체 suite는 재실행하지 않았습니다.

## H003 — 2026-09-11 Runtime 요약 정합성과 마감

### 사전 범위와 Gate

- Problem: H002 상세 결과와 달리 Current Evidence 표의 Last verified runtime은 8월 이미지 유지; publication 단락의 미조회 표현도 현재 결과와 혼동 가능
- Context: root AGENTS, AI Engineering Workflow, TEST_RESULTS 상단 publication/runtime/current 표, README 한·영 경계 표, PATCH_NOTES, roadmap, H002 JSON
- 허용 범위: 요약 문서·근거 링크 정리, 기존 문서 contract와 전체 suite 검증, 사용자 요청에 따른 dev-kafka commit/push 및 master merge/push·CI 확인
- 제외: 제품 코드·배포 설정 변경, 이미지 재게시, runtime 재검증 수치 확대, demo 브랜치 전파
- G1: current 표가 H002 revision/image/readiness와 일치; JSON parse 및 변경 문서 로컬 링크/anchor 검사
- G2: 전체 pytest 통과와 diff check; Gate 실패 시 원인과 수정 결과 기록
- G3: 원격 dev/master CI 성공, 최종 remote commit과 image tag 확인
- first_pass: 각 Gate의 최초 실행으로 기록; token/cost/전체 agent turn 수 미측정

### 로컬 결과 — 2026-09-13 재개

- G1 통과: 로컬 Markdown 링크/anchor 125개와 evidence JSON parse, current runtime 표와 H002 snapshot 일치
- G2 통과: `658 passed in 24.21s`, `git diff --check` 통과
- First-pass: G1 최초 검사 통과. 중단 전 전체 pytest 종료 출력은 세션 소실로 회수 불가; G2 최초 결과 미확인, 재개 후 재실행 통과로 구분
- 제품 코드·manifest 변경 없음; 문서 commit과 master merge에 workflow의 image skip marker 사용, 기존 dev/master 게시 image tag 보존
- G3는 이 기록을 담은 commit 이후 실행; 최종 commit별 GitHub Actions 결과와 사용자 완료 보고를 원격 Gate 근거로 사용
- 공유 적용 범위: dev-kafka와 master; demo-dev/demo-lite는 이번 전파 범위 제외
- 사람 개입: 중단 후 계속 지시 1회; 범위 수정 지시 없음. 비용·token·효율 개선 미측정
- Decision: 문서 정합성·local-ha 기본 처리·Harness 사례 3개 마감. 선택 reliability 실험과 후속 backlog는 필수 완료 범위에서 제외 유지
