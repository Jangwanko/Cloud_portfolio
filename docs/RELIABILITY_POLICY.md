# 신뢰성 정책

이 문서는 Redis / PostgreSQL 신뢰성 기준과 readiness / alert 정책을 한 곳에 정리한 운영 문서입니다.

## 목적
- Redis complete outage와 single-node failover를 같은 상태로 보지 않도록 기준을 분리합니다.
- readiness는 현재 사실을 즉시 반영하고, alert는 짧은 failover 흔들림에 과민하게 반응하지 않도록 분리합니다.
- Redis가 단순 cache가 아니라 intake buffer라는 점을 문서와 운영 기준에 명확히 남깁니다.

## Redis 역할
- Redis는 이 시스템에서 accepted write request를 잠시 받는 `intake buffer` 역할을 합니다.
- 최종 영속 저장소는 PostgreSQL입니다.
- 즉, Redis는 write path에서 중요하지만 최종 기준 저장소는 아닙니다.

## Redis persistence 정책
- Redis는 `AOF everysec`과 `RDB snapshot`을 함께 사용합니다.
- 목적은 성능과 내구성 사이에서 균형을 잡는 것입니다.
- 이 정책에서는 PostgreSQL 영속화 이전 구간에서 최악의 경우 약 1초 내외 손실 가능성을 허용합니다.

## Redis fail-fast 정책
- writable master가 unreachable이면 Redis total outage로 봅니다.
- Sentinel이 현재 master를 결정하지 못하면 Redis total outage로 봅니다.
- Redis total outage 동안에는 API가 새 write request를 계속 받지 않고 fail-fast로 전환합니다.
- 즉, enqueue가 불가능한 상태를 일시적인 약화가 아니라 write path failure로 취급합니다.

## 상태 모델

### `ready`
- Redis writable master reachable
- Redis Sentinel master resolved
- Redis replica count `2+`
- Redis replica link 정상
- PostgreSQL writable primary reachable
- PostgreSQL standby count `2+`
- PostgreSQL standby replication state `streaming`
- PostgreSQL replication lag 정상

### `degraded`
- Redis master는 writable이지만 replica count가 `1` 이하
- Redis replica 중 `master_link_status`가 비정상
- Sentinel은 master를 찾았지만 quorum 또는 topology가 불안정
- Redis enqueue는 가능하지만 PostgreSQL writable primary가 일시적으로 unavailable
- PostgreSQL primary는 writable이지만 standby count가 `1` 이하
- PostgreSQL replication state가 불안정
- PostgreSQL replication lag가 임계치 초과

### `not_ready`
- Redis writable master unreachable
- Redis Sentinel master unresolved
- Redis 인증 / 연결 실패로 enqueue 불가

PostgreSQL writable primary unreachable은 API readiness에서는 `degraded`로 봅니다. Redis intake buffer가 요청을 받을 수 있다면 서비스는 새 요청을 `accepted` 하고, PostgreSQL 복구 후 worker가 영속화를 이어갈 수 있기 때문입니다. 단, persistence path가 막힌 상태이므로 alert에서는 critical로 봅니다.

## readiness 정책
- readiness는 현재 사실을 즉시 반영합니다.
- `ready`, `degraded`, `not_ready` 전환에 별도 유예를 두지 않습니다.
- replica count와 standby count는 degraded 판단 기준이지, grace 기반 지연 판정 대상이 아닙니다.
- 단순 up/down만 보지 않고 role, link, sync 상태를 함께 봅니다.

## readiness 응답 원칙
- readiness 응답에는 운영 판단에 필요한 최소 정보만 넣습니다.
- 권장 필드는 아래와 같습니다.

공통:
- `status`
- `reason`
- `grace_remaining_seconds`

Redis:
- `master_reachable`
- `replica_count`
- `sentinel_master_ok`

PostgreSQL:
- `primary_reachable`
- `standby_count`
- `sync_standby_count`

예시:

```json
{
  "status": "degraded",
  "reason": [
    "redis_replica_count_low",
    "postgres_replication_lag_high"
  ],
  "grace_remaining_seconds": 18,
  "redis": {
    "master_reachable": true,
    "replica_count": 1,
    "sentinel_master_ok": true
  },
  "postgres": {
    "primary_reachable": true,
    "standby_count": 1,
    "sync_standby_count": 0
  }
}
```

로컬 데모에서는 `sync_standby_count`가 `0`이어도 standby가 `streaming`이고 lag가 정상이라면 `ready`로 봅니다. 이 값은 sync/async 상태를 보여주는 보조 정보입니다.

## alert 정책
- `30초`는 readiness 유예가 아니라 alert 승격 유예입니다.
- 상태가 나빠지면 readiness는 바로 `degraded` 또는 `not_ready`를 반영합니다.
- 짧은 failover 흔들림은 흔하므로 degraded warning은 `30초` 동안 유지한 뒤 필요 시 승격합니다.
- Redis total outage는 즉시 critical로 봅니다.
- PostgreSQL primary loss도 즉시 critical로 봅니다. 다만 Redis enqueue가 가능하면 API readiness는 `not_ready`가 아니라 `degraded`입니다.

## 운영 해석
- Redis total outage는 cache miss가 아니라 intake write path 중단입니다.
- Redis degraded는 “쓰기 자체는 가능하지만 topology가 기대치보다 약해진 상태”입니다.
- PostgreSQL degraded는 “intake는 가능하지만 persistence path 또는 HA 여유가 약해진 상태”입니다.
- 따라서 운영자는 `ready / degraded / not_ready`를 단순 bool이 아니라 실제 장애 성격 구분으로 해석해야 합니다.
