# CORE_BACKLOG_PRESSURE Negative Controls - 2026-08-16

## Classification

- Schema: `ops.backlog-negative-control.v1`
- Experiment: `20260816T040746Z`
- Runtime: GitOps `local-ha`, context `kind-messaging-ha`
- Candidate: `phase2.5.pressure-candidate.v1`
- Scaling: existing KEDA policy only; no KEDA or Worker replica mutation
- Result: three valid negative controls `COMPLETE`, candidate `NOT_PRESENT`
- Phase 3: not started

The candidate was frozen before these runs:

```text
total_lag >= 7,000
60-second lag_slope >= 100 records/s
three consecutive captures at about 15-second intervals
total lag increases across both capture transitions
produce_rate - committed_offset_rate matches lag_slope arithmetically
```

`produce_rate - committed_offset_rate` and `lag_slope` are the same offset
change. The candidate has one growth signal. The rate gap is only an arithmetic
consistency check and is never counted as a second vote.

## Result

| Signal | Short burst | Sustainable high | Single transient spike |
| --- | ---: | ---: | ---: |
| Workload | 100 VU, 5s, 64 streams | 8 VU, 180s, 64 streams | 100 VU, 10s, 64 streams |
| Event `202` / error | 5,160 / 0.00% | 22,256 / 0.00% | 9,790 / 0.00% |
| Evidence samples | 12 | 24 | 15 |
| Sample interval avg (min-max) | 15.113s (14.958-16.287) | 15.053s (14.905-16.250) | 15.086s (14.960-16.253) |
| Peak total lag | 3,997 | 3,111 | 8,854 |
| Peak partition share | 19.89% | 28.22% | 17.53% |
| Max 60s produce rate | 86.000/s | 123.733/s | 163.167/s |
| Max 60s committed rate | 86.000/s | 119.667/s | 125.167/s |
| 60s lag slope min-max | -66.617 to 66.617/s | -51.850 to 32.050/s | -125.167 to 147.567/s |
| Candidate samples | 0 | 0 | 2 |
| Matched three-capture window | 0 | 0 | 0 |
| Candidate result | `NOT_PRESENT` | `NOT_PRESENT` | `NOT_PRESENT` |
| Worker max desired/available | 4/4 | 4/4 | 4/4 |
| Final lag / Worker / KEDA | 0 / 2/2 / inactive | 0 / 2/2 / inactive | 0 / 2/2 / inactive |

All runs retained the original KEDA target, min/max `2/4`, polling `5s`, and
cooldown `120s`. KEDA scale-out occurred even in the short burst. Replica
scale-out is therefore response context, not a pressure predicate.

## Control Interpretation

### Short burst

The five-second burst produced 5,160 accepted events. The first and peak lag was
3,997, below the candidate floor, and it drained to zero by sample 3. The
candidate did not activate even though KEDA scaled the Worker to 4/4.

### Sustainable high load

The 180-second load produced 22,256 accepted events, or about 123.6 events/s.
Lag rose gradually from 396 to a bounded peak of 3,111 while KEDA held 4/4.
Across the late load samples, the 60-second slope converged from 21.6 to 11.1,
7.8, 5.1, and 4.1 records/s; the last load capture decreased from 3,111 to
3,106. The runtime therefore reached a local bounded operating region below the
candidate floor instead of an accelerating backlog. It drained to zero after
input stopped.

This is a 180-second local calibration result, not a production sustainable
throughput or SLA claim.

### Single transient lag spike

The ten-second spike crossed the absolute floor. Its lag sequence was `8,854 ->
7,510 -> 5,514 -> 3,338 -> 0`. The first two captures still had positive
60-second slopes because the overlapping range included the burst, so two
individual candidate samples matched. Total lag decreased across captures and
no three-capture rising window existed. The persistence clause prevented a
false `PRESENT` result.

## Evidence Quality

- 51 `ops.evidence.v1` bundles: 40 `COMPLETE`, 11 `PARTIAL`
- 204 raw source projections; every `raw_ref` exists and matches SHA-256
- Kafka end/committed/lag: `OK/FRESH` in all 51 bundles, 8/8 partitions
- Maximum `abs((produce-committed)-lag_slope)`: `1.42e-14 records/s`
- Worker stage summaries: 40 `OK`, 11 `UNKNOWN`, 0 coerced to zero
- PostgreSQL: ready/HA/primary reachable and standby/sync standby `2/2` in all samples
- Maximum PostgreSQL replication delay: 13,496 bytes
- Worker stage observed mean range: 2.150-16.484ms; transaction commit excluded

The 11 `PARTIAL` captures came from Worker metric range/instant label coverage
during scale transitions. They did not affect required Kafka evidence or the
candidate result.

## PRESENT Rule Promotion Proposal

The three negative controls passed, so the candidate is eligible for a
versioned promotion proposal. No rule was changed in this experiment.

1. Add a future `local-ha.conditions.v2` activation policy while retaining v1
   behavior for a single bundle.
2. Evaluate an ordered sequence of immutable `ops.evidence.v1` bundle hashes.
   The current single-bundle evaluator cannot prove three external captures.
3. Require identical profile, context, namespace, topic, group, partition set,
   endpoint identity, and Kafka raw/source identity across the sequence.
4. Require the existing Kafka selector, freshness, 8/8 coverage, `-1`, decrease,
   time-grid, and `lag=end-committed` gates in every bundle.
5. Mark `CORE_BACKLOG_PRESSURE=PRESENT` only when three consecutive captures
   satisfy lag `>=7,000` and 60-second slope `>=100/s`, and total lag strictly
   increases across both capture transitions.
6. Keep `produce-committed` as a consistency assertion for slope. It contributes
   no additional vote.
7. Keep KEDA state, Worker replicas, stage latency, and PostgreSQL readiness as
   context or optional evidence, not activation predicates.
8. Bind the ordered source-bundle digests, sequence timing, policy version, and
   rule implementation version into the evaluation ID.

This proposal covers activation only. Negative/draining lag remains a recovery
observation, and the existing full-window zero-lag rule remains the v1
`ABSENT` proof. Hysteresis, recovery, and clearing rules require a separate
calibration before implementation.

## Provenance

| Artifact | SHA-256 |
| --- | --- |
| `manifest.json` | `b581afcc42d6aed5a635a7091d4d55a4db2688169ef5741d0cfb09ffbd354df4` |
| `short-burst/summary.json` | `f45649450b10ba8dc154278b84e3cd0522a6d8f5ca69ea9f32e7d8bd60c0e59c` |
| `sustainable-high/summary.json` | `b7a6b53e923d4fca976fb7a3a0b172a50c62c9c80851b92ec8670f6ad51874dc` |
| `single-transient-spike/summary.json` | `6282b66758e3b935b8858bf761ff88c92d4ec32591531c5a59e66b5f8aa24284` |

The normalized bundles and raw projections remain local-only because they are
about 62 MB and include runtime topology. The sanitized manifest, analysis,
and three summaries are the tracked reference.

## Post-calibration v2 Replay

The proposal above was implemented as the separate `local-ha.conditions.v2`
ordered-sequence policy. Actual replay produced no `PRESENT` result for short
burst, sustainable high, or single transient spike. All three finished with an
empty activation-window list. The v1 policy remains unchanged, and recovery or
clearing hysteresis remains unimplemented. Evaluation IDs and local output
hashes are in the tracked [sequence replay summary](../../sequence-validation/20260816T044352Z/summary.json).
