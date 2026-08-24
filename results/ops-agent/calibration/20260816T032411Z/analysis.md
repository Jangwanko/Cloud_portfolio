# Worker Backlog Calibration - 2026-08-16

## Classification

- Schema: `ops.backlog-calibration.v1`
- Experiment: `20260816T032411Z`
- Runtime: GitOps `local-ha`, context `kind-messaging-ha`
- Workload: 3 runs, 64 streams, 100 VU, 30 seconds, 0.05 second think time
- Sampling: 71 `ops.evidence.v1` bundles, nominal 15 second interval
- Bundle collection status: 63 `COMPLETE`, 8 `PARTIAL`
- Scaling: existing KEDA policy only; no KEDA or Worker replica mutation
- Result: all three runs `COMPLETE`

The experiment started each run at lag `0`, Worker desired/available `2/2`, and
KEDA `Active=False`. It finished only after positive lag, KEDA scale-out, lag
drain to `0`, and Worker return to `2/2` had all been observed. The full bundle
and raw projection set remains local because it is about 88 MB and contains
runtime topology. The sanitized manifest and run summaries are the tracked
reference.

The first baseline bundle retained one label-on-use Worker stage series as
`MISSING`. Seven scale-out captures retained Worker metric freshness as
`UNKNOWN` because the 60-second range still contained terminated/previous Pod
label sets while the instant timestamp query contained the current Pod set.
Those captures are `PARTIAL`; they are not collector failures and their stage
latency is not treated as zero. All required Kafka end/committed/lag evidence
remained `OK/FRESH` in all 71 bundles. The stage summary distribution is 63
`OK`, 1 `MISSING`, and 7 `UNKNOWN`.

## Run Comparison

| Signal | Run 1 | Run 2 | Run 3 |
| --- | ---: | ---: | ---: |
| Event `202` | 20,047 | 28,862 | 28,600 |
| k6 error rate | 0.00% | 0.00% | 0.00% |
| Evidence samples | 22 | 24 | 25 |
| Sample interval avg (min-max) | 15.067s (14.711-16.892) | 15.057s (14.882-16.566) | 15.053s (14.699-16.522) |
| First positive lag | 7,490 at 17.257s | 8,599 at 16.867s | 10,578 at 16.827s |
| First Worker desired `4` | 32.149s | 16.867s | 16.827s |
| First Worker available `4` | 32.149s | 31.785s | 31.835s |
| Peak lag | 17,537 at 46.860s | 25,256 at 46.667s | 24,096 at 46.616s |
| Lag first returned to `0` | 196.781s | 256.575s | 256.543s |
| Worker returned to `2/2` | 316.779s | 346.609s | 361.580s |
| Max 60s produce rate | 334.117/s | 481.033/s | 476.667/s |
| Max 60s committed-offset rate | 127.017/s | 139.467/s | 137.500/s |
| Positive 60s lag slope | 104.383-292.283/s | 143.317-420.933/s | 156.517-401.600/s |
| Negative 60s lag slope | -127.017 to -19.200/s | -139.467 to -3.083/s | -137.500 to -3.367/s |
| Worker stage mean, observed windows | 2.565-15.917ms | 2.581-13.930ms | 3.431-14.179ms |
| Worker stage finite p95 bucket upper bound | <=100ms | <=100ms | <=100ms |
| Max PostgreSQL replication delay | 9,728 bytes | 10,696 bytes | 8,176 bytes |

The first three positive-lag captures in every run formed the repeatable rising
sequence. Run 1 observed `7,490 -> 16,704 -> 17,537`; Run 2 observed `8,599 ->
21,914 -> 25,256`; Run 3 observed `10,578 -> 23,241 -> 24,096`. Every capture
in that sequence had a positive 60-second lag slope and produce rate greater
than committed-offset rate. Run 1 also proves that pressure can be present
before the HPA desired replica count reaches `4`; replica state is context, not
a required pressure predicate.

Worker desired replicas reached `4` in all runs. Runs 2 and 3 first observed
desired/available `4/2`, then `4/4` on the next capture. KEDA remained
`Ready=True`, changed to `Active=True` under lag, and returned to
`Active=False` during recovery. The configured min/max `2/4`, polling interval
`5s`, and cooldown `120s` were unchanged. PostgreSQL remained `ready` with HA
mode enabled, primary reachable, and standby/synchronous standby counts `2/2`.

