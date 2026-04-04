# Interview Questions And Study Notes

이 문서는 현재 프로젝트를 기준으로, 실무자 면접에서 나올 수 있는 질문과 답변을 정리한 문서입니다.  
목적은 단순 암기가 아니라 질문을 따라가며 시스템 설계와 CS 개념을 같이 익히는 것입니다.

원칙은 두 가지입니다.

- 현재 구현한 것은 구현했다고 말하고, 아직 설계 수준인 것은 설계 수준이라고 구분한다.
- 답변은 "왜 그렇게 설계했는지"와 "그 선택의 한계는 무엇인지"까지 같이 설명한다.

---

## 1. 프로젝트 목적

### Q1. 이 프로젝트를 왜 만들었나?
A.
이 프로젝트는 채팅 기능 자체를 보여주기보다, 메시징 시스템을 어떻게 운영 가능한 구조로 만들지 보여주기 위해 만들었습니다.

보여주고 싶었던 핵심은 아래 네 가지입니다.

1. 메시지 요청을 왜 API에서 바로 DB에 쓰지 않고 queue와 worker로 분리해야 하는가
2. DB가 내려간 동안 들어온 요청을 어떻게 보존할 것인가
3. 장애를 단순 예외가 아니라 운영 이벤트로 보고, 원인과 복구 시간을 어떻게 측정할 것인가
4. 로컬 데모 구조를 Kubernetes HA나 AWS 운영 구조로 어떻게 확장 설명할 것인가

즉 "기능 구현"보다 "운영 가능한 메시지 처리 경로"를 보여주는 포트폴리오라고 설명하는 것이 맞습니다.

### Q2. 이 프로젝트에서 가장 중요하게 본 문제는 무엇인가?
A.
가장 중요하게 본 문제는 "DB가 죽어도 요청을 먼저 받아 둘 수 있느냐"였습니다.

일반적인 CRUD 프로젝트는 DB가 정상이라는 전제 위에서 동작합니다. 하지만 메시징 시스템은 순간적인 DB 장애, 재시작, failover 상황이 충분히 발생할 수 있습니다. 이때 요청 자체를 잃지 않는 구조가 더 중요하다고 생각했습니다.

그래서 현재 구조는 API가 바로 DB를 source of truth로 두되, 요청 수신 경로는 queue-first로 구성해 DB 장애 중에도 요청을 잃지 않도록 만들었습니다.

---

## 2. 전체 구조와 흐름

### Q3. 현재 메시지 요청은 어떤 순서로 처리되나?
A.
현재 흐름은 다음과 같습니다.

1. 클라이언트가 메시지 생성 요청을 보냅니다.
2. API가 `request_id`를 만들고 idempotency key를 확인합니다.
3. API는 요청을 Redis ingress queue에 적재합니다.
4. API는 즉시 `accepted` 응답을 반환합니다.
5. Worker가 queue에서 요청을 꺼냅니다.
6. Worker가 PostgreSQL에 실제 메시지를 저장합니다.
7. 저장이 끝나면 request status를 `persisted`로 갱신합니다.
8. 이후 notification queue에 후속 작업을 넣습니다.

핵심은 API가 "수신과 적재"를 담당하고, Worker가 "최종 저장과 후처리"를 담당한다는 점입니다.

### Q4. 왜 API가 PostgreSQL에 바로 쓰지 않게 했나?
A.
바로 DB에 쓰는 구조는 단순하지만, DB 상태가 요청 성공 여부를 바로 결정하게 됩니다.

예를 들어 DB가 재시작 중이거나 잠깐 연결이 끊기면, 사용자 요청이 그대로 실패합니다. 메시징 시스템에서는 이런 순간적인 장애 때문에 요청이 유실되는 것이 더 치명적일 수 있습니다.

그래서 현재는 API가 먼저 요청을 queue에 넣고 빠르게 응답하게 했습니다. 이렇게 하면 DB가 불안정해도 요청을 보존할 수 있고, DB 복구 후 worker가 다시 처리할 수 있습니다.

### Q5. 그러면 이 구조에서 API는 무슨 책임을 갖나?
A.
API의 책임은 아래처럼 줄였습니다.

- 요청 수신
- 최소한의 요청 식별
- queue 적재
- 상태 조회 진입점 제공

즉 API는 "최종 저장 책임"보다 "요청을 놓치지 않는 진입점" 역할에 더 가깝습니다.

### Q6. Worker는 무슨 책임을 갖나?
A.
Worker는 아래 책임을 가집니다.

