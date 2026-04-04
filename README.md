# Messaging Portfolio (Interview Edition)

로컬에서 바로 실행 가능한 메시징 백엔드 + 간단 프론트 + 송수신 관측툴을 포함한 포트폴리오입니다.

## 핵심 포인트
- Frontend: 메시지 전송/조회/읽음 처리 데모 UI
- API: FastAPI 기반 메시징 API
- Worker: Redis Queue 소비 후 수신(처리) 이벤트 기록
- Observer: 메시지 "전송 vs 수신" 타임라인 관측 화면
- DB/Cache: PostgreSQL + Redis
- 확장 설계: Kubernetes에서 PostgreSQL HA + Redis Sentinel quorum failover

## 1) 로컬 실행
```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
```

접속 주소:
- Frontend UI: http://localhost
- Swagger: http://localhost/api/docs
- Observer: http://localhost/observer/
- Readiness: http://localhost/api/health/ready

## 2) 스모크 테스트
```powershell
powershell -ExecutionPolicy Bypass -File scripts/smoke_test.ps1
```

정상 예시:
- `health=ready message_count=1 unread=0 observer_messages=...`

## 3) 메시지 관측 방식
- 전송(send): `messages` 테이블에 기록
- 수신(receive): worker가 Redis queue 소비 후 `notification_attempts` 기록
- Observer 화면에서 두 타임라인을 동시에 확인 가능

## 4) 면접 설명 포인트
- `X-Idempotency-Key`로 중복 메시지 생성 방지
- API/Worker 분리로 비동기 처리 구조
- Health endpoint 분리 (`/health/live`, `/health/ready`)
- 프론트와 관측툴로 기능 + 운영 시야를 동시에 보여줌

## 5) Kubernetes HA (Quorum Failover)
`k8s/` 폴더에 로컬 kind 실습용 설정 포함:
- PostgreSQL HA: multi-node 자동 failover
- Redis Sentinel: `quorum=2` 기반 승격

가이드:
- [k8s/README.md](k8s/README.md)
- [k8s/values/postgresql-ha-values.yaml](k8s/values/postgresql-ha-values.yaml)
- [k8s/values/redis-ha-values.yaml](k8s/values/redis-ha-values.yaml)

## 6) 종료
```powershell
docker compose down
```

데이터까지 초기화:
```powershell
docker compose down -v
```
