# GitOps and Argo CD

## Deployment Contract

master GitOps 경로:

```text
push master
  -> CI compile / test / image build / kustomize render
  -> GHCR image build and push with 12-character commit SHA tag
  -> Actions bot updates local-ha overlay newTag
  -> bot commit with [skip image publish]
  -> Argo CD observes master
  -> sync / self-heal
```

Argo CD는 repository의 Python/HTML/static source를 build하지 않습니다. 새 source 반영에는 registry image와 manifest tag 변경이 모두 필요합니다.

## GitOps Sources

- `k8s/gitops/base`
  - application, monitoring, KEDA, Kafka runtime manifests
  - 일반 Sync schema migration Job
- `k8s/gitops/overlays/local-ha`
  - master deployment image replacement
- `k8s/argocd/project-messaging-portfolio.yaml`
  - AppProject scope
- `k8s/argocd/application-messaging-portfolio-local-ha.example.yaml`
  - `targetRevision: master`, local-ha overlay
- `k8s/scripts/install-argocd.ps1`
  - pinned Argo CD install manifest
- `k8s/scripts/bootstrap-argocd-app.ps1`
  - repository/revision/path application bootstrap
- `.github/workflows/ci.yml`
  - validation and master image publish/tag update

## CI Validation

`validate` job:

- exact revision checkout
- Python 3.11 dependency install
- compile check
- pytest
- Docker image build
- `kubectl kustomize k8s/gitops/overlays/local-ha`
- rendered output에서 GHCR image 확인

이 job은 cluster deploy를 수행하지 않습니다.

## Master Image Publication

`publish-master-image` 조건:

- `push` event
- `refs/heads/master`
- validation success
- commit message에 `[skip image publish]` 없음

publish 결과:

- candidate: `ghcr.io/jangwanko/cloud_portfolio:candidate-<12-char-sha>`로 먼저 build/push
- verification: registry가 반환한 digest를 직접 pull해 container UID `10001` 확인
- promotion: 검증한 동일 digest에 `<12-char-sha>`와 `master-bootstrap` tag 부여
- release image: `ghcr.io/jangwanko/cloud_portfolio:<12-char-sha>`
- provenance: BuildKit provenance
- SBOM: enabled
- overlay: `newTag`를 같은 SHA로 변경
- bot commit: `ci: deploy master image <sha> [skip image publish]`

validate job의 선행 build와 publish job의 registry candidate build는 서로 다른 job입니다. 배포 tag는 publish job이 만든 candidate digest 자체를 비루트 실행 검증한 뒤에만 생성하므로, 재빌드된 미검증 artifact를 승격하지 않습니다. Docker base image도 tag와 digest를 함께 고정합니다.

race guard:

- workflow 실행 중 master가 앞서가면 이전 run의 tag commit 생략
- 새 master run이 자체 image/tag를 처리

운영 전 확인:

- repository branch protection이 Actions bot push를 허용하는지
- GHCR package visibility
- private GHCR이면 namespace의 `imagePullSecret`
- bot commit 뒤 CI 재실행 정책과 required check 상태

## Dev Kafka Image Publication

`master`는 최종 GitOps 기준이고, 실제 개발·검증 클러스터는 Application의 `targetRevision`을 `dev-kafka`로 지정합니다.

```text
push dev-kafka
  -> CI validation (independent workflow)
  -> .github/workflows/dev-kafka-image.yml (independent workflow)
  -> GHCR image build and push with 7-character commit SHA tag
  -> Actions bot updates local-ha overlay newTag
  -> bot commit with [skip dev-kafka image]
  -> Argo CD observes dev-kafka and syncs
```

현재 validation과 image publication은 별도 workflow로 실행됩니다. `dev-kafka` source push 직후의 구 image tag 상태를 배포 완료로 보지 않습니다. CI validation 성공, GHCR image 발행, overlay tag bot commit을 모두 확인한 뒤 Argo CD revision, workload image, rollout 상태를 확인합니다. Generic v2도 아래의 migration, Worker, API sync wave 순서를 그대로 사용합니다.

English: The development cluster tracks `dev-kafka`. A branch push builds `ghcr.io/jangwanko/cloud_portfolio:<commit-sha>`, updates the local-ha image tag in a bot commit, and then allows Argo CD to sync the staged release.

## Argo CD Sync

Application 기본값:

- revision: `master`
- path: `k8s/gitops/overlays/local-ha`
- namespace: `messaging-app`
- automated prune / self-heal
- `RespectIgnoreDifferences=true`

`messaging-app` Namespace는 `k8s/gitops/base/namespace.yaml`에 계속 유지하며 `Prune=false`를 적용합니다. 자동 prune이 켜진 Application에서 기존에 추적하던 Namespace를 desired state에서 제거하면 namespace-scoped bootstrap 리소스와 PVC까지 함께 삭제될 수 있기 때문입니다. Namespace lifecycle은 application rollout과 분리하고, 이름 변경이나 제거는 별도 migration 절차로 수행합니다.

Replica drift:

- API HPA와 Worker KEDA가 `/spec/replicas` 변경
- Argo CD ignore rule로 autoscaler와 Git desired state 충돌 방지

Historical `Synced / Healthy` 기록은 특정 cluster 시점의 snapshot입니다. 현재 상태는 매번 다시 조회합니다.

## Generic v2 Staged Release

Generic v2는 sync wave로 schema, consumer, API 공개 순서를 강제합니다. 구 Worker는 v2 job의 compatibility body preview만 저장하고 JSON `payload`/`metadata`를 보존하지 못하기 때문입니다.