- queue에서 요청 소비
- DB 저장
- request status 갱신
- notification queue 적재
- 장애 시 재시도 흐름 유지

즉 Worker는 비동기 저장 파이프라인의 중심입니다.

---

## 3. queue-first 구조

### Q7. 왜 queue-first 구조를 선택했나?
A.
메시징 시스템에서 중요한 것은 "바로 저장됐느냐"보다 "요청을 먼저 받았느냐"인 경우가 많기 때문입니다.

queue-first 구조의 장점은 아래와 같습니다.

- DB가 일시적으로 내려가도 요청을 보존할 수 있습니다.
- API 응답 시간을 짧게 가져갈 수 있습니다.
- 저장 처리량과 요청 유입량을 분리할 수 있습니다.
- worker 수를 조절해 처리량을 확장할 수 있습니다.

단점도 있습니다.

- `accepted`와 `persisted`를 분리해 관리해야 합니다.
- 사용자가 보기에 최종 저장과 즉시 응답이 어긋날 수 있습니다.
- 중복 처리와 순서 보장이 더 중요해집니다.

실무에서는 이 장단점을 보고 queue-first를 선택하게 됩니다.

### Q8. queue-first가 항상 좋은가?
A.
항상 그렇지는 않습니다.

단순한 CRUD 서비스라면 오히려 동기 DB 저장이 더 단순하고 일관성도 명확합니다. queue-first는 장애 대응과 처리량 분리에는 강하지만, 상태 관리가 복잡해집니다.

그래서 "왜 queue-first를 썼나?"에 대한 좋은 답변은 "메시징 시스템에서는 장애 중 요청 보존과 비동기 처리 분리가 더 중요해서" 입니다. 모든 서비스에 무조건 좋은 구조라고 말하면 오히려 위험합니다.

### Q9. accepted 와 persisted 를 왜 나눴나?
A.
queue-first 구조에서는 요청 수신과 최종 저장이 같은 시점이 아니기 때문입니다.

- `accepted`: 요청이 API에 들어왔고 queue 적재까지 성공한 상태
- `persisted`: Worker가 PostgreSQL 저장까지 완료한 상태

이 둘을 구분하지 않으면, 실제로는 아직 저장되지 않은 요청을 저장 완료처럼 보이게 만들 수 있습니다. 그래서 상태를 분리하는 것은 구조적으로 필수입니다.

### Q10. 클라이언트는 언제 성공이라고 봐야 하나?
A.
이건 시스템 성공과 UX 성공을 구분해서 말하는 것이 좋습니다.

- UX 관점의 빠른 성공: `accepted`
- 시스템 관점의 최종 성공: `persisted`

즉 사용자는 "서버가 내 요청을 받았다"는 의미로는 `accepted`를 성공으로 느낄 수 있지만, 데이터가 실제 DB에 저장됐다는 의미의 최종 성공은 `persisted` 입니다.

---

## 4. Redis와 Queue 선택

### Q11. 왜 Kafka나 RabbitMQ 대신 Redis를 썼나?
A.
이 프로젝트의 목적은 대규모 브로커를 직접 운영하는 것이 아니라, 메시지 요청을 앞단에서 보존하고 비동기 저장으로 넘기는 흐름을 보여주는 것이었습니다.

Redis를 선택한 이유는 아래와 같습니다.

- 로컬 데모가 가볍습니다.
- 구현 속도가 빠릅니다.
- queue, 상태 저장, reconnect 실험을 한 저장소 안에서 보여주기 좋습니다.

하지만 운영까지 그대로 가겠다는 뜻은 아닙니다. 실제 운영에서는 durability 요구사항 때문에 SQS, Kafka, Redis Streams 같은 다른 선택지를 충분히 검토해야 합니다.

### Q12. Redis List를 쓴 이유는?
A.
현재 단계에서는 구조 설명이 우선이었기 때문입니다.

Redis List는 다음 장점이 있습니다.

- 개념이 단순합니다.
- 빠르게 queue 동작을 구현할 수 있습니다.
- `LPUSH` / `BRPOP` 만으로 소비 구조를 만들 수 있습니다.

하지만 단점도 분명합니다.

- ack 개념이 없습니다.
- pending 항목 추적이 약합니다.
- consumer group 모델이 없습니다.

그래서 더 운영형으로 가면 Redis Streams가 더 적합할 수 있습니다.

### Q13. Redis Streams를 쓰지 않은 이유는?
A.
Streams는 더 운영형에 가깝지만, 이 프로젝트에서 설명 범위를 크게 넓혀버립니다.

Streams를 쓰면 아래 개념까지 같이 설명해야 합니다.