## Partition Distribution

At the first positive capture, the largest partition held `17.53%`, `15.77%`,
and `20.56%` of total lag for runs 1-3. At peak lag the corresponding shares
were `18.90%`, `16.46%`, and `20.90%`. This workload therefore exercised all
eight partitions rather than a hot single stream. Shares close to `1.0` only
appeared near the recovery tail when total lag was very small. A future
concentration rule must apply an absolute total-lag floor before a share test.

## Candidate Activation Policy

No threshold or consecutive-window count was selected before the experiment.
The three-run common envelope supports this provisional activation candidate:

1. Keep the current Phase 2 required-evidence gates: complete/fresh 8/8 end,
   committed-offset, and lag series; aligned 60-second grids; no `-1`, missing
   partition, decrease, or arithmetic mismatch.
2. Require `total_lag >= 7,000`, 60-second `lag_slope >= 100 records/s`, and
   `produce_rate > committed_offset_rate`.
3. Require the predicate for three consecutive captures at about 15-second
   intervals, with total lag increasing across both forward intervals. This is
   about 30 seconds of observed persistence and triggered by the common rising
   sequence before or at peak lag in all three runs.
4. Do not require KEDA `Active=True` or Worker desired `4`; they remain response
   evidence and are not proof of pressure.

`7,000`, `100 records/s`, and three captures are calibration candidates, not an
active evaluator rule. They are deliberately below the common first-observation
minima (`7,490` lag and `124.833 records/s` slope) instead of copying one scrape
timestamp exactly. Promotion to a new immutable ruleset requires a short-burst
negative control and a lower-rate positive control to measure false positives
and missed pressure. Until that validation, positive lag remains `UNKNOWN` in
Phase 2. Full-window lag `0` remains the only implemented `ABSENT` rule.

The 60-second produce rate, committed-offset rate, and lag slope are overlapping
range-window values. Since `lag = end - committed`, the rate gap and lag slope
are an arithmetic cross-check rather than independent causal signals. Negative
slope with lag above zero is drain evidence; this experiment does not add a
recovery condition or clearing rule.

## Latency Semantics

`worker_db_persist_stage_latency` measures `_persist_message_with_cursor` only.
It excludes the enclosing transaction commit and must not be reported as
PostgreSQL commit latency. The finite p95 value is a histogram bucket upper
bound, not an exact percentile. Missing label-on-use series remain `MISSING` and
are never converted to zero.

## Provenance

| Artifact | SHA-256 |
| --- | --- |
| `manifest.json` | `70f0b413f667391482f9415a08127351a5c732c7ab7ceb2fbf933ee1f761b4ce` |
| `run-01/summary.json` | `9c11fcfa65dd33138a06e962ad8c1a03a790654d773edc73d4dd97333fbff639` |
| `run-02/summary.json` | `43dc2fae550dc35334b590d80bc4f9a926fe92d4e4bd189c3b645ef3894cb746` |
| `run-03/summary.json` | `effe975d6091fe7eeeb331f872965037eb87f26d47001035332f87cb7853222c` |

The run summaries retain every sampled per-partition end offset, committed
offset, lag, rate, slope, Worker/KEDA state, PostgreSQL readiness field, and
Worker-stage observation. The local bundle set contains 71 normalized bundles
and 284 raw source projections.

## Post-calibration v2 Replay

After the negative controls completed, the calibrated contract was implemented
as `local-ha.conditions.v2` without changing the v1 single-bundle rule. Replaying
the complete ordered captures returned `CORE_BACKLOG_PRESSURE=PRESENT` for all
three runs. Each matched capture indexes `[1,2,3]`; the lag sequences were
`7,490 -> 16,704 -> 17,537`, `8,599 -> 21,914 -> 25,256`, and `10,578 ->
23,241 -> 24,096`. The tracked [sequence replay summary](../../sequence-validation/20260816T044352Z/summary.json)
retains the evaluation IDs and local output hashes.
