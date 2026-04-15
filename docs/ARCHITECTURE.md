# Architecture

## 구성 요소
- API (`FastAPI`)
  - 메시지 요청 수락
  - Redis ingress queue 적재
  - health / readiness / metrics 노출
- Worker
  - Redis queue 소비
  - PostgreSQL 영속화
  - retry / DLQ 처리
- DLQ Replayer
  - DLQ 적재 메시지 재투입
- PostgreSQL HA
  - `bitnami/postgresql-ha` 기반
  - pgpool 경유 접근
- Redis HA
  - replica + Sentinel 구성
- Prometheus / Grafana
  - metrics 수집, alert, dashboard
- Kubernetes HPA
  - API / Worker CPU 기반 autoscaling
- metrics-server
  - HPA용 resource metrics 제공

## 외부 진입
현재 로컬 검증 기준 외부 진입은 아래와 같습니다.

- API: `NodePort 30080`
- Grafana: `NodePort 30300`

즉 내부 서비스는 Kubernetes 안에서 동작하지만, 외부 공개 계층은 아직 `Ingress` 중심이 아니라 `NodePort` 중심입니다.

## 요청 처리 흐름
1. 클라이언트가 API에 메시지 요청을 보냅니다.
2. API는 요청을 바로 DB에 쓰지 않고 Redis ingress queue에 적재합니다.
3. Worker가 queue에서 메시지를 가져와 PostgreSQL에 저장합니다.
4. 저장 실패 시 retry를 수행합니다.
5. retry 한도를 넘기면 DLQ로 이동합니다.
6. DLQ Replayer가 복구 후 조건이 맞으면 다시 ingress queue로 재투입합니다.

## 장애 시나리오별 동작

### DB down
- API는 Redis queue에 요청을 계속 수락할 수 있습니다.
- Worker는 DB 저장 실패 시 retry를 수행합니다.
- retry 한도 초과 시 DLQ로 이동합니다.
- DB recovery 후 pgpool-backed DB query가 안정화되면 worker와 replayer가 다시 영속화를 진행합니다.

### Redis down
- API는 queue 적재를 할 수 없어 요청 실패가 증가합니다.
- readiness는 `not_ready`로 바뀝니다.
- Worker는 queue 소비를 중단합니다.
- Redis recovery 후 queue 처리와 readiness가 정상화됩니다.

### Worker backlog
- API는 요청을 계속 수락할 수 있지만 queue depth가 증가합니다.
- Worker replica를 늘리거나 재기동하면 backlog가 다시 줄어듭니다.

## Autoscaling
현재 autoscaling은 CPU 기반 HPA로 구성돼 있습니다.

- API HPA
  - min replicas: `3`
  - max replicas: `8`
  - target CPU: `65%`
- Worker HPA
  - min replicas: `2`
  - max replicas: `4`
  - target CPU: `70%`

최근 검증에서는 API replica가 `3 -> 5`, `3 -> 6`으로 scale-up 되는 것을 확인했습니다.

## Observability
현재 관측 항목:
- API request count / latency
- worker processing count / latency
- queue depth
- DB / Redis / Worker health
- Prometheus alert firing / resolution

현재 검증된 alert 흐름:
- DB outage alert
- Redis outage alert
- recovery 후 alert resolution

## 현재 한계
- 외부 진입은 아직 `Ingress` 중심으로 정리되지 않았습니다.
- `k6`는 실행은 정상이나 latency threshold는 아직 실패합니다.
- 멀티 파드 환경에서 메시지 순서 보장 검증은 더 필요합니다.