- consumer group
- ack
- pending entry list
- 재할당 정책

그 구조는 더 좋지만, 프로젝트 목적이 "메시지 요청 보존과 장애 대응"을 보여주는 것인 만큼 처음에는 List가 더 적절하다고 봤습니다.

### Q14. Redis를 durable queue처럼 써도 되나?
A.
조심해서 말해야 합니다. "쓸 수는 있지만 한계가 분명하다"가 맞습니다.

Redis는 메모리 기반이라 빠르지만, durable broker로서 Kafka나 SQS 같은 수준의 보장을 기본 전제로 두긴 어렵습니다. persistence 전략, 메모리 압박, failover 설정을 어떻게 하느냐에 따라 안정성이 달라집니다.

즉 현재 프로젝트는 "로컬에서 queue-first 개념을 실험하고 설명하는 용도"로 Redis를 쓴 것이고, 운영 환경에서는 요구사항에 따라 broker를 바꾸는 것이 자연스럽습니다.

### Q15. Redis가 죽으면 어떻게 되나?
A.
현재 구조에서 Redis는 ingress queue입니다. 즉 Redis가 죽으면 API의 요청 적재 경로 자체가 영향을 받습니다.

이건 현재 구조의 중요한 한계입니다.

- DB 장애 중 요청 보존: 해결
- Redis 장애 중 요청 보존: 충분히 해결되지 않음

실무에서는 여기서 다음 선택지가 나옵니다.

- Redis HA + persistence 강화
- Redis Streams 사용
- SQS/Kafka 같은 durable broker로 이전

이 한계를 솔직하게 인정하는 것이 중요합니다.

---

## 5. Worker와 비동기 처리

### Q16. Worker를 왜 분리했나?
A.
API 응답 경로와 저장 경로를 분리하기 위해서입니다.

API가 모든 것을 다 하면 아래 문제가 생깁니다.

- 요청 응답 시간이 길어집니다.
- DB나 후처리 장애가 바로 사용자 요청에 전파됩니다.
- 확장 포인트가 적습니다.

Worker를 두면 요청 수신과 실제 저장을 느슨하게 결합할 수 있습니다.

### Q17. Worker가 죽으면 요청은 어떻게 되나?
A.
요청은 이미 queue에 들어가 있기 때문에 worker가 죽어도 요청은 남습니다.

Worker가 다시 시작되면 queue를 다시 소비해서 처리할 수 있습니다. 이런 이유로 비동기 구조는 프로세스 장애에 강합니다.

### Q18. Worker가 같은 요청을 두 번 처리할 수 있나?
A.
그 가능성은 있습니다. 예를 들어 DB 저장 직후 worker가 죽으면, 시스템은 저장 성공 여부를 완벽히 판단하지 못한 채 같은 요청을 다시 처리할 수 있습니다.

그래서 exactly-once를 보장하겠다고 말하기보다, idempotency와 unique key를 통해 중복 영향이 없도록 설계하는 것이 더 현실적입니다.

### Q19. notification queue를 따로 둔 이유는?
A.
메시지 저장과 후속 처리는 책임이 다르기 때문입니다.

메시지 저장은 source of truth를 만드는 작업입니다.  
notification은 그 이후의 파생 작업입니다.

이 둘을 분리하면 아래 장점이 있습니다.

- 저장 실패와 알림 실패를 분리해 볼 수 있습니다.
- 이후 push, fan-out, retry 정책을 독립적으로 가져갈 수 있습니다.
- 장애 원인을 더 잘 나눠서 볼 수 있습니다.

---

## 6. 중복 방지와 정합성

### Q20. X-Idempotency-Key는 왜 필요한가?
A.
네트워크 재시도나 클라이언트 중복 요청은 실제 서비스에서 매우 흔합니다.

예를 들어 모바일 앱에서 응답이 늦으면 사용자가 다시 버튼을 누를 수 있습니다. 이때 같은 메시지가 두 번 저장되면 안 됩니다. 그래서 클라이언트가 같은 요청임을 표현할 수 있게 `X-Idempotency-Key`를 받습니다.

### Q21. request_id와 idempotency key의 차이는?
A.
둘은 역할이 다릅니다.

- `request_id`: 시스템 내부에서 요청을 추적하기 위한 ID
- `X-Idempotency-Key`: 클라이언트가 같은 요청임을 알려주기 위한 키

즉 하나는 내부 추적용, 하나는 외부 중복 제어용입니다.

### Q22. 현재 구조는 at-least-once 인가, exactly-once 인가?
A.
현재 구조는 at-least-once에 가깝습니다.

