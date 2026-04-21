# 관측 지표 안내

이 문서는 Grafana / Prometheus에서 확인할 수 있는 주요 지표와 해석 기준을 정리합니다.

## 목적

이 프로젝트의 관측 목표는 단순히 Pod가 살아 있는지 확인하는 데서 끝나지 않습니다.

- API / Worker 처리량과 지연 시간을 확인합니다.
- Redis queue depth와 Redis HA topology를 확인합니다.
- PostgreSQL primary / standby / replication 상태를 확인합니다.
- `ready`, `degraded`, `not_ready` 상태를 운영자가 빠르게 해석할 수 있게 합니다.

## 대시보드 구성

Grafana의 `Messaging Portfolio Overview` 대시보드는 아래 묶음으로 구성됩니다.

- API request rate / latency
- Worker throughput
- Queue depth
- Redis health / role / replica / Sentinel 상태
- PostgreSQL primary / standby / sync / replication 상태
- DB failure reason

## 공통 지표

### `messaging_health_status`

component별 health 신호입니다.

- `component="redis"`: Redis writable master와 Sentinel master 확인 기준
- `component="db"`: PostgreSQL writable primary 확인 기준
- `component="worker"`: Worker 처리 루프 상태

이 값은 전체 장애 판단에는 유용하지만, replica 부족이나 sync mismatch 같은 `degraded` 원인을 모두 설명하지는 않습니다.

## Worker 지표

### `messaging_worker_processed_total`

Worker가 Redis ingress queue에서 이벤트를 꺼내 처리한 누적 건수입니다. Grafana의 `Worker Throughput` 패널은 이 counter를 기반으로 최근 처리량을 계산합니다.

```promql
sum(rate(messaging_worker_processed_total[1m])) by (result)
```

이 패널은 아래 상황을 확인할 때 사용합니다.

- Worker가 queue를 실제로 소비하는지 확인
- 처리 결과가 `success`인지 `failure`인지 확인
- DB 장애 복구 후 backlog가 다시 처리되는지 확인
- 부하 테스트 중 Worker 처리량이 증가하는지 확인

주의할 점은 idle 상태입니다. 새 이벤트가 들어오지 않았거나 Worker가 아직 한 번도 이벤트를 처리하지 않았다면 counter 자체가 생성되지 않아 Grafana에서 `No data`로 보일 수 있습니다. 이 경우는 장애가 아니라 “처리할 이벤트가 없는 상태”일 수 있습니다.

값을 확인하려면 smoke test 또는 API 요청으로 이벤트를 생성한 뒤, Grafana 시간 범위를 `Last 15 minutes` 정도로 넓혀 확인합니다.

## Redis 지표

### `messaging_redis_role`

API가 연결한 Redis 노드의 role을 보여줍니다. 정상 기준에서는 `role="master"` 값이 `1`로 관측됩니다.

### `messaging_redis_connected_replicas`

writable master에 연결된 Redis replica 수입니다.

- `2+`: ready 기준
- `1 이하`: degraded 기준

### `messaging_redis_master_link_status`

각 Redis replica의 master link 상태입니다.

- `1`: 정상
- `0`: 비정상

일부 replica link가 내려가면 master가 살아 있어도 topology는 `degraded`로 해석합니다.

### `messaging_redis_sentinel_master_ok`

Sentinel이 writable master를 식별할 수 있는지 나타냅니다.

- `1`: master 식별 가능
- `0`: Sentinel master 미결정 또는 master 상태 이상

이 값이 `0`이면 Redis total outage에 가까운 상태로 보고 즉시 확인해야 합니다.

### `messaging_redis_sentinel_quorum_ok`

Sentinel quorum이 유지되는지 나타냅니다. master는 살아 있지만 quorum이 깨지면 failover 여력이 낮아진 상태이므로 `degraded`로 해석합니다.

## PostgreSQL 지표

### `messaging_postgres_is_primary`

pgpool 경유로 writable primary가 reachable한지 나타냅니다.

- `1`: primary reachable
- `0`: write path unavailable

### `messaging_postgres_standby_count`

pgpool이 up 상태로 보고하는 standby 수입니다.

