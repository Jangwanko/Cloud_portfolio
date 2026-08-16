# Ops Agent

Phase 1 collection, the Phase 2 deterministic evaluators, and the Phase 3
Evidence-grounded Diagnosis Agent are implemented.
`ops_agent` collects normalized, read-only operational evidence for the Worker
backlog scenario. The v1 evaluator reads one frozen bundle; the calibrated v2
evaluator reads an ordered bundle sequence. The single Phase 3 Agent starts only
from `CORE_BACKLOG_PRESSURE=PRESENT`, selects fixed read-only investigation
tools, and emits `ops.diagnosis.v1`. It cannot change the condition, decide
recovery, or execute remediation.

The complete contract, live baseline, and Phase 2 boundary are documented in
[docs/OPS_AGENT.md](../docs/OPS_AGENT.md). Synthetic fixtures live under
`ops_agent/fixtures/`; captured runtime evidence lives under
`results/ops-agent/live-baseline/`. Controlled calibration summaries live under
`results/ops-agent/calibration/`.

## Evidence boundaries

- `messaging_worker_processed_total` is a terminal-processing counter. It is not
  a PostgreSQL commit or insert counter.
- `messaging_event_persist_lag_seconds` observes the path from the API-provided
  `queued_at` timestamp to the Worker's post-commit timestamp. It includes Kafka
  wait, retry, Worker, and database work and is not isolated transaction commit
  latency.
- `messaging_worker_stage_latency_seconds{stage="db_persist"}` ends before the
  request-status update and `conn.commit()`.
- Kafka offset `-1`, missing partitions, partial coverage, and offset decreases
  remain explicit evidence. Collectors never coerce them to zero.

The versioned `local-ha.yaml` policy uses JSON syntax, which is a valid YAML
subset. This keeps Phase 1 free from an additional YAML runtime dependency.
Freshness defaults follow the current 5-second Prometheus scrape and 15-second
`/ops/summary` cache contracts. They are evidence-age limits, not incident or
recovery thresholds.

Source artifacts are written only after source-specific safe projection and
recursive credential redaction. The bundle contains the artifact path and
SHA-256; future diagnosis receives normalized evidence rather than source
artifacts.

Bundle provenance records the repository `HEAD` as `source_sha`, the repository
dirty state, and a SHA-256 over the Python collector and policy files actually
used for the run. This keeps an uncommitted collector run distinguishable from
its Git baseline.

New collections also record credential-free Application and Prometheus
endpoint identities, including canonical base URL, Host routing, and whether
the value came from policy or a trusted operator override. This is transport
configuration provenance; it is not remote-server attestation.

The collector invokes only HTTP GETs and fixed `kubectl get` or
`kubectl config current-context` commands. Existing kubeconfig credentials are
not claimed to be least-privilege credentials.
The Windows local profile connects to `127.0.0.1` with `Host: localhost` to
avoid the known `urllib` localhost delay while retaining the Ingress host route.
HTTP redirects are not followed. Windows kubectl execution is restricted to an
`.exe` binary. URL, context, and executable overrides are trusted local operator
inputs; they are not exposed as an unauthenticated service API. Ambient HTTP
proxy variables are ignored so loopback evidence cannot be redirected through
another endpoint.

## CLI

```powershell
python -m ops_agent collect --profile local-ha --context kind-messaging-ha
python -m ops_agent collect --profile local-ha --context kind-messaging-ha --output results/ops-agent/evidence.json
python -m ops_agent collect --profile local-ha --context kind-messaging-ha --incident-id phase1-live-validation --output results/ops-agent/evidence-live.json
python -m ops_agent evaluate --input results/ops-agent/evidence.json --output results/ops-agent/conditions.json
$inputs = Get-ChildItem results/ops-agent/calibration/20260816T032411Z/run-01/bundles/sample-*.json | Sort-Object Name | ForEach-Object FullName
python -m ops_agent evaluate-sequence --input $inputs --output results/ops-agent/conditions-v2.json
$conditions = 'results/ops-agent/sequence-replay/20260816/positive-run-01.conditions.json'
python -m ops_agent diagnose --conditions $conditions --input $inputs --output results/ops-agent/diagnosis/positive-run-01.json --live
.venv\Scripts\python.exe scripts\worker_backlog_calibration.py --runs 3 --streams 64 --vus 100 --duration 30s --think-time 0.05 --sample-interval-seconds 15 --context kind-messaging-ha
.venv\Scripts\python.exe scripts\worker_backlog_negative_controls.py --sample-interval-seconds 15 --context kind-messaging-ha
```

Without `--output`, the bundle is written to stdout. With `--output`, the JSON
result is atomically written to that path and stdout contains the path. Redacted raw
artifacts default to `results/ops-agent/raw/` or an output-adjacent `raw/`
directory.

`evaluate` accepts only a strict `ops.evidence.v1` JSON file, enforces a 16 MiB
local input limit, and emits `ops.conditions.v1`. The evaluator uses freshness
already frozen in the source bundle and performs no network, Kubernetes, or
artifact access. A `PARTIAL` source collection can produce a `COMPLETE`
evaluation when every condition's required evidence is usable.

