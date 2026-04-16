# Architecture

## 구성 요소
- API (`FastAPI`)
  - 트랜잭션 요청 수락
  - Redis ingress queue 적재
  - health / readiness / metrics 노출
- Worker
  - Redis queue 소비
  - PostgreSQL 영속화
  - retry / DLQ 처리
- DLQ Replayer
  - DLQ 적재 요청 재투입
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
- ingress-nginx
  - 로컬 kind 환경의 HTTP ingress 진입점
  - self-signed certificate 기반 HTTPS termination

## 외부 진입
현재 로컬 검증 기준 외부 진입은 아래와 같습니다.

- API: `http://localhost`
- TLS API: `https://localhost`
- Grafana: `http://localhost/grafana`
- TLS Grafana: `https://localhost/grafana`
- Prometheus: `http://localhost/prometheus/`
- TLS Prometheus: `https://localhost/prometheus/`

즉 Service는 `ClusterIP`로 내부에 두고, 외부 접근은 `ingress-nginx`가 받아서 각 서비스로 라우팅합니다. 로컬 HTTPS는 self-signed certificate로 종료합니다.

## 요청 처리 흐름
1. 클라이언트가 API로 트랜잭션 요청을 보냅니다.
2. API는 요청을 바로 DB에 쓰지 않고 Redis ingress queue에 적재합니다.
3. Worker가 queue에서 요청을 가져와 PostgreSQL에 영속화합니다.
4. 실패하면 retry를 수행합니다.
5. retry 한도를 넘기면 DLQ로 이동합니다.
6. DLQ Replayer가 복구 후 조건이 맞으면 다시 ingress queue로 재투입합니다.

## 인증 / 인가
현재 최소 범위의 인증 / 인가가 들어가 있습니다.

- 사용자 생성 시 `password_hash` 저장
- `/v1/auth/login`으로 bearer token 발급
- 주요 API는 로그인 사용자 기준으로 처리
- room membership 검증 적용

중요한 점:
- 인증은 token payload를 기준으로 처리해서 DB down 중에도 인증 때문에 요청이 막히지 않도록 구성했습니다.
- DB down 수락 경로를 유지하기 위해 room membership는 Redis에도 캐시합니다.

## 장애 시나리오별 동작

### DB down
- API는 Redis queue로 요청을 계속 수락할 수 있습니다.
- Worker는 DB 쓰기 실패 시 retry를 수행합니다.
- retry 초과 시 DLQ로 이동합니다.
- DB recovery 후 pgpool-backed DB query가 안정화되면 worker와 replayer가 다시 영속화를 진행합니다.

### Redis down
- API는 queue 적재를 할 수 없어 요청 실패가 증가합니다.
- readiness는 `not_ready`로 바뀝니다.
- Worker는 queue 소비를 중단합니다.
- Redis recovery 후 queue 처리와 readiness가 정상화됩니다.

### Worker backlog
- API는 요청을 계속 수락할 수 있지만 queue depth가 증가합니다.
- Worker replica를 늘리거나 부하가 줄면 backlog가 다시 감소합니다.

## Autoscaling
현재 autoscaling은 CPU 기반 HPA로 구성되어 있습니다.

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

현재 검증한 alert 흐름:
- DB outage alert
- Redis outage alert
- recovery 후 alert resolution

## 현재 한계
- HTTPS는 local self-signed certificate 기준이라 브라우저 신뢰 경고가 발생할 수 있습니다.
- `k6`는 실행은 정상이나 latency threshold는 아직 실패합니다.
- 멀티 파드 환경에서 요청 순서 보장 검증은 더 필요합니다.
