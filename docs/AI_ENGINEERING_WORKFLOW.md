# AI Engineering Workflow

이 프로젝트에서 Codex를 개발 에이전트로 사용하는 역할과 검증 경계를 정의합니다. 공통 계약과 작업별 문서 선택은 [root AGENTS.md](../AGENTS.md)를 따릅니다.

## Harness 설계 이유와 검증 단계

기존 root `AGENTS.md`는 공통 계약, 날짜별 실험 수치, 실행 명령, UI·문서·GitOps 세칙을 함께 담고 있었습니다. 2026-09-09 문서 리팩터링은 작업 시작 시 읽는 공통 범위를 줄이고, 필요한 세부 정보를 작업별 문서에서 찾도록 변경했습니다. 최초 작업 트리 기준 root는 344줄에서 96줄로 축소됐으며 이후 master 전용 임시 파일 관리 규칙도 보존했습니다. 줄 수 감소는 문서 구조 변화의 증거이며 token·비용 감소 실측이 아닙니다.

- 고정 계약: root invariants와 branch/authority 규칙
- 필요한 context: task별 기존 문서 routing; 불필요한 directory-specific instructions 추가 제외
- 역할: 사람이 문제·범위·설계·acceptance criteria 결정, agent가 구현·검증 수행
- 완료 판정: test/evidence Gate와 사람의 검토; agent 완료 보고만으로 승인 처리 금지
- 실패 피드백: Gate·expected/actual·로그·위반 계약·수정 범위를 다음 iteration에 전달

제품의 Ops Agent는 운영 증거를 조사하는 기능입니다. Codex Harness는 이 저장소를 개발·검증하는 작업 체계입니다. 현재 주장 범위는 체계의 구조화와 사용 기록이며, 생산성·정확도·비용 개선은 비교 가능한 측정 뒤 판단합니다.

실제 유지보수 2~3개를 [사용 기록](HARNESS_WORK_LOG.md)에 남깁니다. 작업 전에 범위와 Gate를 정하고 실행 뒤 결과·실패·수정·사람의 판단을 기록합니다. 일부러 실패를 만들지 않으며 Gate 변경이 필요하면 이유를 남깁니다. First-pass는 최종 상태가 아니라 사전에 정의한 Gate의 최초 실행으로 판정합니다.

## Human responsibility

- 문제와 요구사항 정의, 작업 범위 결정
- Architecture boundary와 변경 금지 영역 결정
- Acceptance criteria, 운영 및 성능 기준 결정
- 검증 결과 해석, 최종 설계 선택, 위험 작업 승인

## Codex responsibility

- 필요한 repository 탐색, 코드 및 설정 구현, 반복 수정
- 작업에 맞는 테스트 작성 및 실행, diff 및 로그 정리
- 구현 대안 제안, 실패 원인 후보 분석

## 기본 작업 흐름

`Problem → Context → Constraints → Implementation → Validation → Evidence → Decision`

문제와 완료 조건을 확인하고 필요한 context와 제약을 선택합니다. 허용된 범위에서 구현한 뒤 검증 evidence를 수집하고 결과와 남은 판단을 보고합니다.

## Context 원칙

- 고정 프로젝트 사실은 root `AGENTS.md`, 세부 사실은 task-specific docs 사용
- 매 작업마다 저장소 전체 재분석 제외; 관련 없는 과거 실험 결과의 사전 읽기 제외
- 필요한 경우에만 관련 문서 추가 읽기, 이미 검증된 사실의 불필요한 재추론 제외
- Source candidate, published image, runtime 관측 시점을 구분해 기존 사실과 검증 범위 유지

## Validation과 Failure feedback

Codex의 완료 보고를 완료 조건으로 사용하지 않습니다. 작업의 acceptance criteria에 맞는 evidence로 판정합니다.

| 변경 / 질문 | 선택할 evidence |
| --- | --- |
| 코드와 계약 | pytest, smoke test, failed / dropped |
| 배포와 설정 | manifest render, CI, GitOps source-runtime consistency |
| 처리 정확성과 장애 | ordering, failure injection, recovery |
| 성능과 확장 | consumer lag, latency, replica behavior, 실험 조건 |

실행 명령은 [QUICK_START.md](QUICK_START.md), 측정 조건과 해석은 [TEST_RESULTS.md](TEST_RESULTS.md), 원본 보존은 [results/README.md](../results/README.md)에서 확인합니다. 현재 test count는 직접 실행한 출력으로 확인하며 과거 수치를 복사하지 않습니다. 실행 불가능하면 환경 원인과 실행한 범위를 보고합니다.

실패한 Gate, expected result, actual result, 관련 로그, 위반한 project invariant, 수정 가능한 범위를 다음 iteration에 전달합니다. 이 정보를 사용해 수정하고 같은 Gate를 다시 확인합니다.

