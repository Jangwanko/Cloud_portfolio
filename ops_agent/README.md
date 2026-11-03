# Ops Agent — CLI and execution boundaries

현재 구현된 수집·진단·재생·실험 도구의 실행 안내다. 확정 목표와 구현 순서는 [설계 문서](../docs/OPS_AGENT.md#implementation-priorities), 결과와 한계는 [검증 기록](../docs/OPS_AGENT_EVALUATION.md)에 둔다. 로컬 통제 실험과 사건 경험 저장·검색을 연결했으며 상시 자동 감시는 후속 범위다. 과거 평가의 고정 Runbook 비교군은 코드 분류 규칙이다.

The model has read-only investigation tools. Deterministic code owns condition,
recovery and incident state. Experimental fault runners are separate operator
tools; they do not grant the model cluster write access.

## Evidence and runbook assistance

The default public JSON/text omits model `updates`; the original response and record
remain unchanged on disk. New API requests constrain `updates` to an empty list and
previous model updates are excluded from follow-up input. Code observations provide
the historical timeline. Other prose remains UNREVIEWED. `counter_evidence` and
`recovery_proposals` carry current citations; proposals require operator approval,
an available runbook ID or null, and verification observations. Abstention is allowed.

## Controlled local MVP

`scripts/ops_agent_mvp.py` reuses the existing collectors, PG/Worker drills and grounded
investigation. `preflight` reads only the fixed `kind-messaging-ha`/`messaging-app`
scope, rejects non-loopback Kubernetes endpoints, records readiness/lag/PG partition
and changes no workload. Review impact, stop conditions and rollback before approving
each real experiment. `--approval` records an actual operator decision; the flag itself
does not authorize a new fault.

```powershell
.venv\Scripts\python.exe scripts\ops_agent_mvp.py preflight --output-dir results/ops-agent/shadow/<new-preflight>
# Only after explicit approval of the concrete local plan:
.venv\Scripts\python.exe scripts\ops_agent_mvp.py execute --execute --scenario lock --approval <approval-reference> --output-dir results/ops-agent/shadow/<new-case>
# Choose --scenario worker for the separately approved startup drill.
.venv\Scripts\python.exe scripts\ops_agent_mvp.py archive --scenario lock --output-dir results/ops-agent/shadow/<case> --review <review.json>
.venv\Scripts\python.exe scripts\ops_agent_mvp.py search --query "Worker startup"
```

The approved run budgets at most three calls per scenario; this MVP uses only two,
one baseline and one during-fault snapshot. Diagnosis runs after safe rollback.
Recovery has a code-only record and is NEVER sent to Luna. Private injection/rollback
plans and review answers are excluded from model input. Each first request/response
or failure is retained with no model retry. Logs, Pod states and metrics captured
during the fault remain on disk even though diagnosis is post-capture.

PG uses a new owned stream row, a 50-second server-bounded lock and at most 21 events.
Worker uses the existing startup regression and 300-second rollback watchdog, with
one seed and 20 fault-period events. After either drill, owned Worker/PG configuration
hashes, PG Pod UIDs/restart counts and original Argo automated policy must match;
exact accepted request persistence and service readiness are checked by the drill.
Stop before another experiment on recovery/configuration failure and inspect the
drill's `release.json` or `rollback.json`/`restored.json` plus watchdog results.
Do not delete data or change PG rollout partition as a recovery shortcut.

The searchable `ops.incident-experience.v1` sidecar references chronological grounded
records and raw artifacts with verified hashes. It deliberately does not fabricate
a calibrated `CORE_BACKLOG_PRESSURE` activation/closure for these small faults.
Cases are `RECOVERY_VERIFIED_REVIEW_PENDING`; model quality and operator review stay
separate from code-verified recovery. `--exclude-incident` omits the current case
from lookup. Search requires no model or API key. `runbook-draft.json` is an observed
procedure draft, not an automatically published catalog entry. Runtime `readyReplicas`
can be omitted during outage; it stays UNKNOWN and explicit non-ready container
observations provide the separate outage evidence.

## Default assistance CLI

The default CLI computes code-owned observations before optionally asking Luna for
investigation. It retrieves three indexed sections of `docs/RUNBOOK.md` and retains
source versions, requests/responses and prior-record hashes. Model output has no
current-summary/severity field and cannot replace the deterministic observation object.
APPLICABLE means investigation relevance, not a proven cause or permission to act.

```powershell
# Offline: save fixed observations and retrieved sources; no model call.
.venv\Scripts\python.exe scripts\assist_incident.py --example similar `
  --output-dir results/ops-agent/shadow/<new-prepared-directory>

# One API call per step; follow-ups retain earlier code observations separately.
.venv\Scripts\python.exe scripts\assist_incident.py --example similar --live `
  --output-dir results/ops-agent/shadow/<new-first-directory>
.venv\Scripts\python.exe scripts\assist_incident.py --example similar --step 1 --live `
  --previous results/ops-agent/shadow/<new-first-directory>/record.json `
  --output-dir results/ops-agent/shadow/<new-next-directory>
```

Examples `matching`, `similar`, `unmatched` are synthetic. For captured evidence,
replace `--example` with `--bundle <ops.evidence.v1.json> --provenance recorded
--question "<operator question>"`. Select 1–40 evidence items, preserve their original
metadata, and keep the same incident ID, scope and profile for follow-ups. Inputs
are limited to 2 MiB and projected content to 90 KB. Freshness is relative to the
saved observation time; this CLI does not collect a new live snapshot.

The default saves `input.json`, `observations.json`, `history.json`, `plan.json` and `record.json` even
without a model call. Code reports only the listed requests as pending/persisted/unknown,
latest lag separately from historical peak, and observed DB blocking edges. Unsupported,
stale or incomplete data stays UNKNOWN; the older examples use custom metrics and may
produce UNKNOWN facts. Read errors do not prove stalled writes. Poll intervals are not
exact commit latency. DB server time is retained separately from collector freshness.

With `--live`, one request is allowed, at most 6,000 output tokens, 45-second timeout
and no repair/retry. Model transport errors produce FAILED; invalid output or a typed
contract conflict produces WITHHELD, with raw response/failure retained and CLI exit 1.
Both keep code observations in the record and standard output. Valid model analysis
remains UNREVIEWED: references/structured checks do not verify the truth or safety of
all prose. Runbook assessments declare CURRENT or HISTORICAL; historical peak cannot
establish current sampled-request delay after persistence with latest lag zero.
No automatic action, lifecycle closure or runbook update is performed.

The default request uses strict JSON Schema through Responses API `text.format`.
All fields are required on the wire (an unrelated next check uses `runbook_id: null`),
and current evidence, history, runbook and unknown IDs are restricted to this input.
Hypotheses require usable current evidence; empty available sets allow only empty lists.
The local semantic/relationship validators still run after generation. Refusal or
incomplete output is WITHHELD even if an included JSON fragment parses. There is no
unstructured fallback or retry. The per-call plan records the actual schema hash.
The earlier strict-output change preserved v3 records. The new investigation policy
and required check targets use v4; older artifacts remain with their archived sources.

The current record schema is `ops.grounded-assistance.v4`. Previous records must use
the same version, incident/scope/profile/provenance and an earlier observation time;
failed-model records can still provide earlier code observations. Legacy records are
not migrated implicitly. Use `--legacy-assistance` for the original model-authored
state/impact experiment, or `--investigate` for optional model-selected reads. These
comparison modes retain their earlier contracts; the new fixed-fact boundary is the
default path only. `--format text` labels code facts and unreviewed model text separately.

Per-request facts separate `append`, `persistence`, `status_read` and their observation
times. `unknowns` selects IDs from the code-generated `unknown_catalog`; it accepts
neither arbitrary prose nor invented IDs. For a known 202 receipt with a 403/503
status read, persistence/read visibility may be unknown but append acknowledgement
is retained. `selected_unknowns` provides deterministic wording scoped to each
request. Other model prose is still UNREVIEWED; the catalog is bounded, and needs
outside it can only be proposed as unreviewed next checks.

`observation_history` retains up to eight earlier code snapshots, even after a
WITHHELD/FAILED model result. Observation times, source-record/content hashes and
incident scope are preserved; `updates.history_ids` is separate from current
`evidence_ids`. Duplicate/future/foreign histories are rejected. `dropped_entries`
exposes eviction from the model input; original records remain on disk. No automatic
summary or silent trimming bypasses the 90 KB projected-input limit. v2/v3 records
are not implicitly migrated; replay original bundles in a new directory for v4.

`investigation_policy` is code-owned and saved before calling the model. For the
retrieved PG runbook, a current observed blocking edge or explicit primary reachability
failure makes CURRENT investigation applicable. With no supported positive signal it
remains UNKNOWN/NEEDS_MORE_EVIDENCE; healthy readiness/empty waits cannot establish
global DB health. This bounded rule supports the saved DB wait and application
readiness observation shapes, not arbitrary persistence logs. Causal linkage to request
IDs remains unproven; earlier DB signals belong in model updates, not a switched scope.

Required targets cover unavailable request-status reads (including each request and
HTTP result), Worker desired>0/ready=0, and current DB blocking. Worker counts require
a unique, fresh, complete typed observation; intentional desired=0 is not a zero-ready
failure target. No global severity or restart/scaling authority follows from a target.
Each model next check supplies `target_ids` and the corresponding evidence IDs, with
at most three checks; targets may share a check. Missing targets or mismatched citations
withhold the whole model response, preserving facts and the visible code policy.
Target tags do not prove useful prose: a counterexample test deliberately passes an
unrelated sentence with valid tags. Human/explicit evaluation review is still required.

```powershell
# Four synthetic development cases; freezes baseline and rubric before any calls.
# Omit --execute for zero API calls. Maximum four calls, no retries.
.venv\Scripts\python.exe scripts\evaluate_grounded_assistance.py --execute `
  --output-dir results/ops-agent/shadow/<new-grounded-evaluation>
```

This compares the same fixed observations/retrieved runbooks with optional Luna
interpretation. The evaluator reports structural results and requires a separate
semantic review; it does not score operator usefulness or held-out accuracy automatically.

For exact-input variability checks, add `--repeat 2` (allowed 1–3, maximum 16 total
calls). Repeated requests use the same frozen code-only predecessor, including the
same history and no prior model analysis. Request hashes must match within each
case. This tests repeated snapshots, not propagation of model answers through a
live sequence. Every response/failure is saved separately. Runbook decisions and
selected-gap equality are reported separately from prose, whose consistency and
usefulness require review. Repeats never stop at the first success.

Use `--fixtures <frozen-cases.json>` for a separate evaluation set (1–8 selected
snapshots). The evaluator saves source copies and hashes before calls, checks source
stability before each snapshot and at completion, validates predecessor order, and
never sends private `review` criteria as model input. Preserve first outputs and
record semantic findings separately; a zero exit status only means structural checks
and fixed-fact equality passed. The latest new-case evaluation found a prose
contradiction despite those checks passing; see [the evaluation record](../docs/OPS_AGENT_EVALUATION.md).
Its frozen synthetic input and private rubric are retained in
[grounded_novel_v1.json](fixtures/grounded_novel_v1.json). Reusing it for a fix or
rerun does not constitute another unseen-case evaluation.
Pass `--regression` when reusing such cases; it explicitly labels the plan and summary
as development regression. The v3 repair run used `--case s03 --case s04a --case s04b
--case s04c` and preserved one withheld response without retrying or repairing it.

The bounded synthetic pilot reuses the existing evaluation runner:

```powershell
.venv\Scripts\python.exe scripts\evaluate_investigation.py --mode runbook-assistance `
  --execute --output-dir results/ops-agent/shadow/<new-pilot-directory>
```

This performs up to eight API calls (three cases, four snapshots, two independent
runs). Source fixtures and acceptance criteria freeze before calls. Offline mode
omits `--execute`. Results do not measure operator time savings or search-only superiority.

### Model-selected investigation and direct explanation

```powershell
.venv\Scripts\python.exe scripts\assist_incident.py `
  --bundle <selected-observation-bundle.json> --provenance recorded `
  --question "현재 상태와 원인 후보, 런북 적용 여부를 설명해줘" `
  --investigate --live --format text `
  --output-dir results/ops-agent/shadow/<new-investigation-directory>
```

`--investigate` gives the model five fixed observation readers and a runbook search
tool. Initially it sees the question, scope, timestamp and optional earlier model
answer; observation values and runbook excerpts arrive only through its selected
tools. The adapter supports the standard application/Worker/lag metric groups and
the request-status/DB-activity observations produced by `run_local_lock_fault.py`.
It reads **saved evidence only**, not a live cluster. Missing groups return
UNAVAILABLE. No SQL, shell, arbitrary URL/file reads or writes are exposed.

There are at most five model calls, six unique tool calls and 6,000 output tokens
per model call, with no retry/repair. Final output requires observation reads and
runbook search, and may cite only returned evidence. A failed explanation remains
a failure; no Codex-authored replacement is produced. The function-call exchange
uses the [official OpenAI protocol](https://developers.openai.com/api/docs/guides/function-calling)
with the existing client and configured model.

`record.json` and `calls/` retain the model output and exact requested tools.
`explanation.txt` (also `--format text`) displays model wording unchanged, with
deterministic labels, citation IDs and source metadata. Runbook application
rationales are labeled as **model judgments**, not quoted runbook instructions.
The original excerpts remain in the recorded input. `--previous` uses the same
incident/scope/time gates as the existing assistance path. Model authorship and
valid citations do not prove semantic correctness.

## Collect and evaluate

Use the repository virtual environment and a new output directory for each run.

```powershell
.venv\Scripts\python.exe scripts\observe_worker_backlog.py `
  --context kind-messaging-ha --interval-seconds 15 `
  --output-dir results/ops-agent/shadow/<new-observation-directory>
```

This captures three local-ha bundles and replays the existing activation policy.
It does not invoke a model. `ABSENT` and `UNKNOWN` are not positive incidents.

The lower-level CLI exposes collection and deterministic evaluation separately:

```powershell
.venv\Scripts\python.exe -m ops_agent collect `
  --profile local-ha --context kind-messaging-ha `
  --output results/ops-agent/evidence.json
.venv\Scripts\python.exe -m ops_agent evaluate `
  --input results/ops-agent/evidence.json --output results/ops-agent/conditions.json
$inputs = Get-ChildItem <run-directory>/bundles/sample-*.json |
  Sort-Object Name | ForEach-Object FullName
.venv\Scripts\python.exe -m ops_agent evaluate-sequence `
  --input $inputs --output <conditions.v2.json>
```

The single-bundle evaluator emits `ops.conditions.v1`; the sequence evaluator
emits `ops.conditions.v2`. Both use frozen evidence freshness, without runtime I/O.
Sequence activation requires three adjacent valid captures with lag ≥ 7,000,
60-second slope ≥ 100 records/s and increasing total lag, under the same
eight-partition/source/configuration identity. Missing or inconsistent required
evidence remains UNKNOWN.

## Diagnose a verified activation

```powershell
.venv\Scripts\python.exe scripts\run_runtime_diagnosis_v3.py `
  --conditions <conditions.v2.json> --input $inputs `
  --output-dir results/ops-agent/shadow/<new-diagnosis-directory>
```

By default this prepares frozen projection and a fixed Runbook decision.
Add `--live` for the actual model comparison. A verified PRESENT gate runs before
key loading; normal evidence skips the model. Runtime input limits are 256 files,
16 MiB per file and 64 MiB total. Existing output directories are rejected.

The v3.5 adapter projects the final capture of the first activation window.
Its four zero-argument tools expose stage, PG, replica and KEDA evidence.
Required investigation, evidence-ID selection and validation are enforced by this
path; runtime stage elevation remains unavailable without an operational baseline.
Replay describes the saved observation time, not current service health.

The original diagnosis entry point remains available:

```powershell
.venv\Scripts\python.exe -m ops_agent diagnose `
  --conditions <conditions.v2.json> --input $inputs `
  --output <diagnosis.json> --live
```

This emits the older `ops.diagnosis.v1` contract. Its allowlist covers partition
lag, Worker stage/replicas, KEDA, PG/application readiness, runtime image, Pod
restart and Argo CD. It allows at most four tool calls, 1,600 output tokens and
one tool-free output repair. Do not confuse this contract with v3.5 or the newer
experimental log-diagnosis runner.

## Recovery and follow-up replay

```powershell
.venv\Scripts\python.exe -m ops_agent evaluate-recovery `
  --activation <conditions.v2.activation.json> `
  --input <ordered-post-activation-bundles> `
  --source-digest <one-canonical-digest-per-input> `
  --incident-id <incident> --profile local-ha --policy-version v2 `
  --output <recovery.json>
```

Recovery v1 is the compatibility default and emits ACTIVE/RECOVERING/UNKNOWN.
Explicit v2 adds RECOVERED after RECOVERING and three fresh, usable observations
inside the calibrated MEDIUM envelope: produce rate
74.98333333333333–77.08333333333333 records/s, lag ≤ 22, slope ≤ 0,
acceptable PG readiness and the 9–21 second cadence gate. It does not require lag
zero or two Worker replicas. This is backlog-incident completion, not global health.
Negative exporter lag remains invalid; no clamping or derived replacement.

```powershell
.venv\Scripts\python.exe scripts\replay_followup_diagnosis.py `
  --run-dir <archived-run-directory> --activation-end <sample-index> `
  --output-dir results/ops-agent/shadow/<new-followup-directory>
```

The follow-up candidate consumes one bundle at a time after verified activation,
without future observations. UNKNOWN blocks further projection; closure cannot
reopen. This runner uses the fixed Runbook only and has no model API option.

## Experimental tools and audit

Read [known gaps](../docs/OPS_AGENT_EVALUATION.md#known-gaps) before reusing these
runners. Successful exit or the existence of a result file is not sufficient
acceptance evidence.

| Script | Side effects and scope |
| --- | --- |
| `run_local_event_probe.py` | Creates a user/stream and 1–60 test events, ≤1 event/s; requires unique request/event IDs and matching counts; retains sanitized status polls. No reset |
| `run_local_burst.py` | Localhost-only traffic: ≤20 seconds, ≤300 events/s, ≤6,000 events, ≤32 in flight; creates one test user and 16 streams, retains receipts, observes up to 120 seconds of recovery and checks ≤60 persistence samples. No model calls or configuration changes |
| `run_local_lock_fault.py` | Default is read-only preflight. `--execute` locks one newly created stream sequence row for 50 seconds with server timeouts and rollback, creates 21 events across 9 streams, verifies every accepted ID. `--live` adds up to 3 sequential model calls after release |
| `watch_local_until_error.py` | Read-only observations plus repeated event probes, ≤30 minutes. Unreachable readiness is an evidence warning; probe failure is separately retained |
| `run_worker_fault_diagnosis.py` | `--execute` actually changes Worker command/strategy and temporarily disables Argo automatic sync; model calls and rollback follow. Explicit local fault experiment |
| `diagnose_postgres_restarts.py` | Collects PG status/logs/probes/config history and immediately calls the configured model; no cluster changes. No separate `--live` flag |
| `audit_live_experiments.py` | Offline artifact comparison and mocked counterexamples; no model or cluster calls |
| `verify_postgres_recovery.py` | Read-only exact 3-Pod/UID/restart/readiness sampled verification; requires pre-intervention primary identity. No traffic, patches or failover |
| `evaluate_investigation.py` | Three synthetic cases vs a frozen adaptive Runbook. Offline by default; `--execute` makes up to 15 model calls, no cluster access |

The two experimental diagnosis runners return collected snapshots through
allowlisted tools. They require investigation, JSON fields and citations to acquired
evidence; semantic correctness remains separate from structural validation. They must
not be used to report general accuracy or autonomous remediation success.

```powershell
.venv\Scripts\python.exe scripts\run_local_burst.py `
  --output-dir results/ops-agent/shadow/<new-burst-directory> `
  --rate 300 --seconds 20 --workers 32
```

The rate is an upper bound, not achieved throughput. New event requests stop on
5 consecutive failures, 10 failures in the last 50 completions, 2 consecutive
readiness/collector failures, or a `STOP` file in the run directory. In-flight
requests have a 3-second timeout and are not retried. Polling and collector time
affect stop latency. Recovery means three consecutive sampled healthy/zero-lag
observations plus sampled persistence; it does not prove every accepted event
persisted. Test data remains in the service. This is a local load observation,
not a DDoS resilience or service capacity benchmark.

```powershell
# Fixed kind-messaging-ha / messaging-app; no fault without --execute.
.venv\Scripts\python.exe scripts\run_local_lock_fault.py
.venv\Scripts\python.exe scripts\run_local_lock_fault.py --execute --live `
  --output-dir results/ops-agent/shadow/<new-row-lock-directory>
```

The fault holds `SELECT ... FOR UPDATE` on one owned `room_sequences` row, with
50-second server sleep, 55-second statement timeout, 2-second lock acquisition
timeout, and 60-second idle transaction timeout. It changes no stored row values,
deployments or HA settings. Worker serialization can delay other streams sharing
the consuming Worker/partition; the affected scope is measured, not assumed.
Recovery requires no remaining owned session, every request persisted with unique
event IDs, three zero-lag/ready observations, no DB waiters and unchanged PG Pod
UIDs/restart counts. User/stream/event test data remains.

Model input progresses from symptoms and actual request receipts to a later DB
wait graph, then recovery evidence. The fixed SQL reader supplies observed waiter
and blocker PIDs, wait type, transaction age and relations; sleeping SQL, session
names and the injection plan are withheld. Calls occur after capture/release and
are recorded replay, not automatic live investigation. Existing runbook sections
are unchanged before the experiment; no new lock-resolution procedure is supplied
as an answer key. Failed runs and raw artifacts remain alongside later attempts.

```powershell
.venv\Scripts\python.exe scripts\audit_live_experiments.py `
  --output-dir results/ops-agent/shadow/<new-audit-directory>
```

This audit needs the existing local raw archives. It checks current counterexample
handling and separately reports the frozen old PG predicate. Preserve failed and
interrupted runs, including accepted IDs whose persistence was not confirmed.

```powershell
# Add --execute for bounded API evaluation; otherwise freezes fixtures/baseline only.
.venv\Scripts\python.exe scripts\evaluate_investigation.py `
  --output-dir results/ops-agent/shadow/<new-pilot-directory>

# Supply the primary identity captured BEFORE an intervention, not after it.
.venv\Scripts\python.exe scripts\verify_postgres_recovery.py `
  --output-dir results/ops-agent/shadow/<new-verification-directory> `
  --primary-uid <captured-primary-uid> --primary-restarts <captured-count>
```

The new PG verifier requires a 180-second sampled span with gaps no greater than
21 seconds, and saves UID and HTTP status on every sample. Its tests use mocked
collectors; it has not yet been rerun against the cluster. Synthetic pilot scores
compare cause labels/citations; prose quality and operator usefulness need review.

## Credentials, provenance and safety

- Existing model configuration: process environment or ignored `.env.local`,
  `OPENAI_MODEL` and `OPENAI_API_KEY`; recorded recent runs used `gpt-5.6-luna`
- No keys in prompts, logs, fixtures or tracked files; normal pytest/CI makes no API calls
- Core collector: fixed HTTP GET and kubectl get/config operations, safe projection
  and credential redaction; no arbitrary PromQL, shell or write tool for the model
- Windows local routing: `127.0.0.1` with `Host: localhost`; redirects and ambient
  proxies disabled; kubectl override restricted to an executable
- Existing kubeconfig is not claimed to be least privilege. Operator endpoint,
  context and executable overrides are trusted local inputs
- Preserve source HEAD/dirty state, collector identity, source timestamps,
  canonical bundle digests and artifact hashes. Hashes do not attest a remote server
- Worker processed count, stage latency and end-to-end persistence lag are distinct;
  stage timing excludes the later request-status update and transaction commit
- Closed incidents remain immutable history; later observations do not imply
  automatic reopen. [Public replay](../docs/OPS_AGENT.md#public-replay-boundary)
  reads sanitized recorded data without model/runtime calls

The current local PG correction is a **standby-only runtime patch with partition 1**.
Primary rollout and Helm release reconciliation remain separate work. See
[the runbook](../docs/RUNBOOK.md#ops-agent-local-pg-correction) before changing that partition.

## Local validation

```powershell
.venv\Scripts\python.exe -m pytest -q tests/ops_agent
.venv\Scripts\python.exe scripts\check_docs.py docs/OPS_AGENT.md docs/OPS_AGENT_EVALUATION.md ops_agent/README.md
```

Unit-test totals are implementation checks. Use the evaluation document's
case-level evidence and limitations when discussing model capabilities.
