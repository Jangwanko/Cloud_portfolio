# Ops Agent Live Baselines

이 디렉터리는 실제 runtime source에서 수집한 read-only Evidence Bundle을 보존합니다. `ops_agent/fixtures/`의 synthetic test fixture와 용도가 다릅니다.

## `no-backlog-20260812.json`

- 실행 시각: `2026-08-12T00:39:07.614065Z` - `2026-08-12T00:39:09.625601Z`
- profile/context: `local-ha` / `kind-messaging-ha`
- namespace/topic/group: `messaging-app` / `message-ingress` / `message-worker`
- bundle ID: `ac4d45ac-6280-4eb7-9d35-ea735243f00b`
- collection: `PARTIAL`, 114 evidence
- 정상 기준: Kafka 8/8 partition lag `0`, Worker available `2/2`, PostgreSQL primary와 sync standby `2`, Argo CD `Synced / Healthy`
- partial 이유: 최근 60초 실제 처리 event 부재로 label-on-use Worker metric 2개 `MISSING/UNKNOWN`
- source: Git `004f2e7791543de2d570c287cf8938410c61807c`, dirty worktree
- collector tree SHA-256: `5c7055011d474ba94ef15f9a21e49b09062e4f96bda0aa79c8ce426f9609c72f`
- bundle SHA-256: `0ba45dad286394ba24891806974a6818dfb9177e338775a9d2e667cc5a5f3242`

## `no-backlog-20260812.conditions.json`

- 입력: 위 `ops.evidence.v1` capture
- 출력 schema/policy: `ops.conditions.v1` / `local-ha.conditions.v1`
- evaluation: `COMPLETE`; source collection status `PARTIAL` 보존
- canonical source bundle SHA-256: `c0471fcee17ff3ba1ce98960f33da061a9b29c86313f2b704bf8ab051e4743ba`
- evaluation ID: `b5477d8fc18bd7a6bda3e0edcb6627d734485c2d9c6643cb46b5adea1bdf3bdb`
- file SHA-256: `3bfd839efbfb1019b2d7023f3a134a2f86c1c5e4c1d1fbeabae24ce9da0d20f8`
- conditions: backlog pressure, partition concentration, DB degraded, Worker replica unavailable 모두 `ABSENT`
- assessment: `NO_BACKLOG_PRESSURE_DETECTED=PRESENT`
- 생성 방식: source 재조회 없이 deterministic offline evaluation

Bundle의 `raw_ref`는 repository root 기준입니다. `results/ops-agent/raw/ac4d45ac-6280-4eb7-9d35-ea735243f00b/` 네 JSON도 함께 추적합니다.

| Raw source | SHA-256 |
| --- | --- |
| `application.json` | `e1abc2b9a14d0152b8c8d739a512e1caf11d3ac505bef5e1651ade831d9ce837` |
| `prometheus.json` | `5a95f106845c3c3c10957ec36671441a3ac5a6c2302cf8a716649cd6cf17330d` |
| `kubernetes.json` | `d3257d4a60887408be181c3a89fc32e4dbab5d85e6e2b22daa308fa437a0edbb` |
| `argocd.json` | `f2a42b0206878959762aef5805cd4bd093d5f6b55c41c70f1d471eb7765a0145` |

각 파일은 source-specific safe projection과 credential redaction 뒤 저장됐으며 bundle의 `raw_sha256`과 일치합니다. 이 capture에서 known credential/token/JWT/private-key pattern은 검출되지 않았습니다. Prometheus label은 open-ended이므로 이후 capture도 별도 secret scan을 통과해야 합니다.

이 capture에는 내부 Prometheus target IP, Kubernetes UID/resourceVersion, Pod suffix, runtime image digest, replica/HA topology가 포함됩니다. 공개 범위에서 운영 식별자 노출이 허용되는지 별도로 검토합니다.

이 파일은 no-backlog 운영 reference입니다. stable 성능 baseline, production SLA, node-level HA 증거로 사용하지 않습니다.
