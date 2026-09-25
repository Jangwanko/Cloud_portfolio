# Deployment Status

## Public demo-lite — 2026-09-14 관측

| 항목 | 확인 결과 | 증거 경계 |
| --- | --- | --- |
| 공개 UI | `2.5.0` | 실제 HTML의 UI version 확인 |
| API | `2.1.0`, readiness `ready` | 공개 endpoint HTTP 조회 |
| Worker | available/desired `1/1`, max `2` | `/ops/summary`의 Prometheus 기반 응답 |
| Release / desired image | `3509ed0` / `a67f40e2a29b` | 2026-09-13에 확인한 demo-lite branch 설정; 서버 imageID 아님 |
| 서버 imageID·Argo sync·migration | 이번 관측에서 미확인 | 공개 endpoint 접근만 수행 |
| Outbox | demo-lite release source에는 미포함 | full master/local-ha와 다른 notification 경로 |

원본: [공개 endpoint 관측](harness/public-demo-20260914.json). Host UTC `2026-09-13T19:02:10Z`는 한국 시각 9월 14일입니다. 이 파일은 해당 시점의 기록이며 실시간 상태판이 아닙니다.

저사양 데모는 이벤트 수락·저장·관측과 recorded Agent investigation을 체험하는 입구입니다. HA·장시간 부하·Outbox 보장은 full-profile 증거를 따릅니다. UI 2.5.0의 controlled scenario는 정적 기록 재생이며 현재 서버의 실시간 AI 진단을 뜻하지 않습니다.

## Full local-ha

2026-09-11 dev image `5e8addfb10d0`의 migration·Outbox 기본 처리 관측은 [별도 runtime 기록](TEST_RESULTS.md#local-ha-runtime-2026-09-11)을 따릅니다. Master 이미지 `74405259cefd`의 게시 결과를 public demo나 master-targeted runtime 성공으로 해석하지 않습니다.

## 갱신 절차

1. 대상 profile과 context 또는 public-only 범위 지정
2. `scripts/check_portfolio_status.py` 실행, 저장된 JSON의 관측 시각·실패·미확인 검토
3. 공개할 필드만 검토한 snapshot을 `docs/harness/`에 보존
4. 현재 상태 설명은 이 문서를 참조하고, 과거 검증 기록은 날짜와 조건 유지

Git commit과 실행 image의 일치는 runtime imageID와 배포 조건까지 별도 확인해야 합니다. `public-only` 결과의 passed는 공개 endpoint 검사 범위의 통과입니다.