이유는 queue에 요청이 남아 있고, worker가 장애 후 다시 처리할 수 있기 때문입니다. 대신 같은 요청이 두 번 처리될 수 있는 가능성을 완전히 없애지 못하므로 exactly-once라고 말하긴 어렵습니다.

대신 DB unique key와 idempotency로 "중복 처리가 실제 중복 저장으로 이어지지 않게" 설계했습니다.

### Q23. exactly-once가 왜 어려운가?
A.
분산 시스템에서는 "처리"와 "확인"이 항상 분리되기 때문입니다.

예를 들어 아래 상황을 생각할 수 있습니다.

1. worker가 DB 저장을 완료함
2. 하지만 저장 직후 프로세스가 죽음
3. 시스템은 이 요청이 저장됐는지 못 됐는지 애매해짐

이런 상황에서 exactly-once를 단순하게 보장하는 것은 어렵습니다. 그래서 실무에서는 idempotency, deduplication, unique constraint를 이용해 중복 영향을 통제하는 쪽으로 갑니다.

### Q24. API는 accepted를 줬는데 결국 failed가 될 수 있나?
A.
그럴 수 있습니다.

예를 들어 room이나 user가 유효하지 않으면 API는 queue 적재까지만 보고 `accepted`를 줄 수 있지만, worker가 실제 저장 시점에서 유효성 문제를 발견하고 `failed`로 바꿀 수 있습니다.

이건 queue-first 구조에서 생기는 trade-off입니다. 그래서 상태 조회 API를 함께 두는 것이 중요합니다.

---

## 7. PostgreSQL 기초와 역할

### Q25. 왜 PostgreSQL을 source of truth로 뒀나?
A.
메시지 데이터는 단순 저장만 필요한 것이 아니라 조회, 정렬, unread count, 읽음 처리 같은 관계형 질의가 필요합니다.

PostgreSQL을 둔 이유는 아래와 같습니다.

- 메시지 목록 조회가 쉽습니다.
- room / user 관계를 모델링하기 좋습니다.
- transaction과 unique constraint를 활용할 수 있습니다.
- 최종 저장소로 설명하기 자연스럽습니다.

### Q26. connection pool은 왜 필요한가?
A.
DB 연결을 요청마다 새로 열고 닫으면 비용이 큽니다. TCP 연결, 인증, 세션 생성이 반복되기 때문입니다.

connection pool은 미리 연결을 만들어두고 재사용하게 해줍니다.  
이렇게 하면 응답 속도가 빨라지고 DB 연결 관리도 쉬워집니다.

### Q27. pool이 죽거나 stale connection이 생기면 어떻게 되나?
A.
실제 운영에서는 DB 재시작이나 네트워크 단절 이후 pool 안에 죽은 연결이 남을 수 있습니다. 이걸 stale connection이라고 볼 수 있습니다.

현재 프로젝트에서는 이런 경우 pool을 재초기화하고 다시 연결하도록 했습니다. 즉 "한 번 열린 연결은 영원히 정상일 것"이라고 가정하지 않고, 런타임 재연결을 고려했습니다.

### Q28. transaction은 왜 중요한가?
A.
transaction은 여러 DB 작업을 하나의 논리적 단위로 묶기 위해 필요합니다.

예를 들어 메시지 저장과 idempotency key 기록이 같이 성공하거나 같이 실패해야 할 수 있습니다. transaction이 없으면 일부만 반영된 불완전 상태가 남을 수 있습니다.

메시징 시스템처럼 중복과 정합성이 중요한 구조에서는 transaction 개념을 꼭 알고 있어야 합니다.

---

## 8. PostgreSQL HA, replication, failover

### Q29. replication은 무엇인가?
A.
replication은 primary의 데이터를 replica에 복제하는 것입니다.

주요 목적은 다음과 같습니다.

- 장애 시 대체 노드 확보
- 읽기 분산 가능성 확보
- 데이터 안정성 향상

하지만 replication은 backup과 다릅니다. replication은 실시간/준실시간 복제이고, backup은 시점 복구를 위한 별도 데이터 사본입니다.

### Q30. synchronous replication과 asynchronous replication 차이는?
A.
동기 복제는 primary가 commit을 완료하기 전에 replica 반영까지 기다립니다.  
비동기 복제는 primary가 먼저 commit하고 replica는 나중에 따라갑니다.

동기 복제 장점:
- 데이터 유실 가능성이 낮습니다.

동기 복제 단점:
- 지연이 커질 수 있습니다.
- replica 상태가 write 성능에 영향을 줄 수 있습니다.