## Authority boundary

- Destructive operation, secret 또는 실제 데이터 손실 위험, 의미가 불확실한 merge conflict는 사람의 판단 필요
- Architecture contract 변경과 stable baseline 승격은 사람이 정한 범위와 acceptance criteria에 따라 결정
- 증거 없는 성능, production SLA, exactly-once / global ordering / 무손실 주장 금지
- 실제 검증하지 않은 HA 또는 failover 주장 금지
- Git 작업 권한은 [root Work / Branch Rules](../AGENTS.md#work--branch-rules)의 단일 규칙 적용; 승인된 범위의 단계별 재확인 불필요

## Harness improvement metrics

향후 측정 가능한 작업에서 아래 항목을 기록할 수 있습니다. 현재 측정 결과를 뜻하지 않으며 미측정 값은 `미측정`으로 남깁니다.

| 필드 | 기록 기준 |
| --- | --- |
| agent_turns | 작업 시작부터 종료까지 agent turn 수 |
| human_interventions | 수정·추가 지시 등 사람 개입 횟수 |
| first_pass_gate | 첫 Gate 성공 여부와 Gate 이름 |
| context_scope | 읽은 파일·section·실험 범위 |
| repeated_analysis | 동일 범위 재분석 횟수와 이유 |
| regression | 검증된 회귀 여부와 evidence |
| token_usage / cost | 제공된 실제 usage와 비용, 측정 범위 |
| human_decisions_before_approval | 최종 승인 전 사람의 판단 횟수 |

작업 식별자, 범위, Gate, evidence 위치와 함께 기록합니다. 알 수 없는 turn·token·cost를 추정값으로 채우지 않습니다.

## 브랜치 간 문서 공유

공통 `AGENTS.md`와 문서 변경은 관련 주요 브랜치에 동일 패치 또는 cherry-pick으로 공유할 대상으로 기록합니다. 실제 반영은 root Git 권한 규칙에 따라 요청받은 범위에서 수행합니다. 문서 정리 요청만으로 다른 브랜치에 commit·merge하지 않습니다.

변경 의도, 적용 범위와 demo-lite 전용 여부를 먼저 구분합니다. 공통 운영 원칙은 [GITOPS.md](GITOPS.md), [OPERATIONS.md](OPERATIONS.md), [ARCHITECTURE.md](ARCHITECTURE.md), 저사양 전용 제약은 [DEMO_LITE.md](DEMO_LITE.md)에 둡니다. 특정 브랜치의 임시 상태나 수동 image import/local-only workaround를 전체 GitOps 기본 방식으로 일반화하지 않습니다.

## 문서 작성 규칙

- README는 포트폴리오 첫 화면 역할로 유지합니다. 모든 세부 내용을 README에 넣지 말고, 핵심 요약 / 데모 진입 / 대표 검증 결과 / 문서 지도만 남깁니다.
- README는 요약 → 아키텍처 → 운영 판단·대표 장애 → Ops Agent·Codex Harness → 역량·검증 범위 순으로 소개합니다. 발전 과정은 펼쳐진 독립 section으로 유지하고, 상세 수치·Pod 구성·AWS 대응 관계·운영 계약은 아래 접이식 영역에 둡니다. Current와 historical, Redis·Kafka baseline은 분리하고 문서 지도와 연락처로 마무리합니다.
- README의 기본 설명과 사용법은 외국인 리크루터도 볼 수 있게 한국어와 영어를 함께 사용합니다. 전체 문서를 완전 번역하지는 않더라도, project summary, demo usage, AWS migration blueprint는 영어 문장을 같이 둡니다.
- README에서 자세한 내용을 docs로 넘길 때는 링크만 던지지 않습니다. 각 주제마다 2~4줄 요약, 왜 중요한지 한 문장, 관련 docs 링크를 함께 제공합니다.
- 세부 구현, 실험 과정, 운영 절차, 장애 대응, Terraform AWS migration blueprint는 docs 문서로 분리합니다.
- changelog, patch notes, test results, migration plan처럼 시간 흐름이 중요한 문서는 최신 항목을 위에 둡니다. 과거 기록은 아래쪽 historical section으로 보냅니다.
- `docs/PATCH_NOTES.md`는 최신 변경이 맨 위에 오도록 관리합니다.
- `docs/TEST_RESULTS.md`는 최신 검증 결과를 먼저 보여주고, 과거 baseline은 historical results로 분리합니다.
- `docs/AWS_IAC_PLAN.md`는 현재 AWS migration blueprint를 먼저 설명하고, 구현 단계와 모듈 세부 설명은 뒤에 둡니다.
- `docs/ARCHITECTURE.md`는 현재 최종 Kafka-centered 구조를 먼저 설명하고, 과거 전환 배경은 뒤쪽 또는 별도 문서로 둡니다.
- Terraform 문서는 로컬 검증 구조를 AWS managed architecture로 이전하는 migration blueprint 관점으로 씁니다. AWS 배포 증거는 실제 실행 결과가 있을 때 기록합니다.
- 문서에서 Kafka 최종 구조를 Redis에서 이름만 바꾼 것처럼 쓰지 않습니다.
- Kafka를 Kafka-only라고 과장하지 않습니다. 이 프로젝트는 Kafka-centered 구조이며 PostgreSQL state/read model을 유지합니다.
- Kafka Worker KEDA 효과를 API throughput 증가로 단정하지 않습니다. Kafka에서 Worker scaling 효과는 consumer lag, persistence latency, drain time으로 봅니다. 2026-06 PowerShell 원본은 DB row `created_at` / row-visible proxy이며 실제 commit timestamp로 부르지 않습니다. 현재 script의 `accepted_to_status_observed_ms`는 client가 `persisted` status를 본 시각까지로 polling/network를 포함합니다. Worker histogram은 `commit()` 반환 뒤 기록한 `persisted_at` 기준이며 새 cluster 측정 전입니다.
- Redis 성능 수치는 Redis 프로젝트의 이전 scaling/tuning 성과로만 설명합니다.
- Kafka 성능 수치는 append-first intake baseline과 ordering/recovery validation으로 설명합니다.
- 2026-06 성능 결과의 event status `200`은 `202 Accepted` route contract 명시 전의 역사적 증거로 표시합니다. 현재 HTTP 계약과 성능은 새 build에서 다시 측정합니다.
- DLQ API의 `recent_samples`, `by_reason`, `oldest_sample_age_seconds`는 조회한 append-only log 표본의 통계로 설명합니다. unresolved queue depth, 현재 backlog, 미해결 event SLO로 표현하지 않습니다.
- `results/README.md`, `results/kafka-performance/latest.txt`, `results/ordering-failure/latest.json`, `results/postgres-restore/latest.json`, `results/postgres-recovery/latest.json`은 Git 추적 대상으로 유지합니다. 새 실행은 원본, 조건, stable baseline 채택 여부 또는 restore/recovery 검증 범위를 함께 기록합니다.
- `ops_agent/fixtures/`는 synthetic test input, `results/ops-agent/live-baseline/`은 sanitized captured runtime evidence와 deterministic derived condition result로 분리합니다. live capture는 bundle/raw hash, source dirty state, collector tree hash, freshness/coverage, `raw_ref` 기준 경로를 함께 기록합니다.
- `dev-kafka`를 현재 기본 배포 브랜치처럼 쓰지 않습니다. GitOps 기본 revision은 `master` 기준입니다.
- 문서와 답변에서 대비를 앞세운 상투적인 문장 구성을 피합니다. 서술의 중요도를 비교형 도입으로 만들지 않고 주제와 판단 기준을 바로 선언합니다. "A까지 포함한다", "B로 이어진다", "A를 바탕으로 B를 처리한다"처럼 의미를 직접 씁니다.
- 영어 문서와 답변도 상투적인 부정-대조 구문을 쓰지 않습니다. 같은 의미가 필요하면 짧고 직접적인 문장으로 나눕니다.
- 불렛 문서는 문장형 끝맺이를 피합니다. `~합니다`, `~했습니다`, `~하지 않음`보다 `~ 확인`, `~ 대기`, `~ 분리`, `~ 유지`, `~ 제외` 같은 항목형 표현을 우선합니다.

## Rollback 규칙

- 사용자가 "롤백", "실행취소", "이전 상태", "N번 전"이라고 말하면 새로 비슷하게 재코딩하지 않습니다. 먼저 현재 `git status`, 최근 commit, 작업 diff를 확인하고 어느 변경을 되돌릴지 특정합니다.
- uncommitted 변경은 해당 변경 범위만 되돌립니다. unrelated user change는 건드리지 않습니다.
- committed 변경은 대상 commit이 명확할 때 `git revert` 또는 명시된 baseline으로의 선택적 되돌리기를 우선 검토합니다. `git reset --hard`, 전체 `git checkout -- .`, `git clean` 같은 광범위한 삭제성 명령은 사용자가 명확히 승인한 경우에만 씁니다.
- 사용자가 "4번째 패널 전", "5번 전"처럼 UI 기준을 말하면, 최근 patch notes / commit log / diff에서 그 기준점을 먼저 찾아 설명한 뒤 되돌립니다.
- rollback 요청 중에는 기능 개선을 함께 섞지 않습니다. 요청한 상태로 되돌린 뒤 별도 수정이 필요하면 그 다음 단계에서 처리합니다.
