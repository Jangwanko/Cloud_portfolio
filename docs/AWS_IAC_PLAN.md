# AWS IaC 계획

로컬 `kind + Kubernetes` 검증 환경과 AWS `production-like` 환경의 대응 관계를 정리한 문서입니다.
목표는 단순히 "AWS에 올린다"가 아니라, 프로젝트의 핵심인 `async intake`, `failure recovery`, `autoscaling`, `observability`, `backup / restore`를 AWS에서도 자연스럽게 이어 가는 것입니다.

## 목표
- 로컬 검증 구조를 AWS에서도 비슷한 책임 분리로 유지
- 수동 설정보다 `IaC`로 재현 가능한 인프라 구성
- 애플리케이션은 Kubernetes 중심을 유지하고, 데이터 계층은 AWS managed service로 단순화
- 포트폴리오 기준으로도 "실제 배포까지 고려했다"는 점이 보이도록 설계

## 선택 기준
이 문서는 `Terraform + EKS` 기준으로 작성합니다.

이 조합을 고른 이유:
- 현재 프로젝트가 이미 Kubernetes 중심 구조입니다
- 로컬 검증 환경과 운영 환경의 구조 차이를 줄일 수 있습니다
- AWS managed service를 붙이기 쉽습니다
- 포트폴리오 관점에서 인프라 설계 의도를 설명하기 좋습니다

## 권장 AWS 구성

### 컴퓨팅
- `Amazon EKS`
  - API / Worker / DLQ Replayer 실행
- API CPU HPA, Worker Kafka lag autoscaling, Ingress, metrics 구조 유지
- `Managed Node Group`
  - 초기에는 일반 노드 그룹 하나로 시작
  - 이후 API / worker 용도 분리 가능

### Ingress / 네트워킹
- `VPC`
  - public subnet: ALB
  - private subnet: EKS node, RDS, MSK connectivity
- `AWS Load Balancer Controller`
  - Kubernetes `Ingress`를 ALB로 연결
- `Application Load Balancer`
  - `/` API
  - `/grafana` Grafana
  - `/prometheus` Prometheus
- `Route 53`
  - 도메인 연결
- `ACM`
  - TLS certificate 발급 및 ALB HTTPS 종료

### Container registry
- `Amazon ECR`
  - API 이미지 저장
  - Worker / DLQ Replayer도 동일 이미지 또는 분리 이미지 사용 가능

### 데이터베이스
- `Amazon RDS for PostgreSQL` 또는 `Aurora PostgreSQL`
  - 로컬 PostgreSQL HA + pgpool 역할을 AWS에서는 managed DB로 단순화
  - 포트폴리오 기준 첫 버전은 `RDS PostgreSQL Multi-AZ`가 설명하기 쉽습니다
  - 더 높은 확장성과 읽기 분리가 필요하면 `Aurora PostgreSQL`로 확장 가능

### Event log
- `Amazon MSK`
  - Kafka ingress topic / DLQ topic 역할 유지
  - Multi-AZ broker replication 사용
  - 로컬 3-broker KRaft runtime을 운영형 managed Kafka runtime으로 대체

### Secret
- `AWS Secrets Manager`
  - DB credential
  - Kafka credential 또는 SASL/SCRAM secret
  - JWT secret
  - Grafana admin credential
- EKS에서는 `External Secrets Operator` 또는 CSI driver로 주입 가능

### 관측성
- 1차안:
  - Prometheus / Grafana를 EKS 안에 유지
  - dashboard와 alert 흐름을 유지
- 2차안:
  - `Amazon Managed Service for Prometheus`
  - `Amazon Managed Grafana`
  - 운영성을 더 높이고 관리 포인트를 줄임

포트폴리오 기준으로는 1차안이 현재 구조를 설명하기 쉽고, 문서에는 2차 확장 방향만 같이 남기는 방식이 자연스럽습니다.

### 백업 / 복구
- `RDS automated backups`
- 수동 snapshot
- 필요 시 point-in-time recovery
- Kafka topic 내구성은 replication factor, `min.insync.replicas`, producer `acks` 정책으로 관리

AWS에서는 managed backup 전략을 기본값으로 둡니다.

## 로컬 구성과 AWS 매핑

| 현재 구성 | AWS 대응 |
|---|---|
| `kind` cluster | `Amazon EKS` |
| `ingress-nginx` | `AWS Load Balancer Controller + ALB` |
| local self-signed TLS | `ACM + Route 53 + HTTPS ALB` |
| PostgreSQL HA + pgpool | `RDS PostgreSQL Multi-AZ` 또는 `Aurora PostgreSQL` |
| Kafka 3-broker local KRaft runtime | `Amazon MSK` |
| runtime secret | `AWS Secrets Manager` |
| local image build/load | `ECR push + EKS deploy` |
| in-cluster Prometheus/Grafana | EKS 유지 또는 `AMP / AMG` |
| CronJob weekly backup | RDS automated backup + snapshot |

## 추천 배포 단계