비동기 복제 장점:
- 빠릅니다.

비동기 복제 단점:
- primary 장애 시 최신 데이터 일부를 잃을 수 있습니다.

### Q31. quorum은 왜 필요한가?
A.
quorum은 과반 동의 또는 과반 생존 기준이라고 이해하면 됩니다.

HA에서 quorum이 필요한 이유는 split-brain을 막기 위해서입니다.  
split-brain은 여러 노드가 동시에 자신이 primary라고 주장하는 상태입니다. 이 상황이 발생하면 데이터가 갈라져서 더 위험해집니다.

그래서 홀수 개 노드를 두고, 과반을 만족해야 primary 승격이 가능하도록 하는 구조가 흔합니다.

### Q32. pgpool은 무엇을 하나?
A.
pgpool은 PostgreSQL 앞단의 진입점 역할을 합니다.

앱 입장에서는 직접 primary를 찾기보다 pgpool에 연결하고, pgpool이 적절한 PostgreSQL 노드로 연결을 전달하게 합니다. 이렇게 하면 앱이 DB topology를 직접 알 필요가 줄어듭니다.

### Q33. repmgr는 무엇을 하나?
A.
repmgr는 PostgreSQL replication cluster를 관리하는 도구입니다.

주요 역할은 다음과 같습니다.

- primary / standby 관계 관리
- 장애 감지
- standby 승격
- 노드 재합류 관리

즉 failover orchestration을 담당한다고 보면 됩니다.

### Q34. failover와 recovery는 어떻게 다른가?
A.
failover는 장애가 났을 때 다른 노드를 primary/master로 승격하는 과정입니다.  
recovery는 장애난 노드가 다시 시스템에 합류해 정상 상태를 회복하는 과정입니다.

면접에서는 이 둘을 구분해서 말하는 것이 좋습니다.  
"새 primary가 생기는 시간"과 "기존 primary가 standby로 돌아오는 시간"은 다른 지표입니다.

### Q35. replication lag는 왜 중요한가?
A.
replication lag는 primary와 replica 사이 데이터 반영 차이입니다.

lag가 크면 아래 문제가 생길 수 있습니다.

- 읽기 일관성이 깨질 수 있습니다.
- 장애 시 최신 데이터 일부를 잃을 수 있습니다.
- failover 이후 기대와 다른 데이터 상태를 볼 수 있습니다.

그래서 HA 설명에서 replication lag는 중요한 운영 지표입니다.

---

## 9. Redis Sentinel과 failover

### Q36. Redis Sentinel은 무엇인가?
A.
Redis Sentinel은 Redis master를 감시하고, master 장애 시 replica를 승격시키는 감시/선출 계층입니다.

주요 역할은 다음과 같습니다.

- master 감시
- 장애 감지
- quorum 합의
- replica 승격
- 클라이언트에게 새 master 정보 제공

### Q37. Sentinel quorum 2는 무슨 의미인가?
A.
예를 들어 Sentinel이 3개일 때 quorum 2는 최소 2개가 master 장애를 인정해야 failover가 가능하다는 뜻입니다.

이렇게 해야 한 노드의 일시적 오판이나 네트워크 문제 때문에 잘못된 failover가 일어나는 것을 줄일 수 있습니다.

### Q38. Redis failover 중 클라이언트는 어떤 영향을 받나?
A.
master 장애 순간에는 write 실패나 재연결 오류가 발생할 수 있습니다.  
Sentinel이 새 master를 선출하고 클라이언트가 새 master를 인지하는 동안 짧은 불안정 구간이 생길 수 있습니다.

그래서 client reconnect, retry 정책이 중요합니다.

### Q39. 기존 master는 다시 올라오면 자동으로 replica가 되나?
A.
현재 실험 구조에서는 다시 올라온 노드가 replica로 재합류하는 것을 확인했습니다.  
다만 이 동작은 Sentinel과 chart 설정, 클러스터 상태에 의존합니다. 그래서 "항상 자동으로 된다"가 아니라 "현재 구성에서는 그렇게 검증했다"고 말하는 것이 더 정확합니다.

---

## 10. 장애 대응과 운영 관측

### Q40. 장애 원인을 왜 분류해서 세나?
A.
운영에서는 "에러가 몇 번 났다"보다 "어떤 종류의 에러가 반복되나"가 더 중요하기 때문입니다.

예를 들어:

- `dns_resolution`: 서비스 디스커버리나 DNS 문제 가능성
- `connection_refused`: 프로세스 다운 가능성
- `timeout`: 네트워크 지연이나 과부하 가능성
- `server_closed_connection`: 서버 재시작이나 세션 단절 가능성

