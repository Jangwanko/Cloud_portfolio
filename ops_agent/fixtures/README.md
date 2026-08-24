# Synthetic Ops Agent Fixtures

이 디렉터리의 JSON은 collector/controller unit test용 synthetic input입니다.

- runtime capture 아님
- 정상 운영 baseline 아님
- missing, error, stale, offset `-1`, partial coverage, decrease, redaction 분기 검증용
- `sequences/`: decreasing lag, capture 수 부족, stale middle, partial partition, source scope 변경, timestamp reorder 등 v2 adversarial mutation specification

실제 `local-ha` capture는 `results/ops-agent/live-baseline/`에 분리합니다. Phase 2 evaluator test는 synthetic fixture와 captured live evidence의 역할을 구분해야 합니다.