### 1단계: 가장 현실적인 AWS 첫 버전
- ECR
- EKS
- ALB Ingress
- RDS PostgreSQL Multi-AZ
- Amazon MSK
- Secrets Manager
- Prometheus / Grafana는 EKS 안에서 유지

이 단계만으로도 충분히 "실배포 가능한 구조"로 설명할 수 있습니다.

### 2단계: 운영형 고도화
- Route 53 도메인 연결
- ACM 정식 인증서 적용
- managed Prometheus / Grafana 검토
- WAF, private access, tighter secret rotation
- GitHub Actions 기반 CI와 연동

## Terraform 기준 디렉터리 제안
이 저장소의 Terraform 코드는 아래 구조를 기준으로 정리합니다.

```text
infra/
└─ terraform/
   ├─ envs/
   │  ├─ dev/
   │  └─ prod/
   ├─ modules/
   │  ├─ vpc/
   │  ├─ eks/
   │  ├─ ecr/
   │  ├─ msk_kafka/
   │  ├─ rds_postgres/
   │  ├─ route53_acm/
   │  └─ secrets/
   └─ README.md
```

## Terraform 모듈 책임

### `modules/vpc`
- VPC
- public/private subnet
- NAT gateway
- security group 기본 구조

### `modules/eks`
- EKS cluster
- managed node group
- IAM OIDC provider
- cluster addon 기본 설치 전제

### `modules/ecr`
- application image repository
- lifecycle policy

### `modules/msk_kafka`
- Amazon MSK cluster
- broker security group
- EKS node security group에서 Kafka broker 접근 허용
- bootstrap broker endpoint output

### `modules/rds_postgres`
- Multi-AZ PostgreSQL
- subnet group
- parameter group
- backup retention

### `modules/route53_acm`
- hosted zone lookup
- ACM certificate
- validation record

### `modules/secrets`
- JWT / Grafana / app secret
- DB / Kafka credential 참조 구조

## Kubernetes 쪽에서 바뀌는 점

AWS로 옮겨도 앱의 핵심 책임은 유지됩니다.
- API / Worker / DLQ Replayer deployment
- readiness / metrics
- API HPA
- Worker Kafka consumer lag autoscaling
- Ingress 기반 노출

대신 아래는 바뀝니다.
- ALB Ingress 중심 접근 사용
- DB endpoint는 RDS endpoint 사용
- Kafka bootstrap endpoint는 MSK bootstrap endpoint 사용
- 로컬 backup CronJob 역할은 managed backup 전략으로 대체
- TLS는 self-signed 대신 ACM 사용

## 보안 기준
- EKS node는 private subnet 배치
- RDS / Kafka brokers는 public access 비활성화
- ALB만 public subnet 노출
- secret은 Kubernetes manifest에 직접 쓰지 않고 Secrets Manager에서 주입
- Grafana / Prometheus는 ALB path로 노출하더라도 실제 운영에서는 접근 제한 필요

## 이 프로젝트에서 가장 중요한 설명 포인트
AWS로 옮길 때 핵심은 서비스를 많이 붙이는 것이 아니라, 로컬에서 검증한 구조를 운영형 책임으로 정리하는 것입니다.

즉 포인트는:
- `kind`에서 검증한 Kubernetes 구조를 `EKS`로 확장
- DB/Kafka를 managed service 기반 runtime으로 단순화
- Ingress / TLS / secret / backup을 AWS 방식으로 치환
- 애플리케이션 레벨의 `async intake`, `retry`, `DLQ`, `replayer`, `API HPA`, `Worker Kafka lag autoscaling`, `metrics`는 그대로 유지

## 권장 최종 문장
면접이나 문서에서 AWS IaC 방향을 설명할 때는 아래 식이 가장 자연스럽습니다.

> 로컬에서는 `kind` 기반으로 장애 복구와 운영 시나리오를 검증했고, 실제 배포 단계에서는 `Terraform + EKS + RDS + MSK + ALB + ACM + Secrets Manager` 조합으로 옮겨갈 수 있도록 구조를 설계했습니다.

## 구현 우선순위
실제로 IaC를 추가한다면 이 순서가 좋습니다.

1. `infra/terraform` 기본 구조 생성
2. `VPC + EKS + ECR`
3. `RDS PostgreSQL + MSK`
4. `AWS Load Balancer Controller + Ingress`
5. `Secrets Manager` 연동
6. observability / CI 연결

## Terraform 구조
저장소에는 아래 Terraform 코드가 포함되어 있습니다.
- `infra/terraform/envs/dev`
- `infra/terraform/modules/vpc`
- `infra/terraform/modules/eks`
- `infra/terraform/modules/ecr`
- `infra/terraform/modules/msk_kafka`
- `infra/terraform/modules/rds_postgres`
- `infra/terraform/modules/secrets`
- `infra/terraform/modules/route53_acm`

즉 이 문서는 단순 아이디어 메모가 아니라, 실제 IaC 골격이 들어간 뒤 그 기준과 의도를 설명하는 문서입니다.