- `2+`: ready 기준
- `1 이하`: degraded 기준

### `messaging_postgres_sync_standby_count`

sync 또는 quorum 기준을 만족하는 standby 수입니다.

- 로컬 데모에서는 `0`이어도 standby가 `streaming`이고 lag가 정상이라면 `ready`로 봅니다.
- 이 값은 “현재 복제가 async인지 sync인지”를 설명하는 보조 지표입니다.
- 따라서 이 값만 단독으로 alert critical 기준으로 사용하지 않습니다.

### `messaging_postgres_replication_state_count`

replication state별 standby 수입니다. 정상 기준에서는 standby가 `streaming`으로 관측되는 것이 기대값입니다.

이 값은 pgpool의 `SHOW pool_nodes`가 비워 두는 경우가 있어, API는 `pg_stat_replication`을 보조 소스로 사용합니다. 이를 위해 로컬 HA 설치 스크립트는 `portfolio` 사용자에게 읽기 전용 모니터링 역할인 `pg_monitor`를 부여합니다.

### `messaging_postgres_replication_sync_state_count`

sync state별 standby 수입니다. `sync`, `quorum`, `async` 분포를 보고 복제 안정성을 판단합니다.

로컬 kind 데모에서는 `async` standby도 정상 운영 상태로 취급합니다. 이 프로젝트의 로컬 ready 기준은 sync standby 보유 여부보다 `streaming` 상태, standby 수, replication lag 정상 여부에 둡니다.

### `messaging_postgres_replication_delay_bytes_max`

가장 큰 replication delay를 byte 기준으로 보여줍니다. 임계치를 넘으면 replication lag 상승으로 보고 `degraded`로 해석합니다.

## 상태 해석

### `ready`

- Redis writable master 정상
- Redis replica 2개 이상
- Redis replica link 정상
- Sentinel master 식별 가능
- PostgreSQL writable primary 정상
- PostgreSQL standby 2개 이상
- PostgreSQL standby가 `streaming` 상태
- PostgreSQL replication lag 정상

### `degraded`

서비스 연결 자체는 가능하지만 HA 여력 또는 replication 안정성이 낮아진 상태입니다.

- Redis replica 부족
- Redis replica link 불안정
- Sentinel quorum 저하
- Redis enqueue는 가능하지만 PostgreSQL writable primary가 일시적으로 unavailable
- PostgreSQL standby 부족
- PostgreSQL replication state 불안정
- PostgreSQL replication lag 상승

### `not_ready`

intake write path가 실제로 막힌 상태입니다.

- Redis writable master unreachable
- Sentinel master 미결정
- Redis 인증 / 연결 실패로 enqueue 불가

PostgreSQL writable primary unreachable은 API readiness에서는 `degraded`로 봅니다. Redis intake buffer가 요청을 받을 수 있다면 API는 새 요청을 `accepted` 하고, PostgreSQL 복구 후 worker가 영속화를 이어갈 수 있기 때문입니다. 단, persistence path가 막힌 상태이므로 alert에서는 critical로 봅니다.

## Alert 해석

- readiness는 현재 상태를 즉시 반영합니다.
- 30초는 readiness 유예가 아니라 alert 승격 유예입니다.
- Redis total outage와 PostgreSQL primary loss는 즉시 critical입니다.
- degraded warning은 failover 직후 일시적인 흔들림을 줄이기 위해 30초 지속 후 발생합니다.

## Prometheus Query 기준

Redis topology와 PostgreSQL replication 지표는 현재 API job이 채웁니다.

따라서 Grafana dashboard와 Redis / PostgreSQL alert에서는 아래처럼 `job="api"` 기준으로 조회합니다.

```promql
messaging_health_status{job="api",component="redis"}
messaging_health_status{job="api",component="db"}
messaging_redis_connected_replicas{job="api"}
messaging_postgres_standby_count{job="api"}
```

Worker도 같은 metric name을 노출할 수 있지만 topology 판정은 API가 수행하므로, Redis / PostgreSQL 운영 해석에서는 `job="api"` 시계열을 기준으로 봅니다. Worker 자체 이상 여부는 `component="worker"` 또는 worker 전용 처리량 / 실패율 지표로 봅니다.
