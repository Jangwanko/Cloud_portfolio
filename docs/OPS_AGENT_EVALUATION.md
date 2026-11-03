# Ops Agent — 검증 결과와 남은 한계

이 문서는 [현재 기능](OPS_AGENT.md)을 뒷받침하는 증거와 미검증 범위를 모은다. 회차별 전체 기록은 JSON·로그로 보존하며, 테스트 통과 수와 모델 진단 정확도를 구분한다.

**현재 판단:** 모델이 운영 증거를 설명하고 일부 실제 장애에서 유용한 원인 후보·복구안을 제안한 것은 확인했다. 성공 판정 결함 6종을 보강하고 반례로 재검증했다. 첫 순차 조사 비교에서는 모델·Runbook의 결론이 같았고 모델의 조회 횟수가 더 많았다. 일반적인 원인 정확도, 고정 Runbook 대비 추가 가치, 자율 복구는 아직 입증하지 않았다.

## 통제된 로컬 MVP 완료 — 2026-11-03

기존 수집기·상태 판정·런북 검색·PG/Worker 주입기를 연결했다. 새 구현은 과거 재서술의 기본 출력 제외, 근거가 있는 반대 증거·승인 필요 복구 제안 필드, Worker 표준 Bundle 연결, 설정 복원 검증과 사건 경험 저장·검색이다. 루프백 `kind-messaging-ha` preflight와 사용자 승인 후 두 실험을 각 한 번 실행했다. 원격 운영·공개 데모·기존 데이터 삭제·PG rollout 변경은 수행하지 않았다.

| 실제 실험 | 관측과 복구 | 최초 Luna 판단·한계 |
| --- | --- | --- |
| PG 행 잠금 | 새 stream 행 최대 50초 잠금. 수락 21건 중 장애 중 저장 5건, waiter 1건, lag 20→16. 해제 후 21/21 저장·owned session 0·lag 0 연속 3회 | 실제 waiter/blocker로 DB blocking 후보 제시, 요청 연결은 미확인. PG 조사 관련성은 코드 정책 준수. rebalance는 과거 로그를 포함한 미확인 추가 후보. 복구 제안은 빈 목록 |
| Worker 기동 실패 | 잘못된 시작 모듈로 CrashLoopBackOff. 장애 중 수락 20건 대기, lag 20. 원래 command/strategy와 Argo 정책 복원 후 20/20 저장·Worker 2/2·API ready | `No module named worker.consumer` 로그로 기동 실패 식별. Worker lag 런북은 증상 조사에 적용. 복구 제안 보류. 정상 seed가 이미 저장됐는데 소량 lag 1로 런북을 과하게 적용한 한계 |

두 실험 모두 Worker/PG 설정 hash, PG UID/restart, Argo 자동 정책 일치를 확인했다. Luna `gpt-5.6-luna`는 **정상·장애 시점 각 1회, 총 4회** 호출했다. 승인 상한 6회, 호출당 6,000 출력 토큰·45초·재시도 없음이다. 안전한 rollback 이후 저장 증거를 재생했고 **복구 snapshot·주입 계획·정답·평가 기준은 전달하지 않았다.** 복구는 코드 전용 세 번째 기록이다. 네 최초 응답은 형식·인용 검사를 통과했으며 수정·재호출하지 않았다. 새 `updates`는 빈 목록으로 제한하고 기본 JSON/text와 후속 모델 문맥에서 제외하며 원본은 보존한다.

같은 Incident ID의 정상·장애·복구 Bundle, 실제 로그/메트릭/Pod 상태, 모델 입력·응답, 승인·주입·롤백·설정 비교를 `results/ops-agent/shadow/20261103-mvp-{lock,worker}-01`에 보존했다. 각 `incident-record.json`과 `runbook-draft.json`을 생성하고 모델 없이 검색·현재 사건 제외·artifact 변조 거부를 검증했다. 이는 `ops.incident-experience.v1` sidecar이며 작은 실험에 calibrated backlog IncidentRecord를 임의 생성하지 않는다. 복구 증거 검증과 운영자 의미 검토/PENDING을 분리하며 런북을 자동 게시하지 않는다. Kubernetes가 readyReplicas를 생략하면 UNKNOWN을 유지하고 실제 non-ready 컨테이너를 별도 장애 근거로 사용한다.

런북 세 상황은 [기존 Luna 평가](../results/ops-agent/diagnosis/runbook-assistance-20261101-summary.json)를 재사용하고 현재 검색을 재검증했다: matching→PG, similar→Kafka, unmatched→빈 목록. 런북 없이 추가 확인을 제공하는 회귀도 유지한다. 이를 현재 모델의 새 독립 정확도 결과로 계산하지 않았다.

전체 테스트 **1,080개 통과(57.77초)**. 집중 회귀와 여섯 시점 연결·원본 hash·네 응답 대응·복구 정보 비공개·사건 검색 검증 완료. 최초 집중 검증의 Windows UTF-8 읽기 실패도 validation log에 보존했다. [완료 증거·검토·테스트/원본 경로](../results/ops-agent/diagnosis/mvp-20261103-summary.json). 복구 제안 유용성, 과한 런북 적용, 넓은 조회는 후속 품질 과제로 남기며 이 한계로 MVP를 무기한 확장하지 않는다.

실험용 MVP로 병합 검토 가능한 범위다. 로컬 committed ref의 읽기 전용 merge-tree에서 `docs/OPS_AGENT.md`, `docs/TEST_RESULTS.md` 충돌을 확인했다. 미커밋 MVP 변경은 예측에 포함되지 않으므로 병합 전 재검토가 필요하다. 원격 CI·master 병합·공개 README 수정·원격 배포는 수행하지 않았다. PG primary rollout·Helm reconciliation을 이번 복구 성공으로 검증했다고 해석하지 않는다.