이렇게 나눠야 원인 추적이 빨라집니다.

### Q41. readiness와 liveness를 왜 분리했나?
A.
liveness는 "프로세스가 죽었는가"를 보고, readiness는 "지금 트래픽을 받아도 되는가"를 봅니다.

예를 들어 API 프로세스는 살아 있지만 DB가 완전히 죽어 있으면, 프로세스는 살아 있으므로 liveness는 성공할 수 있습니다. 하지만 이 상태에서 요청을 받으면 제대로 처리할 수 없으므로 readiness는 실패해야 합니다.

이 둘을 구분하지 않으면 운영 중 잘못된 재시작이나 잘못된 트래픽 라우팅이 생길 수 있습니다.

### Q42. Prometheus에서 가장 먼저 볼 메트릭은?
A.
현재 구조에서는 아래 다섯 개를 먼저 봅니다.

- `messaging_queue_depth`
- `messaging_db_failure_total`
- `messaging_db_reconnect_total`
- `messaging_worker_processed_total`
- `messaging_api_request_latency_seconds`

이 다섯 개만 봐도 적체, DB 불안정, worker 처리 실패, 사용자 지연을 빠르게 파악할 수 있습니다.

### Q43. queue depth는 왜 중요한가?
A.
queue depth는 시스템이 현재 유입을 감당하고 있는지 바로 보여줍니다.

- 계속 증가: worker 처리량 부족, downstream 장애, retry 적체 가능성
- 계속 0: 유입이 적거나 소비가 매우 빠름
- 급격한 spike: 순간 트래픽 증가나 장애 징후 가능성

운영에서는 queue depth가 "시스템이 밀리고 있는가"를 가장 쉽게 보여주는 지표입니다.

### Q44. p95, p99 latency를 왜 보나?
A.
평균 latency만 보면 tail latency를 놓치기 쉽기 때문입니다.

예를 들어 대부분 요청은 빠르지만 일부 요청만 매우 느릴 수 있습니다.  
이런 문제는 평균값보다 p95, p99에서 더 잘 드러납니다.

실무에서는 사용자 체감 문제를 tail latency에서 많이 발견합니다.

### Q45. 복구 시간을 왜 메트릭으로 봐야 하나?
A.
단순히 "복구됐다"는 말은 운영적으로 충분하지 않기 때문입니다.

실무에서는 아래처럼 숫자가 필요합니다.

- primary가 죽은 뒤 새 primary가 되기까지 몇 초 걸렸는가
- 다시 정상 readiness까지 얼마나 걸렸는가
- 기존 노드가 재합류하는 데 몇 초 걸렸는가

이런 숫자가 있어야 SLA, 장애 보고, 튜닝 기준을 만들 수 있습니다.

---

## 11. 실제 실험과 검증

### Q46. PostgreSQL failover는 실제로 어떻게 검증했나?
A.
로컬 `kind` 기반 `Kubernetes`에서 현재 primary pod를 강제로 삭제했습니다.

그 뒤 아래 두 가지로 확인했습니다.

- 각 노드에서 `pg_is_in_recovery()` 값을 조회해 누가 primary인지 확인
- `repmgr` 로그를 통해 승격 메시지 확인

실제로 기존 standby가 primary로 승격되는 것을 확인했습니다.

### Q47. Redis failover는 실제로 어떻게 검증했나?
A.
현재 Redis master pod를 삭제한 뒤, Sentinel에서 `SENTINEL get-master-addr-by-name mymaster` 를 조회했습니다.

또한 각 Redis 노드에서 `INFO replication` 의 `role` 값을 확인해 새 master와 replica를 확인했습니다.  
실제로 기존 replica가 새 master로 승격되는 것을 봤습니다.

### Q48. 복구 시간은 어떻게 측정했나?
A.
stopwatch 개념으로 측정했습니다.

- PostgreSQL: primary 삭제 시점부터 다른 노드가 `pg_is_in_recovery() = false` 가 되는 시점까지
- Redis: master 삭제 시점부터 Sentinel이 새 master를 가리키고, 해당 노드가 `role:master` 가 되는 시점까지
- rejoin time: 기존 노드가 다시 standby/replica 상태로 붙는 시점까지

즉 이벤트를 관측 가능한 상태 변화로 측정했습니다.

### Q49. 이번 실험 수치는?
A.
현재 로컬 실험 기준 수치는 아래와 같습니다.

