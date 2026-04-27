# Test Results

Kafka Event Stream Systems 포트폴리오의 로컬 Kubernetes/kind 검증 결과입니다.

## Kafka HA Current Result

| Scenario | Result | Notes |
| --- | --- | --- |
| Full quick start | Pass | `scripts/quick_start_all.ps1` completed successfully |
| Kafka broker rollout | Pass | `apache/kafka:3.7.0` 3-broker KRaft StatefulSet, `3/3` ready |
| Kafka topic bootstrap | Pass | `message-ingress`, `message-ingress-dlq`, partitions `8`, replication factor `3`, `min.insync.replicas=2` |
| API Kafka intake | Pass | API accepts events through Kafka-only ingress path |
| Worker consumer group | Pass | `message-worker` consumes `message-ingress` partitions and persists to PostgreSQL |
| Smoke test | Pass | event accepted -> Kafka ingress topic -> Worker -> PostgreSQL persisted |
| PostgreSQL outage test | Pass | API remains ready in degraded mode and accepts cached-stream events while DB primary is down |
| Kafka DLQ flow | Pass | poison event reaches `message-ingress-dlq` and is visible through DLQ API |
| HPA metrics test | Pass | API HPA metric is valid; replica count stayed stable because CPU remained below target |
| Unit tests | Pass | `.venv\Scripts\python.exe -m pytest -q`, `12 passed` |
| Kafka performance suite | Pass | 100 VU / 30s Kafka intake baseline, `0.01%` error, p95 `92.25ms` |

## Verified Commands

- `.venv\Scripts\python.exe -m pytest -q`
- `.venv\Scripts\python.exe -m compileall portfolio worker observer alembic`
- `kubectl apply --dry-run=client -f k8s\gitops\base\kafka-ha.yaml -o yaml`
- `kubectl kustomize k8s\gitops\overlays\local-ha`
- `powershell -ExecutionPolicy Bypass -File scripts\quick_start_all.ps1`
- `powershell -ExecutionPolicy Bypass -File scripts\test_db_down.ps1 -SkipReset`
- `powershell -ExecutionPolicy Bypass -File scripts\test_dlq_flow.ps1 -SkipReset`
- `powershell -ExecutionPolicy Bypass -File scripts\test_hpa_scaling.ps1 -TimeoutSec 60`
- `git diff --check`

## Performance Baseline

The Kafka-native performance baseline is measured separately from correctness tests.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_kafka_performance_suite.ps1
```

Latest run:

| Metric | Result |
| --- | ---: |
| Timestamp | `2026-04-28T02:07:25+09:00` |
| k6 profile | `single500` |
| Concurrent users | `100` |
| Duration | `30s` |
| Idempotency header | disabled |
| Total HTTP requests | `30922` |
| Event status 200 | `30916` |
| Event status 503 | `2` |
| Error rate | `0.01%` |
| API accept latency avg | `46.50ms` |
| API accept latency p95 | `92.25ms` |
| Async latency sample size | `50 events` |
| Async accept avg / p95 | `56.28ms` / `68.19ms` |
| Accepted-to-persisted avg / p95 / max | `49.02ms` / `9.14ms` / `2044.09ms` |
| API HPA final replicas | `8` |
| Worker KEDA final replicas | `8` |

Latest output is written to `results/kafka-performance/latest.txt`.

Important interpretation:

- This baseline measures the Kafka append-centered intake path.
- `X-Idempotency-Key` is disabled for this baseline.
- With `X-Idempotency-Key` enabled, the API performs PostgreSQL state-store work before Kafka append. A diagnostic run showed the DB state path becoming the bottleneck, including `503` responses and Pgpool restart pressure.
- Therefore idempotency-state design remains a separate Kafka-native hardening task.

## Latest Cluster Snapshot

- `statefulset/kafka`: `3/3` ready
- `job/kafka-topic-bootstrap`: complete
- API: 3 pods running
- Worker: 2 pods running
- DLQ replayer: running
- PostgreSQL HA and Pgpool: running
- Prometheus and Grafana: running
- During the latest performance run, API HPA and Worker KEDA both reached 8 replicas.

## Reliability Interpretation

- Kafka now carries the ingress and DLQ transport roles that were previously tied to the queue layer.
- Same-room ordering is preserved by using the room key as the Kafka partitioning boundary.
- Kafka broker availability is protected by 3 brokers, replication factor `3`, and `min.insync.replicas=2`.
- PostgreSQL remains the durable source of truth for persisted events, request status, sequence allocation, idempotency, and DLQ metadata.
- During a PostgreSQL primary outage, already-known stream membership can continue to append to Kafka while the API reports degraded readiness.

## Operating Notes

- The performance baseline measures the Kafka append-centered intake path.
- Idempotency-enabled write load is tracked as a separate state-path performance profile.
- Long-duration 500+ VU testing is treated as a capacity profile.
- Dedicated Grafana panels for Kafka DLQ depth and consumer lag can improve operations visibility.