**작업 범위 조정 — 2026-11-02:** 최근 세 회차는 동일 네 관측에 총 20회 호출한 개발 회귀다. PG 판단·필수 조사 대상의 일관성은 코드가 지정한 조건 준수로 집계한다. 알려진 과거 설명 오류는 보존하되, 다음 작업은 기본 출력 범위를 줄이는 최소 변경으로 한정한다. 아래 실패마다 새로운 검증기와 모델 호출을 추가하거나 실험 기능 병합에 운영 수준의 완성을 요구하지 않는다. 변경별 종료·병합·승격 기준은 [하네스 규칙](AI_ENGINEERING_WORKFLOW.md#harness-scope-and-stop)과 [현재 완료 조건](OPS_AGENT.md#implementation-priorities)을 따른다. 이 조정으로 과거 실패가 통과로 바뀌거나 병합이 승인된 것은 아니다.

## 조사 기준 고정과 필수 확인 누락 검사 — 2026-11-02

v4는 현재 blocking edge 또는 명시적 primary 연결 실패가 있으면 PG 경로 조사를 적용하고, 지원되는 양성 신호가 없으면 미확인으로 남긴다. 현재/과거 적용 범위를 임의로 바꾸는 응답을 거부하며, 과거 관측 설명은 별도 updates로 유지한다. 조회 불가 요청·Worker desired>0/ready=0·현재 DB 잠금에는 필수 조사 대상 ID와 근거를 제공하고, 다음 확인에서 누락하거나 다른 근거로 연결하면 보류한다. **이 일관성과 대상 선택은 코드 정책의 성과이며 모델 정답률로 계산하지 않는다.**

앞선 네 관측을 각각 두 번, 총 8회로 고정해 같은 Luna를 호출했다. 쌍마다 입력과 코드 이력 hash가 같고 이전 모델 답변은 넣지 않았다. 8개 응답 모두 계약 검사 통과, 코드 관측·이력·정책은 비교군과 동일했다. 실제 문장 검토에서는 다음을 확인했다.

- 403: 두 응답 모두 해당 요청의 인증·인가·가시성 및 조회 경로를 읽기 전용으로 확인하도록 설명
- DB 잠금: 두 응답 모두 관측된 waiter/blocker의 query·transaction·lock 정보를 요청별 Worker 저장 기록과 연결해 확인하도록 설명. 원인 확정은 유보
- Worker ready 0: 두 응답 모두 가용성 후보로 사용하고 readiness·startup·스케줄링·로그 확인을 제안. scaling·재시작 권한 부여 없음
- 남은 오류: 재발 시점의 두 응답 모두 과거 ‘요청 저장 미관측’과 나중의 ‘lag 0’을 한 시점처럼 섞어 설명. 실제 lag 0 이력에서는 해당 두 요청이 이미 persisted였다. 유효한 이력 ID 인용만으로 문장의 사실성이 보장되지 않음
- 남은 부담: 표본 회복 후 두 응답 모두 적용 제외한 Kafka intake도 넓게 재확인하도록 제안. 운영자 시간 절감은 미입증

전체 테스트 **1,071개 통과(54.20초)**, 집중 검증 127개 통과. 오래된·불완전한·중복 Worker 관측, 음수/boolean replica, 의도적 desired 0, 일반 503과 명시적 DB 실패 구분, 필수 조사 누락·오인용, 정책 변조·구버전 혼용을 검증했다. 올바른 대상 ID에 무관한 문장을 붙여도 구조 검사를 통과하는 반례도 유지한다. [동결 소스·원본·판단 범위](../results/ops-agent/diagnosis/grounded-policy-20261102-summary.json).

이번 변경은 조사 기준·누락 검사를 검토 가능한 개발 후보로 만든 것이다. 의미 일관성이 완성됐거나 운영 기능으로 승격할 준비가 끝났다는 뜻은 아니다. 다음 핵심은 **인용한 과거 시점의 사실과 모델의 변경 설명을 대조하는 경계**다. 이번 원문을 보정하거나 성공할 때까지 추가 호출하지 않았다. master 병합·원격 CI·배포는 이번 범위에 포함하지 않았다.

## 구조화 출력과 동일 입력 반복 — 2026-11-02

**형식 계약은 통과했지만 판단 일관성 기준은 미달이다.** strict JSON Schema로 모든 필드를 요구하고 인용 가능한 현재·과거·미확인 항목 ID를 제한했다. 응답 거절·출력 중단도 원문을 보존하고 설명을 보류한다. 기존 의미·인용 관계 검증과 코드의 사실·상태 소유권은 유지했다.

수정에 사용한 4개 관측을 각각 2회, **총 8회로 호출 전에 고정**했다. 같은 `gpt-5.6-luna`를 사용하고, 쌍마다 이전 코드 관측까지 동일하게 제공했으며 요청 hash 일치를 확인했다. 이전 모델 답변은 전달하지 않았으므로 실제 순차 조사 안정성이나 미관측 정확도 평가가 아니다. 자동 보정·재시도 없이 8개 모두 형식 검사를 통과했고 코드 관측·이력은 비교군과 같았다. 이는 **정답률 8/8이 아니다.**

| 비교 항목 | 확인한 결과 |
| --- | --- |
| 필수 필드·인용 ID | 이번 8회에서는 누락·오타로 인한 보류 없음. 12개 record와 동결 소스·원문 대응 검증 |
| 미확인 항목 선택 | 네 쌍 모두 동일. 다만 403 사례는 두 답변 모두 권한·조회 가시성 다음 조사를 빠뜨림 |
| 런북 판단 | 두 쌍은 동일, 두 쌍은 달랐음. 잠금 시점 PG 조건이 MET/UNKNOWN으로, 표본 회복 시점에는 HISTORICAL 추가 조사/CURRENT 제외로 변경 |
| 원인 후보·추가 확인 | 문장과 우선순위가 달라짐. 재발 사례는 두 답변 모두 관측된 Worker ready 0을 명시적 가용성 후보로 활용하지 못함 |
| 과거 관측 보존 | 두 반복 모두 잠금 이력을 유지. 다만 한 문장은 과거의 ‘저장 미관측’을 ‘저장되지 않음’으로 강하게 표현 |

전체 테스트 **1,040개 통과(61.22초)**, 집중 검증 90개 통과. 거절·불완전 응답, 빈 ID 집합, 과거/현재 인용 구분, 반복 예산과 동일 입력 보존을 검증했다. [원본 hash·판단 차이·검토 범위](../results/ops-agent/diagnosis/grounded-strict-repeat-20261102-summary.json). 의미 검토는 Codex가 수행했으며 독립 운영자 평가가 아니다.

다음 빈틈은 DB 런북 조건의 증거 기준·현재/과거 조사 범위를 명확히 하고, 요청별 조회 실패 조사 누락과 중요한 관측의 미활용을 검토할 수 있게 하는 것이다. 이번 응답을 수정하거나 잘 나올 때까지 추가 호출하지 않았다. 의미 일관성·운영자 부담 감소·master 병합 준비 완료를 주장하지 않는다.

## 요청별 미확인 항목과 관측 이력 보강 — 2026-11-02

앞선 실패 사례를 개발 회귀 집합으로 전환해 두 빈틈을 수정했다. v3는 요청별 append 수락·저장·상태 조회를 별도 코드 필드로 제공하고, 모델 `unknowns`에는 코드가 만든 미확인 항목 ID만 허용한다. 자유문장·임의 ID로 이미 확인된 수락을 미확인으로 바꾸면 응답을 보류한다. 이전 코드 관측 최대 8개를 모델 설명과 독립적으로 보존하며, 출처 hash·시각·scope와 생략된 항목 수를 표시한다. 과거 관측은 현재 근거와 별도로 인용한다.

동일 `gpt-5.6-luna`로 혼합 상태 1시점과 잠금→회복→재발 3시점, 총 4회 호출했다. **세 응답은 UNREVIEWED, 잠금 시점 하나는 WITHHELD**였다. 보류 원인은 `next_checks.runbook_id` 필수 필드 3개 누락이며, 원문에는 잘못 복사된 근거 ID도 있었다. 자동 보정·재시도 없이 첫 응답을 보존했다. 코드 관측과 과거 관측 내용은 모델 없는 비교군과 모든 시점에서 동일했다.

- 혼합 상태: 세 요청의 append 수락 유지, 저장 1·대기 1·조회 불가 1 구분. 403 요청에 저장 미확인과 조회 실패 원인 항목을 선택하고 인증·인가·가시성 확인 제안
- 회복: 직전 모델 응답이 보류됐어도 과거의 실제 blocking edge 관측을 이력 ID로 인용. 현재 표본 저장·lag 0·대기 없음과 분리
- 재발: 최초 잠금 관측과 그 뒤 표본 회복을 각각 인용하고, 새 요청 대기·Worker ready 0을 별도 현재 조사 대상으로 설명

전체 테스트 **1,030개 통과(54.70초)**, 집중 검증 86개 통과. 이전 잘못된 문장·임의 미확인 ID 거부, 이력 변조·다른 scope·미래 시각·과거 ID의 현재 인용 차단, 모델 실패 후 이력 유지, 8개 초과 생략 표시를 확인했다. record hash 8개와 응답 정규화 일치를 검증했다. 선택적 `history_ids` 누락은 빈 목록으로 정규화하며 모델 문장은 바꾸지 않는다. [원본·실패·검토 범위](../results/ops-agent/diagnosis/grounded-v3-20261102-summary.json).

이는 수정에 사용한 합성 사례의 회귀 결과다. 다른 자유문장까지 의미 검증한 것은 아니며, 4회 중 1회의 출력 계약 실패와 넓은 추가 조회가 남는다. 미관측 정확도·운영자 부담 감소·master 병합 준비 완료로 해석하지 않는다. 기존 v2 실패 기록은 보존하고 암묵적으로 v3 이력으로 변환하지 않는다.

## 구현 동결 후 새 사례 평가 — 2026-11-02

**판정: 병합 검증 기준 미달.** 구현·프롬프트·런북을 고정한 뒤 새 합성 4상황·6시점을 작성하고, 기대 행동·실패 기준과 소스 사본/hash를 호출 전에 저장했다. Luna `gpt-5.6-luna`를 시점당 1회, 총 6회 호출했고 이번 평가 중 수정·재시도는 하지 않았다. 모델에는 현재 증거와 엄격히 이전 결과만 제공하고 평가 답안은 제외했다. 동일 작성자·검토자의 합성 평가이며 독립된 정확도 검증이나 실제 장애 재현은 아니다.

| 새 상황 | 확인 결과 |
| --- | --- |
| 요청 저장 정상·lag 수집 불능 | 표본 저장 확인과 lag 미관측을 구분하고 장애 원인을 단정하지 않음. 지표 재조회 외 DB·Kafka 로그까지 넓게 권고해 조사 범위의 효율성은 미흡 |
| 요청 대기·양수 lag·관측된 DB blocker 없음 | 잠금 원인을 확정하지 않고 Worker/persistence 후보와 offset·retry·요청별 로그 확인 제안 |
| 3개 수락 요청 중 저장 1·queued 1·상태 조회 403 1 | 코드 분류는 정확하지만 모델 unknowns에 “qc-3의 403 원인과 Kafka append 여부를 알 수 없다.”라고 작성. 같은 응답의 Kafka 런북 설명은 세 요청 모두 append 수락됐다고 인정. **알려진 202 계약 위반과 응답 내부 모순** |
| 잠금 후보→표본 회복→새 요청 대기·Worker ready 0 | CURRENT→HISTORICAL→CURRENT 구분과 새 Worker 가용성 후보 갱신 확인. 다만 첫 시점에서 관측한 blocking edge가 세 번째 입력의 구조화 이력에 남지 않아 과거 증거가 없다는 표현 발생 |

6개 응답 모두 구조화 검사를 통과해 `UNREVIEWED`였고 코드 관측은 비교군과 동일했다. 따라서 형식 통과를 의미 정확도로 승격하지 않는다. 혼합 상태 사례에서는 고정 관측+런북 비교군에 없던 오류를 모델이 추가했고, 403 요청의 조회 권한/가시성을 확인하는 구체적 다음 조사도 빠졌다. 운영자 부담 감소·일반 정확도·반복 안정성은 입증하지 않았다.

검증: source freeze hash 9개, record hash 12개, 원문 응답·모델 입력 대응 6개와 이전 record 연결 확인. 전체 테스트 **1,014개 통과(54.03초)**, 집중 회귀 65개 및 평가 실행기 5개 통과. 실행기는 별도 fixture 입력·최대 8시점 제한·소스 사본 저장·실행 중 소스 불변 확인을 추가했으며 진단 구현·프롬프트·런북은 바꾸지 않았다. [전체 판정·반례 원문·원본 위치](../results/ops-agent/diagnosis/grounded-novel-20261102-summary.json).

후속 수정은 **요청별 append 수락 / 저장 상태 / 조회 가시성의 구조화된 사실·미확인 항목 대조**, **모델 문장과 독립된 여러 시점의 관측 사실 보존**, **원인을 구분할 다음 조회의 우선순위**다. 이번 실패를 수정에 사용하면 해당 사례를 개발 회귀 집합으로 전환하고 다음 미관측 평가에는 새 사례를 사용한다. 이번 평가에서는 변경 커밋·push·원격 CI·master 병합을 수행하지 않았다.

## 코드 관측과 모델 해석 분리 — 2026-11-02

기본 CLI가 모델 호출 전에 요청 ID별 수락·저장 조회, 최신/과거 lag와 DB 대기 관계를 계산·저장하도록 변경했다. 모델은 원인 후보·런북 조사·추가 확인을 작성하며 현재 상태·심각도를 덮어쓰지 않는다. 모델 오류에도 관측을 반환하고, 구조화 계약 충돌은 설명을 보류한다. 자유문장은 통과 후에도 `UNREVIEWED`다. 임의의 거짓 자유문장이 구조화 검사를 통과할 수 있다는 한계도 반례 테스트로 명시했다.

직접 작성한 합성 4상황에서 고정 관측+검색 런북과 Luna 추가 응답을 비교했다. 기존 설정 `gpt-5.6-luna`로 첫 4회, 시간 범위 보강 후 잠금→회복 2회, 총 6회 호출했으며 자동 재시도는 없다. 모든 호출에서 모델 유무에 따른 코드 관측은 동일했다. 이는 코드 격리 검증이며 모델 정답률이 아니다.

| 상황 | 모델 원문에서 확인한 내용 / 한계 |
| --- | --- |
| 요청 대기 + DB blocking 관계 | 잠금을 후보로 제시하되 요청과 DB 세션의 연결은 미확인으로 유지. blocker 트랜잭션·Worker 로그 대조 제안 |
| 같은 대기·lag + ready Worker 0 | Worker 가용성 부족을 다른 후보로 제시하고 상태·로그 확인 제안. 초기 v1 모델 호출 결과 |
| 상태 조회 503·지표 미수집 | 저장 여부 UNKNOWN 유지, 원인 가설 없이 상태/DB 조회와 추가 관측 제안. 초기 v1 모델 호출 결과 |
| 표본 저장·최신 lag 0·과거 peak 12 | 초기 응답은 Worker 런북 적용의 시간 범위가 모호함. v2는 HISTORICAL로 구분하고 80초를 실제 commit 지연이 아닌 관측 구간으로 설명 |

v2는 현재/과거 런북 조사 범위를 명시하고, 표본 전량 저장·최신 lag 0인데 현재 지연 조건을 MET으로 주장하면 보류한다. 최종 v2 프로토콜의 실제 모델 호출은 잠금/회복 두 시점만 수행했으며, 나머지 두 상황은 최종 코드의 결정적 회귀 테스트와 이전 v1 모델 응답 근거로 구분한다. 모델의 Kafka 런북 제외 판단은 해당 수락 요청 범위의 해석이며 전역 Kafka 정상이나 모든 catalog 조건 충족의 증거로 보지 않는다.

기존 실제 행 잠금의 세 snapshot은 모델 호출 없이 재생해 요청 대기→DB blocking 관계→21개 표본 전량 저장·최신 lag 0·과거 peak 15를 확인했다. 첫 재생은 DB 서버 시각이 수집기보다 약 1.7초 앞선 것을 오류로 취급했다. 서로 다른 시계를 직접 비교하던 검사를 수정해 collector freshness와 server timestamp를 분리했고 최초 실패도 보존했다. 새 장애 주입·배포·서비스 변경은 없다.

전체 테스트 **1,009개 통과(58.20초)**. 첫 CLI 회귀 테스트의 Windows stdout 인코딩 문제를 UTF-8 명시로 수정했다. 기록 hash 12개와 원문 응답 6개의 model_analysis 일치를 확인했다. 검토 주체는 Codex이며 독립 운영자 평가가 아니다. 넓은 추가 조회가 여전히 제안되므로 조회 수·작업 시간 감소나 런북 대비 우월성은 미입증이다. 개발에 사용한 사례를 미관측 정확도 평가로 재사용하지 않는다. [결과·원문 위치·실패·검토 한계](../results/ops-agent/diagnosis/grounded-assistance-20261102-summary.json).

## 모델이 조회하고 원문으로 설명 — 2026-11-01

Codex의 사후 해설을 모델 능력으로 섞지 않도록 `assist_incident.py --investigate`를 추가했다. 기존 잠금·회복 snapshot 각 1개로 검증했으며 새 장애 주입이나 실시간 조회는 없었다. 초기 요청에는 질문·scope·시각·선택적인 이전 모델 답변만 주고, Luna가 관측 도구를 호출한 뒤 검색어를 정해 런북을 조회했다. 설명 문장은 수정·보완하지 않고 `explanation.txt`에 그대로 출력한다.

두 시점 모두 모델이 관측 도구 5개 전부를 요청하고 다음 호출에서 런북을 검색한 뒤 최종 설명을 작성했다. 각 3회, 총 API 6회이며 자동 repair/retry는 없다. 잠금 시점에는 `room_sequences`의 transactionid lock 대기를 근거로 DEGRADED, 회복에서는 전량 저장·현재 lag 0을 근거로 NORMAL이라고 설명했다. 요청한 도구와 반환 근거, 최종 인용, 모델 JSON과 표시 문장 일치, record hash를 확인했다. 전체 테스트 968개 통과(53.18초).

**의미적 오류는 그대로 남겼다.** 후속 답변은 수락부터 persisted 조회 시각까지의 구간을 실제 저장 소요 시간처럼 표현했고, 이 서비스의 append-before-202 계약에도 불구하고 202 수락으로 append 성공을 판단할 수 없다고 설명하며 Kafka 런북을 다시 추가 확인으로 바꿨다. 과거 원인·재시작의 시간적 관련성도 추가 보강이 필요하다. 도구는 모두 조회했으므로 선택적 조사나 조회 비용 절감의 성과가 아니며, 이미 본 장애의 개발 재생으로 일반 정확도·안정성을 주장하지 않는다. [Luna 원문·도구 이력·검토 한계](../results/ops-agent/diagnosis/model-selected-20261101-summary.json).

## 적은 요청으로 재현한 DB 행 잠금 — 2026-11-01

새 stream의 `room_sequences` 한 행에 50초간 `FOR UPDATE` 잠금을 유지했다. 장시간 트랜잭션이 후속 쓰기를 막는 상황이며, 기존 DB·Worker 런북의 증상과는 겹치지만 색인된 절차에는 DB blocking PID 조사나 이 장애의 해제 답안을 추가하지 않았다. PostgreSQL의 [행 잠금·트랜잭션 종료 규칙](https://www.postgresql.org/docs/17/explicit-locking.html)과 [세션 timeout](https://www.postgresql.org/docs/17/runtime-config-client.html)을 사용해 주입 수명을 제한했다. 배포·HA 설정과 행 값은 변경하지 않았다.

완료한 실행에서는 **21건 모두 수락됐지만 장애 중 15건이 미저장**이었다. 대상 stream의 12건 외에 대조 stream 요청 3건도 지연됐으며, readiness는 정상이었다. DB 관측에서 `room_sequences` upsert가 특정 트랜잭션의 `transactionid` lock을 기다리는 관계를 확인했다. 자동 해제 후 21/21 unique request/event ID 저장, lag 0 연속 3표본, DB waiter·주입 세션 0, PG 3개 Pod UID·restartCount 불변을 확인했다. 전체 테스트 958개 통과(57.60초).

| 모델에 순서대로 제공한 관측 | 결과와 검토 |
| --- | --- |
| 수락·저장 상태와 lag, readiness | DEGRADED, 원인 미확정. Worker 런북 적용, DB 런북 추가 확인 |
| 후속 관측과 실제 DB 대기 관계 | `room_sequences` 갱신의 transactionid lock 대기를 원인 후보로 좁힘. DB 런북 적용으로 변경 |
| 전량 저장·현재 lag 0·DB 대기 없음 | 회복 사실은 설명했지만 최근 60초의 과거 lag로 DEGRADED 유지. 현재 잠금이 없다는 이유로 앞서 관측한 원인까지 흐리는 표현 |

API 3회 모두 형식·근거 검증을 통과했지만 **현재 상태와 사건 이력의 구분은 미흡**했다. 오래된 Worker 재시작도 계속 원인 후보로 남겼다. 다음 개선은 현재 영향·최근 영향·과거에 관측한 원인·현재도 진행 중인 원인을 분리하고, 이 실제 관측 순서를 회귀 사례로 쓰는 것이다. DB 조회와 시점 선택은 Codex가 수행했으며 모델은 주입 계획과 미래 결과 없이 종료 후 기록을 재생했다. 자율 조사·일반 정확도 성과로 해석하지 않는다.

첫 실행은 상대/절대 경로 처리 오류로 모델 호출 전에 실패했다. 원본과 실패를 보존했고, 잠금 세션 종료 및 그 실행의 21/21 저장을 별도 확인했다. 경로 수정과 회귀 테스트 뒤 새 실행으로 검증했다. 두 실행의 raw 40개 hash와 모델 record hash 3개를 확인했으며, [결과·최초 실패·남은 한계](../results/ops-agent/diagnosis/row-lock-20261101-summary.json)에 함께 기록했다. 새 런북 문서는 만들지 않았다.

## 실제 부하 관측과 진단 연결 — 2026-11-01

정상 local-ha에 20초·최대 300건/초·총 6,000건·동시 32개로 제한한 요청을 보냈다. 두 회차 모두 readiness는 정상으로 관측됐으며 Worker는 2→4개로 자동 확장했다. 설정 변경이나 데이터 초기화는 없고 테스트 사용자·stream·event는 남는다.

| 회차 | 실제 수락 / 평균 전송률 | 관측 최대 lag | 부하 후 확인 |
| --- | --- | --- | --- |
| 1 | 1,277건 / 약 64건/초 | 360 | lag 0 연속 3표본, 선택한 60건 persisted |
| 2 | 4,447건 / 약 222건/초 | 3,620 | lag 0 연속 3표본, 선택한 60건 persisted |

첫 회차에서 Windows `monotonic`의 15.625ms 정밀도가 전송률과 latency 측정을 제한해 `perf_counter`로 수정했다. 두 번째 회차의 수락 응답 p95는 약 54ms였다. 이는 persistence latency가 아니며, 최대 처리량·DDoS 방어·전량 저장을 입증하지 않는다. 원본 124개 hash와 수락 ID 유일성·표본 저장 결과를 재확인했다.

모델에는 종료 후 선택한 정상·최대 적체·회복 snapshot을 순서대로 재생하려 했다. **두 회차 모두 적체 시점에서 실패해 회복 설명 갱신은 미검증이다.** 첫 실패는 3,200 output token 한도에서 JSON 잘림이었다. 인용 ID 단축·간결한 출력 지시·6,000 token 상한으로 수정한 다음 회차는 JSON 해석에 성공했지만, DEGRADED의 근거에 정상 lag 증거와 미수집 commit 지표를 함께 인용해 validator가 거부했다. 미수집 지표는 한계를 설명하는 문맥으로 사용했으므로, 수치 조작과 구분하고 **심각도 지지 근거와 불확실성 근거의 역할 분리**가 필요하다.

내용 검토에서도 Kafka bootstrap 정상만으로 intake 장애를 EXCLUDED한 것은 근거보다 강한 결론이었다. 실험에서 확인한 요청 수락·저장 결과가 모델 입력에는 연결되지 않아 Worker 런북은 추가 확인으로 남았다. 다음 작업은 **시점에 맞는 요청 수락·저장 증거 연결 → 근거 역할 분리 → 동일 3시점 재검증**이다. 실패를 없애기 위해 validator를 완화하거나 결과를 성공으로 바꾸지 않았다. API 4회 중 정상 시점 2개만 기록 완료했으며, 전체 테스트는 954개 통과(62.87초). [결과·실패·원본 hash](../results/ops-agent/diagnosis/local-burst-20261101-summary.json).

## 런북 조사 최소 흐름 — 2026-11-01

`assist_incident.py`로 기존 canonical evidence와 런북 원문 3항목을 연결했다. 모델 `gpt-5.6-luna`로 직접 작성한 합성 3상황·4시점을 각 2회 실행했으며, 후속 시점은 해당 반복의 이전 답변만 제공했다. 정답·반복 횟수·입력·원문·코드를 호출 전에 동결했다. 실제 서비스 수집이나 조치는 수행하지 않았다.

| 상황 | 두 실행에서 확인한 결과 |
| --- | --- |
| DB 연결 실패 직접 증거 | PostgreSQL 런북 APPLICABLE, 영향 DEGRADED |
| endpoint 불명 HTTP 503·Kafka 수집 불능 | Kafka 런북 NEEDS_MORE_EVIDENCE, 영향 UNKNOWN |
| 후속: Kafka 수락 정상·DB 장애 증거 | Kafka EXCLUDED, PostgreSQL APPLICABLE, 영향 DEGRADED. 변경 근거 인용 |
| TLS 인증서 만료 임박·관련 런북 없음 | 후보 없음, 영향 UNKNOWN. 인증서·체인 확인 제안 |

기대 적용 판단은 8/8이며 4시점 모두 두 반복의 적용 여부·심각도가 일치했다. 이는 소규모 개발 pilot으로 일반 안정성·모든 권고의 일관성을 입증하지 않는다. API 8회, 입력 24,894·출력 10,778 tokens. Codex 검토에서 쓰기 조치는 제안하지 않았으나, 다음 확인이 4–5개로 길어지는 경우와 초기 관측에 “불확실성이 증가”했다고 표현한 사례가 있었다. 검색만 제공한 비교군·운영자 시간 절감·자동 지식 축적은 미검증이다. [결과와 원본 hash](../results/ops-agent/diagnosis/runbook-assistance-20261101-summary.json).

전체 테스트 948개 통과(52.96초), 이후 코드 출처 기록 보강의 관련 테스트 18개 통과. 호출 중 추가한 영향 근거 validator는 모든 저장 응답에 오프라인 재적용해 통과했으며 API 재시도는 없었다. 호출 코드의 기준은 실행 전 동결본이다. 초기 step별 코드 hash가 실행 중 디스크 변경을 반영한 한계와 import 시점 hash로 수정한 내용은 위 요약에 구분해 기록했다. 형식·인용·조건 일관성 검사는 해석의 의미적 정확성을 보증하지 않는다.

<a id="model-evidence"></a>
## 기능별 대표 검증

| 기능 | 확인한 결과 | 해석의 한계·증거 |
| --- | --- | --- |
| backlog 발생·회복·incident 기록 | 과거 local-ha 75→330→75 records/s 실행에서 탐지·회복·종료 기록, 133 bundle·532 raw projection 보존 | deterministic 운영 검증. 모델의 원인 정확도 점수가 아님. 로컬 `results/ops-agent/incidents/inc-88a1eeaa17897f6a8a929bba/` |
| 구조화 진단·근거 인용 | v3.5 합성 개발·새 조합에서 구조화 출력과 근거 선택 확인 | 같은 사례의 고정 규칙도 통과. 현재 운영 정확도나 판단 안정성으로 일반화 불가. [결과](../results/ops-agent/diagnosis/v35-second-probe-20261029-summary.json) |
| 실제 증거 adapter | 과거 실제 backlog snapshot에서 모델·Runbook 모두 ABSTAIN. 정상 snapshot은 API 호출 생략 | 운영 stage 기준선 없음, 설명문 품질 문제 별도. [adapter 결과](../results/ops-agent/diagnosis/runtime-adapter-20261030-summary.json) |
| 실제 Worker 시작 오류 조사 | 명령 오류 후 Worker 0/2, 20건 accepted·조회 404, 모델이 누락 모듈 오류 식별. 원래 설정 복원 후 20/20 persisted | 정확한 복원 명령은 특정하지 못함. PG 불안정과 Recreate 전략 변경 동반. [원본 연결 요약](../results/ops-agent/diagnosis/worker-startup-fault-20261101-summary.json) |
| 실제 PG 재시작 조사 | 모델이 startup probe 필요성 제안. standby 2개에 적용 후 183.5초 범위의 정상 표본 13개, probe 집계 20/20 persisted | Pod 재생성·순차 교체도 동반. 단독 원인 확정·장기 안정성·HA 검증 제외. [복구 기록](../results/ops-agent/diagnosis/postgres-restarts-20261101-summary.json) |

Worker·PG 진단은 `gpt-5.6-luna`를 사용한 각 1회 사례다. 모델은 먼저 수집된 snapshot을 도구로 조회했고, 복구는 Codex가 검토·구체화해 수행했다. 모델이 스스로 장애를 발견하거나 쓰기 명령을 실행한 것은 아니다.

Worker에 제공된 로그에는 실제 `No module named worker.consumer`가 있었다. 최초 입력에 주입 원인·private plan은 없었지만, 사람이 조사 범위와 도구를 미리 선택한 제한된 과제였다. 이 두 사례의 판정 기준과 입력은 합성 v3.5 평가와 달라 한 점수로 합치지 않는다.

<a id="development-history"></a>
## 남겨야 할 설계 판단

| 발견한 문제 | 설계 판단과 확인한 범위 |
| --- | --- |
| 형식은 맞아도 내용이 틀림 | 형식과 품질 평가 분리. 초기 contract 8/8·quality 1/8 실패를 보존. [근거](../results/ops-agent/diagnosis/pilot-prompt-v2-20261029-summary.json) |
| 근거 없이 조기 결론·잘못된 인용 가능 | 관측/가설/문맥 분리, 최소 조사, 제공된 ID 선택 도입. 내용의 정확성은 별도 평가. [조사 gate](../results/ops-agent/diagnosis/investigation-v34-regression-20261029-summary.json), [인용 검증](../results/ops-agent/diagnosis/citation-v35-regression-20261029-summary.json) |
| 반복한 개발 사례 점수로 일반화 불가 | 새 조합·최초 실패·repair 분리. 새 사례의 정상 PG 오판도 보존. [근거](../results/ops-agent/diagnosis/pilot-v3-novel-20261029-summary.json) |
| 실험 코드가 잘못된 성공을 만들 수 있음 | 수락/저장 ID, 조회 실패, 빈 진단, 복구 조건을 반례로 검사·보강. [근거](../results/ops-agent/diagnosis/experiment-validation-fixed-20261101-summary.json) |
| 원인 정답만으로 운영 도움을 입증하기 어려움 | 같은 증거로 고정 규칙과 비교. 우위 미확인 결과를 보존하고 현재 조사·런북 적용·후속 갱신을 사용 흐름으로 평가. [근거](../results/ops-agent/diagnosis/paired-investigation-20261101-summary.json) |

모델 가중치는 변경하지 않았다. 개발 사례 점수와 코드·프롬프트 개선을 모델 학습 효과나 독립 정확도로 표현하지 않는다. 버전별 점수·최초 실패·repair 결과는 `results/ops-agent/diagnosis/*summary.json`과 로컬 원본에 보존한다.

<a id="followup"></a>
## 시간 연결과 관측의 검증

후속 진단은 과거 activation을 최신 snapshot에 임의로 붙이지 않도록 구현했다. `FollowupReplay`는 동일 incident/source/scope의 현재까지 prefix만 사용하고 첫 UNKNOWN에서 BLOCKED, 최초 RECOVERED에서 CLOSED로 고정한다. 미래 값이나 나중의 정상 상태로 공백을 지우지 않는다.

과거 [후보 감사](../results/ops-agent/diagnosis/runtime-case-audit-20261031-summary.json)·[시간 감사](../results/ops-agent/diagnosis/incident-followup-audit-20261031-summary.json)·[음수 lag 대조](../results/ops-agent/diagnosis/negative-lag-review-20261031-summary.json)는 나중의 상태를 과거 판단에 붙이지 않아야 함을 보여줬다. [후속 replay](../results/ops-agent/diagnosis/followup-replay-20261031-summary.json)는 증거 연결/차단을 구현했지만 실시간 LLM은 미연결이다. 현재의 영구 BLOCKED 정책을 지속 감시에 그대로 적용하지 않으며, 공백을 보존하는 진단 재개 조건이 필요하다.

[한정 트래픽 관측](../results/ops-agent/diagnosis/live-observation-20261031-summary.json)과 [첫 watch](../results/ops-agent/diagnosis/continuous-watch-20261031-summary.json)는 처리 확인과 수집 품질 경고를 구분했다. 이후 30분 watch는 약 24분 15초·98 capture에서 중단되어 완료 summary가 없다. 1,181 accepted 중 당시 완료 batch에서 1,140건만 저장 확인했으며, 나머지 41건 미확인은 데이터 손실 또는 무장애 성공의 근거가 아니다.

<a id="known-gaps"></a>
## 감사에서 확인한 결함과 수정 검증

[오프라인 감사](../results/ops-agent/diagnosis/live-experiment-audit-20261101-summary.json)는 원본 변경·모델 호출·장애 주입 없이 다음 반례를 재현했다. 이 문제들은 최근 watcher·probe·실험 runner의 결함이며, 기존 v3.5 validator와 구분한다.

| 우선순위 | 결함 | 수정 후 반례 결과 |
| --- | --- | --- |
| 높음 | 조회 실패와 미저장 구분 누락 | 401·503 거부. 수락 ID 전체를 조회한 유효한 404/queued만 stall 허용 |
| 높음 | 수락/저장 건수와 ID 유일성 검사 누락 | 중복 request ID에서 FAILED·exit 1. 정상 완료는 모든 ID의 서로 다른 event ID와 건수 일치 필요 |
| 높음 | 빈·미조사 모델 응답 gate 부재 | 빈·미조사 응답 거부, 성공 파일 미생성. JSON 필드와 조회한 근거 인용 검사 |
| 높음 | 연결 실패 시 watcher 예외 | `http_status=None`은 수집 불능 경고. WATCH_FAILED를 probe 실패로 덮어쓰지 않음 |
| 중간 | 예상 PG Pod 집합 검사 누락 | 새 verifier에서 예상 3 Pod·UID·restart·HTTP 200·readiness·표본 간격 검사. primary만 있으면 실패 |
| 중간 | watchdog의 복원 대조 부족 | 원래 컨테이너·전략·자동 sync 필드 불일치 시 복원 완료 기록 거부 |

수정 후 [오프라인 감사 요약](../results/ops-agent/diagnosis/experiment-validation-fixed-20261101-summary.json)을 별도로 보존했다. 기존 원본 hash 70개는 그대로 일치한다. 과거 PG verifier는 원본으로 동결하고 새 `verify_postgres_recovery.py`를 제공한다. 새 verifier는 mock 수집기를 통한 검증까지 수행했으며 실제 클러스터에서 복구 관측을 재실행한 결과는 아니다. 새 probe는 status GET별 시각·HTTP 상태·상태 필드·event ID를 보존한다. 이 수정으로 과거에 누락된 증거가 복원되는 것은 아니다.

실제 Worker의 20개 request ID와 recovery ID는 일치했고 서로 다른 event ID 20개가 기록됐다. PG probe의 accepted ID도 20개 모두 고유했다. 따라서 반례가 존재한다는 이유만으로 실제 두 실행을 전부 허위 성공으로 취급하지 않는다. 관련 테스트 11개가 통과하면서 이 반례들을 놓쳤으므로, 전체 suite 통과 역시 판정 타당성의 대체 근거가 될 수 없다.

보존·해석상의 추가 제한:

- 요약에 기록된 hash 70개는 원본과 일치. 수집 사실의 완전성까지 보장하지 않음
- 모델의 tool output은 저장 evidence와 일치. 별도의 자유 조사나 Runbook 대비 가치 검증은 아님
- PG 183.5초는 최대 15.678465초 간격의 표본 13개. 관측 사이 무중단을 보장하지 않음
- 실행 중 UID 비교는 수행했지만 sample projection에 UID 미저장. 독립 재검산 제한
- PG 관측 기준은 수정 적용 후 관측 시작 전에 기록. 개입 전 사전 등록이 아님
- PG 신규 probe는 각 status GET 응답·event ID를 저장하지 않아 20/20 집계의 독립 재계산 제한
- payload·metadata 일치, 순서, 중복, 개별 이벤트 지연 SLO는 해당 probe에서 미검증
- PG 수정은 startup probe·Pod 재생성·순차 rollout을 함께 수행. 단일 변수 인과 실험이 아님

## 현재 운영 적용 범위

최근 PG 수정은 소스 Helm values 변경과 로컬 StatefulSet patch다. standby ordinal 1·2에만 적용했고 primary의 UID·restartCount를 보존했다. **partition 1 유지, primary startup probe 적용·Helm upgrade 미완료** 상태다. partition을 제거하면 primary까지 rollout될 수 있으므로 [운영 주의사항](RUNBOOK.md#ops-agent-local-pg-correction)을 따른다. 이 문서 정리에서 런타임은 변경하지 않았다.

<a id="operational-assistance-evaluation"></a>
## 지속적인 운영 판단 보조의 평가 기준

[고정 목적](OPS_AGENT.md#design-direction)에 따라 경미한 오류·중대 장애·원인 불명·정상·수집 불능을 포함한다. 아래는 운영 수준의 기능 평가 항목이며 현재 통과한 결과가 아니다. 각 변경에 필요한 항목을 선택하며 모든 항목을 실험 기능 병합의 일괄 선행 조건으로 사용하지 않는다. 기존 각 1회 진단과 합성 pilot은 판단 안정성을 입증하지 않는다.

| 확인할 능력 | 검증 방법·실패 기준 |
| --- | --- |
| 정확하면서 일관된 판단 | 같은 증거·문맥·모델 설정을 독립 실행으로 반복. 심각도·원인 확신·권고 행동의 분포와 충돌을 기록. 문장 차이와 행동 충돌을 구분하고 반복해서 같은 오답을 내는 경우도 실패로 계산 |
| 근거에 따른 판단 갱신 | 동일 사건의 증거를 시간 순서로 공개. 이전 판단과 변경 근거를 기록. 미래 증거 사용, 반대 증거 무시, 새 근거 없는 중대한 권고 반전을 실패로 기록 |
| 유용한 판단 보류 | 누락·오래된 증거·충돌을 정상으로 치환하거나 원인을 단정하면 실패. ABSTAIN 여부와 별개로, 다음 조회가 실제로 원인 후보를 구분하는지 평가 |
| 런북 검색과 적용 판단 | 맞는 런북·증상만 유사한 런북·자료 없음 상황 비교. 원문·버전 인용, 충족·반대·미확인 조건과 다음 조회 검사. 유사도를 원인 확정이나 조치 허가로 바꾸면 실패 |
| 경험의 지식 축적 | 관측·가설·실행 조치·결과를 분리한 초안과 기존 항목 수정안 검사. 미검증 설명을 검증된 절차로 승격하거나 합성 결과를 실제 운영 증거로 기록하면 실패 |
| 경미·중대 상황의 적절한 전달 | 놓친 이상과 과도한 경보·권고를 각각 기록. 심각도와 허용 조치는 사전 기준에 따라 평가. 근거 없는 위험 조치 권고는 정확도 평균에 묻지 않고 별도 실패로 보고 |
| 운영자에게 주는 도움 | 같은 증거 접근을 제공한 Runbook과 조사 순서·유용한 확인·지연·토큰을 비교. 사람의 재조사 부담·시간 절감은 실제 사용자 평가 전까지 미측정으로 표시 |
| 변경 후 품질 유지 | 모델·프롬프트·도구 변경 전후를 같은 고정 사례와 시간 순서 사례로 비교. 안전성·정확성·일관성·비용 결과를 보고 적용 범위를 결정 |

실행 전 사례·정답/허용 판단·반복 횟수·예산·비교 버전·합격 기준을 고정한다. 반복 실행은 최초 실패를 포함해 전부 집계하며 만점이 나올 때까지 반복하지 않는다. 주입 원인과 미래 증거는 모델에게 숨기고, 모델 식별자·설정·프롬프트·도구·입력 hash·원본 응답·실패·repair를 보존한다. 개발 사례와 독립 사례, 합성 상황과 실제 관측은 분리한다. 판단 보류는 적절한 진단 결과일 수 있지만 서비스 복구 성공으로 집계하지 않는다.

구현 순서는 [단일 계획](OPS_AGENT.md#implementation-priorities)을 따른다. 첫 평가 묶음은 런북 적용 가능·증상만 유사·자료 없음의 세 상황과 반대 증거를 추가한 후속 시점이다. 원문/버전 인용, 조건 대조, 다음 확인, 근거 있는 갱신을 확인하고 같은 입력의 반복 결과도 보존한다. 검색만 제공한 경우와 현재 조사까지 결합한 경우를 비교한다. 사람의 시간 절감과 판단 충돌 처리·후속 LLM 연결은 아직 미검증이다.

## 같은 증상에서 순차 조사 비교 — 2026-11-01

초기 입력은 세 사례 모두 “20건 수락 후 45초간 조회 404, lag 100→120”이다. 모델 `gpt-5.6-luna`, 프롬프트, 도구, 정답·인용 기준, 전용 Runbook 규칙을 호출 전에 저장했다. 실제 클러스터에 장애를 주입하지 않은 **직접 작성한 합성 개발 사례 3개·각 1회**다. 후보 원인 목록을 제공했으므로 열린 문제나 독립 정확도 평가가 아니다. 전용 Runbook은 Worker 상태→로그→DB→Kafka 순서로 조회하되 직접 근거가 있으면 조기 종료한다. 양쪽에 같은 증거 접근을 제공하며, 모델은 요청한 증거만 한 번에 하나씩 받는다.

| 사례 | 모델 / Runbook 결론 | 모델 / Runbook 조회 수 | 모델 API 시간 |
| --- | --- | --- | --- |
| Worker 시작 모듈 오류 | 모두 시작 실패 식별 | 2 / 2 | 10.19초 |
| Worker INSERT의 DB 잠금 대기 | 모두 잠금 경합 식별 | 4 / 3 | 12.97초 |
| 로그·DB 수집 권한 부족 | 모두 ABSTAIN | 4 / 4 | 13.77초 |

원인 코드와 필수 근거 인용은 양쪽 모두 3/3이다. 모델은 총 13 API 호출, 입력 9,054·출력 2,554 tokens, API 대기 합계 36.92초를 사용했다. 도구 결과는 로컬 fixture이므로 실제 조회 지연이나 사람의 조사 시간 절감으로 환산하지 않는다. 모델의 조사 10회는 Runbook의 9회보다 많아 **이번에는 정확도·조사 효율 우위를 확인하지 못했다**. 최초 출력·조회 순서·가설 checkpoint를 보존했고 결과를 본 뒤 재시도·repair하지 않았다.

Codex의 사후 문장 검토에서, 시작 실패의 세부 원인을 빌드/명령 오류 중 하나로 단정하지 않은 점과 수집 불능 시 추가 읽기 권한 확보를 제안한 점은 적절했다. DB 답변의 “이미 누락된 이벤트의 존재 여부”는 누락 증거 없이 불필요한 전제를 도입한 표현이다. 분류·인용 통과를 자유 서술 전체의 정확성으로 확대하지 않는다. 모델 교체보다 다음 조사 질문의 유용성과 과도한 단정 억제를 평가할 근거다. [결과·원본 hash](../results/ops-agent/diagnosis/paired-investigation-20261101-summary.json).

## 원본과 재현

실행 명령은 [CLI 안내](../ops_agent/README.md)에 둔다. 로컬 원본은 Git-ignored 경로라 저장소만 내려받으면 모두 재실행할 수 있는 것은 아니다.

| 자료 | 위치 |
| --- | --- |
| 합성 모델 평가 원본 | `results/ops-agent/quality-evaluation/` |
| Worker 실제 장애 | `results/ops-agent/shadow/20261101-worker-startup-fault-01/` |
| PG 조사·수정·관측 | `results/ops-agent/shadow/20261101-postgres-restarts-01/` |
| 미완료 watch | `results/ops-agent/shadow/20261031-service-watch-30min/` |
| 최종 감사 반례 | `results/ops-agent/shadow/20261101-live-experiment-audit-03/` |
| 공유용 요약·hash | `results/ops-agent/diagnosis/*summary.json` |

이전 문서 본문은 로컬 `results/ops-agent/shadow/20261101-doc-consolidation/before.zip`과 방향 확정 전 `results/ops-agent/shadow/20261101-direction-confirmed-docs/before.zip`에 보존했다. 이번 정리는 설명 중복과 회차별 나열에 한정하며, JSON·로그 원본·과거 실패·코드·런타임 설정은 유지한다.

## Public Controlled Scenario Replay Candidate - 2026-08-29

`demo-dev` UI `2.5.0`은 기존 adaptive Scenario Lab 실행을 새로 수행하지 않습니다.
검증된 네 run에서 허용된 필드만 `demo.verified-scenario-replays.v1`로 투영하고,
브라우저는 recorded condition, tool selection, observation, next branch, hypothesis와
validator 결과를 순서대로 재생합니다. OpenAI API, runtime source, runtime write는
호출하지 않습니다.

Worker replica shortfall scenario의 Deployment `current=4`, `ready=2`, `available=2`와
KEDA `current=4`는 같은 `2026-08-23T15:32:08Z` capture입니다. 이 evidence는
`available 2 → KEDA scale-out → current 4` 시간 흐름을 증명하지 않습니다. UI는
Deployment availability와 scaler-observed replica count를 별도 label로 표시합니다.
Normalizer의 `WORKER_CAPACITY_SHORTFALL`은 관측 분류이고, Agent의
`WORKER_CAPACITY_SHORTFALL_SUSPECTED=SUPPORTED`는 그 evidence를 인용한 가설
판단입니다. Grounding Validator는 schema와 citation 계약을 검사하며 causal truth를
확정하지 않습니다.

이 candidate는 commit `a67f40e`, focused `59 passed`, full `363 passed`로 검증됐습니다.
현재 public demo-lite는 UI `2.4.1`이므로 배포 완료로 기록하지 않습니다.