- PostgreSQL failover time: 약 14.91초
- PostgreSQL old primary rejoin time: 약 2.48초
- Redis failover time: 약 24.13초
- Redis old master rejoin time: 약 2.81초

중요한 점은 이 수치가 로컬 실험 기준이라는 것입니다. 운영 환경에서는 네트워크, storage, resource pressure에 따라 달라질 수 있습니다.

---

## 12. AWS나 실무 환경으로 확장하면

### Q50. AWS로 가면 어떤 구조로 바꾸겠나?
A.
핵심 패턴은 유지하고, 운영형 컴포넌트로 바꾸는 방향이 자연스럽습니다.

- Redis ingress queue -> `SQS`
- PostgreSQL -> `RDS/Aurora`
- local Kubernetes -> `EKS`
- 일부 메트릭 -> `Prometheus/Grafana` 또는 `CloudWatch`

즉 현재 구조의 핵심은 유지하되, durability와 운영 부담이 큰 부분을 managed service로 넘깁니다.

### Q51. Redis 대신 SQS를 쓰면 장단점은?
A.
장점:

- durability가 더 강합니다.
- managed service라 운영 부담이 적습니다.
- 장애 중 요청 보존에 더 자연스럽습니다.

단점:

- 로컬에서 완전히 같은 방식으로 실험하기 어렵습니다.
- Redis처럼 상태 저장과 큐를 한 곳에서 빠르게 다루긴 어렵습니다.
- 지연 특성과 동작 방식이 달라집니다.

### Q52. 현재 구조의 가장 큰 한계는 무엇인가?
A.
가장 큰 한계는 ingress queue가 Redis 기반이라는 점입니다.

현재 구조는 DB 장애 중 요청 보존은 잘 설명하지만, broker 자체 durability까지 완전히 해결한 구조는 아닙니다. 그래서 실무로 가면 broker 선택과 persistence 전략이 더 중요해집니다.

### Q53. 다음 단계로 무엇을 더 하겠나?
A.
우선순위는 아래 순서로 보겠습니다.

1. DLQ와 retry 정책 명확화
2. 부하 테스트로 TPS, p95, p99 수치 확보
3. Grafana 대시보드에 failover/rejoin 시간 시각화
4. Redis Streams 또는 SQS 기반 구조로 확장
5. unread count, fan-out, ordering 문제 고도화

---

## 13. 기본 CS 질문

### Q54. 동기 처리와 비동기 처리의 차이는?
A.
동기 처리는 호출한 쪽이 결과가 끝날 때까지 기다리는 방식입니다.  
비동기 처리는 작업을 맡기고, 호출한 쪽은 먼저 다음 일을 할 수 있는 방식입니다.

현재 프로젝트에서는 API가 요청을 queue에 넣고 먼저 응답하므로, 최종 저장은 비동기 처리에 가깝습니다.

### Q55. backpressure는 무엇인가?
A.
유입 속도가 처리 속도보다 빠를 때 시스템이 무너지지 않도록 압력을 조절하는 개념입니다.

예를 들어 queue depth가 너무 빨리 증가하면:

- worker 수를 늘리거나
- 요청 속도를 제한하거나
- 일부 작업을 나중으로 미루거나
- drop policy / DLQ 정책을 둘 수 있습니다.

즉 backpressure는 시스템이 과부하에 무너지는 것을 막기 위한 제어 개념입니다.

### Q56. CAP 관점에서 이 시스템은 어떻게 설명할 수 있나?
A.
정확히 한 문장으로 단정하기는 어렵지만, 현재 구조는 장애 중에도 요청을 먼저 보존하려는 쪽에 가깝습니다. 즉 사용자 요청을 받아두는 availability를 높이려는 설계입니다.

다만 최종 저장 시점의 consistency는 PostgreSQL과 idempotency, unique key를 통해 확보하려고 합니다.  
즉 수신과 저장을 분리해서 availability와 consistency를 단계별로 나눠 다루는 구조라고 설명할 수 있습니다.

### Q57. retry와 idempotency를 왜 같이 말해야 하나?
A.
retry만 있고 idempotency가 없으면 같은 요청이 여러 번 저장될 수 있습니다.  
idempotency만 있고 retry가 없으면 장애 시 복구력이 약합니다.

즉 재시도 가능한 구조를 만들수록, 중복 방지 전략도 반드시 같이 가야 합니다.

### Q58. race condition이 생길 수 있는 지점은?
A.
대표적으로는 아래 지점입니다.

- 같은 요청이 거의 동시에 두 번 들어오는 경우
- worker가 중복 소비하는 경우
- failover 직후 여러 노드가 역할을 다시 판단하는 경우