The evaluator bounds evidence count, identifier and label sizes, binds the
canonical source-bundle SHA-256 and evaluator/ruleset versions into the
evaluation ID, and verifies the ID again before serialization. Kafka absence
requires aligned range/source/collection timestamps and a common raw source.
The local-ha DB rule accepts only the deployed PostgreSQL readiness reasons and
returns `UNKNOWN` when their component values conflict with the versioned HA
guardrails.

The v1 evaluator proves backlog and partition concentration `ABSENT` only when
all 13 samples in the fixed 60-second Kafka window have zero lag. Positive lag
remains `UNKNOWN` until pressure and concentration sustain policies are
calibrated. A one-snapshot Worker replica shortfall also remains `UNKNOWN`
because it cannot prove the two-minute availability grace.

`evaluate-sequence` accepts ordered `ops.evidence.v1` files and emits
`ops.conditions.v2` under `local-ha.conditions.v2`. It preserves the v1 gate
for every bundle and marks `CORE_BACKLOG_PRESSURE=PRESENT` only when three
adjacent captures each have total lag at least `7,000` and 60-second lag slope
at least `100 records/s`, with total lag increasing across both transitions.
Produce rate minus committed-offset rate is checked only for arithmetic
consistency with lag slope. KEDA, Worker replica, and Worker stage latency are
retained as context and do not activate the condition.

All sequence bundles must have the same profile, scope, eight-partition set,
collector/endpoint configuration identity, and Kafka exporter identity. The
existing freshness, coverage, offset `-1`/decrease, full-grid arithmetic, and
timestamp provenance checks apply to every capture. Any required failure makes
the sequence condition `UNKNOWN`. The evaluation ID binds the ordered canonical
bundle digests, collection and Kafka source times, v2 policy, ruleset, and
result payload.

The Phase 2.5 calibration runner uses the existing multi-stream k6 workload and
current KEDA policy. It does not reset Kafka or PostgreSQL and does not patch
KEDA, HPA, or Deployment replicas. Every run starts from lag zero and Worker
`2/2`, samples complete Evidence Bundles through pressure and drain, and stops
only after KEDA and Worker return to the starting replica count. The 2026-08-16
three-run result and its negative controls calibrate the v2 activation policy.
## Phase 3 diagnosis boundary

`diagnose` requires explicit `--live`; normal pytest and GitHub Actions paths do
not call OpenAI. The model defaults to `gpt-5.6-luna` and can be changed through
`OPENAI_MODEL`. `OPENAI_API_KEY` is loaded from the process environment or the
Git-ignored `.env.local` file. The key is never included in prompts, IDs, logs,
artifacts, fixtures, or tracked files.

The Agent has four tool steps, four tool calls, 1,600 output tokens, a 30-second
request timeout, one bounded transport retry, and a separate
`max_output_repairs=1` semantic repair budget. Output repair receives the
machine-readable validator error and the existing structured result. It has no
tools and cannot add evidence. A second invalid result terminates as
`validation_failure` without writing a completed artifact. The allowlist is:

- `get_partition_lag`
- `get_worker_stage_latency`
- `get_worker_replica_status`
- `get_keda_status`
- `get_postgres_health`
- `get_application_readiness`
- `get_runtime_image`
- `get_pod_restart_status`
- `get_argocd_status`

Tools reduce the activation window's normalized Evidence Items and issue a new
diagnosis evidence ID. They do not expose raw bodies, arbitrary arguments,
PromQL, URLs, shell, or kubectl. Exact PostgreSQL commit/insert rate,
transaction commit latency, consumer rebalance events, and CPU throttling remain
unavailable. Rebalance therefore cannot be confirmed or excluded.

The deterministic output validator rejects fabricated evidence IDs, repeated or
unknown tools, step overruns, unsupported rebalance confirmation, condition
re-evaluation, recovery claims, and action/remediation codes. Hypotheses are
restricted to the versioned allowlist and contain supporting IDs, conflicting
IDs, and evidence gaps without a model-reported confidence percentage.

Offline golden evaluation and five output-repair fixtures use scripted model
responses and make no API calls. Live Diagnosis Run files are local ignored
artifacts under `results/ops-agent/diagnosis/`. The 2026-08-16 Luna rerun used
the captured positive run-01 sequence, selected four normalized tools, failed
the initial stop-consistency validation, and passed after one tool-free output
repair. The completed run stopped with `insufficient_evidence`; no causal
hypothesis was promoted beyond the available citations.

The negative-control runner applies that frozen candidate to a short burst, a
180-second sustainable high load, and a single transient lag spike. It treats
lag slope as the only growth signal; produce minus committed is an arithmetic
consistency check. The 2026-08-16 controls all returned `NOT_PRESENT`.
Replaying the captured sequences with v2 returns `PRESENT` for all three
positive runs and no `PRESENT` for short burst, sustainable high load, or
transient spike. The v1 single-bundle policy is unchanged. Recovery and
clearing hysteresis are not implemented.

The 2026-08-12 captured no-backlog reference is intentionally `PARTIAL`: two
label-on-use Worker series were absent after process restart. Kafka partition
coverage, Application, Kubernetes, PostgreSQL HA, and Argo CD evidence were
collected from the live runtime. Missing series remain `MISSING/UNKNOWN`; they
are not converted to zero.