GitOps 순서:

1. gate `false`인 `messaging-env` Secret, wave `-3`
2. `messaging-schema-migration` 일반 Sync Job, wave `-2`: 새 image로 Alembic head 적용
3. Worker Deployment, wave `-1`: dual-read/dual-write consumer rollout
4. API Deployment, wave `0`: `local-ha` overlay의 container-level `GENERIC_EVENTS_V2_ENABLED=true`로 v2 공개
5. v2 POST `202`, v2 status/event-list GET, PostgreSQL `payload`/`metadata` canary 확인

Migration Job은 `k8s/gitops/base/migration-job.yaml`의 일반 Sync resource이며 `Force=true,Replace=true` sync option을 사용합니다. `messaging-env`와 PostgreSQL password만 참조하고 `messaging-runtime-secrets`에 의존하지 않습니다. Job 실패 또는 이전 wave unhealthy 상태에서는 다음 wave를 성공 rollout으로 간주하지 않습니다. Base/app Secret의 gate 값은 계속 `false`이고, API wave에 도달한 `local-ha` overlay만 명시적으로 `true`를 주입합니다. 수동 local 경로는 app manifest gate `false`와 quick-start Worker-first enable 순서를 사용합니다.

## Bootstrap Boundary

Cluster bootstrap:

- kind cluster
- ingress / metrics-server / TLS
- PostgreSQL/Kafka prerequisites
- Argo CD controllers
- AppProject / Application

Bootstrap order:

1. PostgreSQL Helm install/upgrade
2. Kafka와 나머지 prerequisites 준비
3. Argo CD controller 설치
4. AppProject / Application 등록

PostgreSQL chart의 첫 install은 `messaging-postgresql-ha-postgresql` Secret에 credential을 생성합니다. 이후 upgrade는 Bitnami chart lookup으로 기존 Secret을 재사용합니다. application workload의 `DB_PASSWORD`는 이 Secret의 `password` key를 참조합니다. PVC를 유지한 채 Secret만 삭제하면 저장된 DB credential과 새 값이 어긋날 수 있으므로, 자동 재생성에 맡기지 않고 기존 credential을 복구해 Secret을 먼저 복원합니다.

GitOps-managed desired state:

- application workloads
- monitoring and alerts
- KEDA resources
- registry image tag

bootstrap component 자체와 storage/operator prerequisites를 GitOps application 밖에서 설치할 수 있습니다. 이 경계를 application sync 완료와 구분합니다.

## Run

전제:

1. 변경이 remote master에 존재
2. master publish workflow 성공
3. overlay tag bot commit remote 반영
4. GHCR image를 cluster가 pull 가능

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_gitops.ps1 `
  -RepoUrl https://github.com/<your-account>/<your-repo>.git `
  -Revision master
```

Script image boundary:

- 기본 실행: remote revision의 committed `local-ha/kustomization.yaml`에서 `newName`/`newTag` 확인
- resolved image를 실행 전에 `docker manifest inspect`로 접근 가능 여부 확인
- local build / kind image load 제외
- 기본 Argo Application: committed overlay를 그대로 추적해 CI bot의 이후 SHA tag commit 반영
- private repository 또는 의도적 고정 배포: `-ImageRepository`와 `-ImageTag`를 함께 전달해 Application override 적용
- registry 없는 local-only 실행은 `quick_start_all.ps1` 사용
- private GHCR: 사전 `docker login ghcr.io`와 cluster `imagePullSecret` 모두 필요

## Status

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_portfolio_status.ps1
```

확인 항목:

- Application sync / health
- rendered/current image tag
- workload readiness
- API readiness
- Kafka exporter lag
- KEDA status
- backup PVC/CronJob

Image 확인 예시:

```powershell
kubectl -n messaging-app get deployment api worker notification-worker dlq-replayer `
  -o jsonpath="{range .items[*]}{.metadata.name}{'\t'}{.spec.template.spec.containers[0].image}{'\n'}{end}"
```

## Manual Local Path

```text
docker build messaging-portfolio:local
  -> kind load
  -> manual manifests
  -> rollout restart
```

이 경로는 disposable local cluster용입니다. GitOps 자동 배포 증거로 설명하지 않습니다.

## Failure Modes

| Symptom | Likely cause | Check |
| --- | --- | --- |
| `ImagePullBackOff` | tag 없음, private package, pull secret 없음 | overlay tag, GHCR package, pod events |
| source changed, pod unchanged | image workflow/tag commit 미완료 | Actions jobs, overlay commit, Argo revision |
| Argo `OutOfSync` replicas only | autoscaler drift ignore 누락 | Application ignoreDifferences |
| push rejected after bot commit | remote master ahead | fetch/rebase after user approval |
| registry image preflight 실패 | tag 없음, package private, Docker auth 없음 | master workflow, `docker login ghcr.io`, manifest inspect |
| `master-bootstrap` pull failure | initial publish 미완료 또는 cluster pull 권한 없음 | master workflow, package visibility, imagePullSecret |
| 예상 SHA와 다른 image | bootstrap alias 또는 Application override 사용 | Argo Application source kustomize image, deployment image |

## Verification Criteria

- `kubectl kustomize`의 app workloads 모두 GHCR SHA tag
- image digest와 expected commit 연결
- Argo CD `Synced / Healthy`
- API/Worker rollout success
- `202 Accepted` smoke flow와 DB persistence 확인
- source-only change가 새 image/tag commit 뒤 반영됨을 확인