현재는 unique constraint와 idempotency key로 일부 race를 줄이고 있지만, ordering이나 분산 처리까지 커지면 더 세밀한 설계가 필요합니다.

### Q59. replication과 backup은 어떻게 다른가?
A.
replication은 실시간 또는 준실시간으로 다른 노드에 복제하는 것입니다. 장애 대응과 읽기 분산 목적이 큽니다.

backup은 특정 시점의 데이터를 따로 보관하는 것입니다. 삭제나 논리 오류, 대규모 장애 후 복구에 중요합니다.

즉 replication은 가용성을 위한 도구이고, backup은 복구 가능성을 위한 도구라고 이해하면 좋습니다.

### Q60. failover와 recovery를 구분해서 말해야 하는 이유는?
A.
운영에서 의미하는 시간이 다르기 때문입니다.

- failover: 새 primary/master가 생기기까지 걸린 시간
- recovery/rejoin: 기존 장애 노드가 다시 cluster에 합류하기까지 걸린 시간

장애 보고나 튜닝에서는 이 둘을 같은 숫자로 보면 안 됩니다.  
이번 프로젝트도 그래서 failover time과 rejoin time을 따로 측정했습니다.

---

## 14. 압박 질문에 대비한 정리

### Q61. 지금 구조의 가장 큰 약점을 하나만 말해보라면?
A.
Redis를 ingress queue로 쓰는 현재 구조는 queue-first 개념을 설명하기에는 좋지만, broker 자체 durability를 강하게 보장하는 운영 구조라고 말하긴 어렵다는 점입니다. 그래서 운영 단계에서는 Redis Streams, Kafka, SQS 같은 선택지를 더 검토해야 합니다.

### Q62. 이 프로젝트에서 실제로 검증한 것과 아직 설계 수준인 것을 나누면?
A.
실제로 검증한 것은 queue-first 요청 처리, DB 장애 중 요청 보존, Prometheus 메트릭 노출, PostgreSQL failover, Redis failover 입니다.

아직 설계 수준인 것은 본격적인 부하 테스트, DLQ 정책, 대규모 fan-out 구조, AWS managed queue 전환 이후의 수치 검증입니다.

### Q63. 이 프로젝트를 한 문장으로 설명하면?
A.
메시지 요청을 queue-first 구조로 처리하고, DB 장애 중에도 요청을 보존하며, PostgreSQL과 Redis failover를 실제로 검증하고 복구 시간을 수치로 설명할 수 있게 만든 메시징 시스템 포트폴리오입니다.

---

## 15. 마지막 정리

이 프로젝트를 통해 면접에서 보여주고 싶은 지점은 아래와 같습니다.

- 요청 수신과 최종 저장을 분리해야 하는 이유를 이해하고 있다.
- 장애 중 요청 보존과 재처리 구조를 설명할 수 있다.
- idempotency와 unique key를 이용해 중복 문제를 줄이는 방법을 이해하고 있다.
- HA, quorum, failover, rejoin 같은 운영 개념을 실제 실험으로 검증했다.
- 장애를 단순 에러가 아니라 관측 가능한 이벤트로 본다.

이 문서는 면접 답변집이면서 동시에 CS 개념 정리 노트로도 사용할 수 있게 만든 것입니다.

---

## 추가 질문: 4) 마이그레이션과 5) DLQ

### Q64. 왜 Alembic 같은 마이그레이션 도구가 필요한가?
A.
앱 시작 시 SQL을 직접 실행하는 방식은 빠르지만, 팀 개발과 운영 단계에서
"언제 어떤 스키마가 적용됐는지" 추적이 어렵습니다.

Alembic을 쓰면:
- 스키마 변경 이력이 버전 파일로 남고,
- 롤백/재현이 가능해지며,
- 배포 시점에 DB 변경을 통제할 수 있습니다.

즉, 기능 구현을 넘어 운영 안정성을 확보하기 위한 장치입니다.

### Q65. DLQ(Dead Letter Queue)는 왜 필요한가?
A.
재시도만 있는 구조에서는 반복 실패 메시지가 정상 큐를 계속 오염시킬 수 있습니다.

DLQ를 두면:
- 반복 실패 메시지를 정상 처리 경로에서 격리하고,
- 운영자가 실패 원인 분석/수동 재처리를 할 수 있으며,
- DLQ 증가량 기반 알림으로 장애를 조기 감지할 수 있습니다.

즉, "실패를 없애는" 것이 아니라 "실패를 통제 가능한 상태로 바꾸는" 것이 DLQ의 핵심입니다.
