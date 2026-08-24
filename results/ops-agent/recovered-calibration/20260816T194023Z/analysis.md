# Continuous-ingress RECOVERED Calibration

- Experiment: `20260816T194023Z`
- Scope: E-04 through E-06 supplemental continuous-ingress calibration
- Decision: `RECOVERED_POLICY_PROMOTED`

## MEDIUM Re-entry Contract

- Contract: `local-ha.medium-reentry-candidate.v1`
- Produce: `{'minimum': 74.98333333333333, 'maximum': 77.08333333333333, 'basis': 'observed Phase 4 MEDIUM minimum and maximum'}`
- Lag: `{'maximum': 22.0, 'basis': 'observed Phase 4 MEDIUM maximum; local-ha candidate only'}`
- Slope: `{'maximum': 0.0, 'basis': 'observed Phase 4 MEDIUM maximum'}`

## Combined E Runs

| Run | Peak lag | First recovering | First re-entry | Max stable | UNKNOWN | Negative invalid |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| E-run-01 | 20806 | 18 | 61 | 3 | 12 | 10 |
| E-run-02 | 21834 | 18 | 52 | 1 | 17 | 15 |
| E-run-03 | 21151 | 18 | 55 | 3 | 12 | 8 |
| E-run-04 | 20261 | 18 | 51 | 4 | 15 | 11 |
| E-run-05 | 22632 | 18 | 43 | 5 | 10 | 8 |
| E-run-06 | 18948 | 18 | 43 | 14 | 18 | 14 |

## Stable Count Distribution

```json
{
  "per_run_maximum_consecutive_usable_reentry_count": {
    "E-run-01": 3,
    "E-run-02": 1,
    "E-run-03": 3,
    "E-run-04": 4,
    "E-run-05": 5,
    "E-run-06": 14
  },
  "candidate_supporting_runs": {
    "1": [
      "E-run-01",
      "E-run-02",
      "E-run-03",
      "E-run-04",
      "E-run-05",
      "E-run-06"
    ],
    "2": [
      "E-run-01",
      "E-run-03",
      "E-run-04",
      "E-run-05",
      "E-run-06"
    ],
    "3": [
      "E-run-01",
      "E-run-03",
      "E-run-04",
      "E-run-05",
      "E-run-06"
    ],
    "4": [
      "E-run-04",
      "E-run-05",
      "E-run-06"
    ],
    "5": [
      "E-run-05",
      "E-run-06"
    ],
    "6": [
      "E-run-06"
    ],
    "7": [
      "E-run-06"
    ],
    "8": [
      "E-run-06"
    ],
    "9": [
      "E-run-06"
    ],
    "10": [
      "E-run-06"
    ],
    "11": [
      "E-run-06"
    ],
    "12": [
      "E-run-06"
    ],
    "13": [
      "E-run-06"
    ],
    "14": [
      "E-run-06"
    ]
  },
  "candidate_selected": 3,
  "selection_status": "PROMOTED",
  "supporting_runs": [
    "E-run-01",
    "E-run-03",
    "E-run-04",
    "E-run-05",
    "E-run-06"
  ],
  "non_supporting_runs": [
    "E-run-02"
  ],
  "rejected_alternatives": {
    "1": "brief envelope entry is insufficient and fails false-recovery control",
    "2": "two-entry then regrowth control would be a false recovery",
    "4": "supported by only three of six continuous-ingress runs"
  }
}
```

## Decision

- Status: `RECOVERED_POLICY_PROMOTED`
- Reason: N=3 is supported by five of six continuous-ingress runs and all three supplemental runs; N=1/2 fail false-recovery controls and N=4 lacks majority support
- RECOVERED is incident-scope Worker backlog completion, not global health.
- Post-recovery backlog regression handling remains future work.
