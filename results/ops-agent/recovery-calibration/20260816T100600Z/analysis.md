# Phase 4 Recovery Calibration Analysis

- Experiment: `20260816T100600Z`
- Scope: Worker backlog recovery calibration only; no recovery state emitted
- Workload: host-local k6 constant-arrival-rate, 64 streams

## Baseline Operating Envelope

| Profile | Samples | Produce median | Lag median / p95 / max | Slope median |
| --- | ---: | ---: | ---: | ---: |
| IDLE | 5 | 0.0 | 0.0 / 0.0 / 0.0 | 0.0 |
| LOW | 4 | 29.691666666666666 | 1.0 / 3.0 / 3.0 | 0.016666666666666666 |
| MEDIUM | 3 | 75.0 | 0.0 / 22.0 / 22.0 | 0.0 |
| HIGH_SUSTAINABLE | 2 | 108.92500000000001 | 231.0 / 246.0 / 246.0 | 2.216666666666667 |

## Recovery Candidates

- `E-run-01`: peak lag `20806.0`, first negative slope index `16`, stable re-entry candidates `[{'start_index': 61, 'end_index': 63, 'capture_count': 3}]`
- `F-run-01`: peak lag `20998.0`, first negative slope index `15`, stable re-entry candidates `[{'start_index': 26, 'end_index': 72, 'capture_count': 47}]`
- `E-run-02`: peak lag `21834.0`, first negative slope index `16`, stable re-entry candidates `[{'start_index': 52, 'end_index': 52, 'capture_count': 1}]`
- `E-run-03`: peak lag `21151.0`, first negative slope index `16`, stable re-entry candidates `[{'start_index': 55, 'end_index': 62, 'capture_count': 4}, {'start_index': 70, 'end_index': 72, 'capture_count': 3}]`

## Policy Candidates

- RECOVERING three-capture candidate: `SUPPORTED_BY_ALL_LIVE_RECOVERY_RUNS_NOT_PROMOTED`; not promoted
- RECOVERED three-capture candidate: `NOT_UNIVERSALLY_VALIDATED`; E-run-02 retained only one usable re-entry candidate because later exporter-negative samples were excluded
- Capture cadence: configured `15s`, observed `9.985~19.977s`; tolerance not promoted
- Fixed lag recovery floor: not selected; match the current ingress profile's observed envelope

## Artifact Validation

- Status: `PASS`
- Bundles: `349/349`
- Raw projections: `1396`

## Boundary

These values are calibration candidates. No `ops.recovery.v1` evaluator,
RECOVERING/RECOVERED production threshold, LLM recovery decision, or remediation
was implemented.
