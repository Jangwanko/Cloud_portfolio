# Event Stream Systems Portfolio

ì´ ì ì¥ìë ì±íí ì´ë²¤í¸ íë¦ì ë¨ì CRUD ë¡ ëë´ì§ ìê³ , ì¥ì  ìí©ììë ìì²­ì ìµëí ë¹ ë¥´ê² ë°ìë¤ì´ê³  ì´íì ë³µêµ¬ ì²ë¦¬í  ì ìë `event stream processing system` ííë¡ êµ¬ì±í í¬í¸í´ë¦¬ì¤ìëë¤.

íµì¬ ëª©íë ìëì ê°ìµëë¤.
- `queue-backed async processing`
- `HA`
- `autoscaling`
- `observability`
- `backup / restore`
- `Ingress + TLS`
- `GitOps / Argo CD`

íì¬ ì ì¥ìë ë¡ì»¬ `kind` íê²½ìì ì ìëë¦¬ì¤ë¥¼ ì¬íí  ì ìëë¡ êµ¬ì±ëì´ ìì¼ë©°, ì´í AWS `EKS` ê°ì ì¸ë¶ í´ë¬ì¤í°ë¡ íì¥í  ì ìë ë°©í¥ë í¨ê» ë´ê³  ììµëë¤.

## Summary
- API ë ìì²­ì ë°ë¡ DB ì ì°ì§ ìê³  Redis ingress queue ì ì ì¬í©ëë¤.
- Worker ë queue ë¥¼ ìë¹íë©´ì PostgreSQL ì ë¹ëê¸° ììíí©ëë¤.
- ì¥ì  ìí©ììë retry, DLQ, replayer ë¡ ë³µêµ¬ ê²½ë¡ë¥¼ ì ê³µí©ëë¤.
- Kubernetes íê²½ììë PostgreSQL HA, Redis HA, HPA, Prometheus, Grafana ë¥¼ í¨ê» ê²ì¦í©ëë¤.
- GitOps ê²½ë¡ììë Argo CD ê° Git ì ìíë ìí(`desired state`)ë¥¼ ê¸°ì¤ì¼ë¡ ì íë¦¬ì¼ì´ì ë§¤ëíì¤í¸ë¥¼ ëê¸°íí©ëë¤.

## Architecture
```mermaid
flowchart LR
    Client[Client] --> Ingress[Ingress + TLS]
    Ingress --> API[FastAPI API]
    API -->|202 Accepted| Client
    Client -->|status / read / query| Ingress
    API --> Queue[Redis Ingress Queue]
    Queue --> Worker[Worker]
    Queue --> DLQ[Ingress DLQ]
    DLQ --> Replayer[DLQ Replayer]
    Replayer --> Queue
    API --> Pgpool[Pgpool]
    Worker --> Pgpool
    Pgpool --> DB[(PostgreSQL HA)]
    API --> Metrics[Prometheus Metrics]
    Worker --> Metrics
    Queue --> Metrics
    Metrics --> Prom[Prometheus]
    Prom --> Grafana[Grafana]
```

ì²ë¦¬ íë¦:
- API ë ìì²­ì `accepted` ìíë¡ ë°ê³  Redis queue ì ì ì¬í©ëë¤.
- Worker ë queue ì ì´ë²¤í¸ë¥¼ PostgreSQL ì ê¸°ë¡í©ëë¤.
- ì¤í¨í ìì²­ì DLQ ë¡ ì´ëíê³ , replayer ê° ë¤ì queue ë¡ ì¬ì£¼ìí©ëë¤.
- ì¬ì©ìë ì´í ìì²­ ìí, ì´ë²¤í¸ ëª©ë¡, unread count ë¥¼ API ë¡ ì¡°íí©ëë¤.
- Prometheus / Grafana ë¡ API latency, worker ì²ë¦¬ ìê°, queue depth, DB / Redis ìíë¥¼ ê´ì¸¡í©ëë¤.

## What This Project Covers
### Normal Path
- event request intake
- async persistence
- read receipt / unread count

### Failure Recovery
- DB down during intake, then persistence after recovery
- Redis complete outage detection
- Redis single-node failover recovery
- retry exhaustion to DLQ

### Operations
- health / readiness / metrics
- HPA autoscaling
- backup / restore
- ingress + local TLS
- GitOps / Argo CD sync

## Prerequisites
íì:
- Docker Desktop
- Windows PowerShell

도구 설치 방법:

**Windows (chocolatey)**
```powershell
choco install kind kubernetes-helm
```

**macOS (homebrew)**
```bash
brew install kind helm
```

**Linux**
```bash
# kind
curl -Lo kind https://kind.sigs.k8s.io/dl/latest/kind-linux-amd64
chmod +x kind && sudo mv kind /usr/local/bin/

# helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

ë¡ì»¬ìì ì¬ì©íë í¬í¸:
- `80` for ingress HTTP
- `443` for ingress HTTPS
- `9090` for Prometheus alert validation fallback

## Quick Start
ì ì²´ ë¡ì»¬ ê²ì¦ì ìë ëªë ¹ì¼ë¡ ì¤íí  ì ììµëë¤.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_all.ps1
```

ì´ ì¤í¬ë¦½í¸ë ìë ììì ìíí©ëë¤.
- kind cluster ìì±
- `ingress-nginx` ì¤ì¹
- `metrics-server` ì¤ì¹
- application image build and load
- PostgreSQL HA / Redis HA ë°°í¬
- application stack ë°°í¬
- ingress readiness íì¸
- smoke / DB recovery / Redis recovery / HPA scaling test ì¤í

ê¸°ë³¸ ì ê·¼ ê²½ë¡:
- API: `http://localhost`
- TLS API: `https://localhost`
- Grafana: `http://localhost/grafana`
- TLS Grafana: `https://localhost/grafana`
- Prometheus: `http://localhost/prometheus/`
- TLS Prometheus: `https://localhost/prometheus/`

## Verified Scenarios
- smoke
- DB recovery
- Redis complete outage
- Redis single-node failover
- DLQ flow
- failover alerts
- HPA scaling
- PostgreSQL backup / restore
- GitOps / Argo CD sync

ìì¸ ê²°ê³¼ë [TEST_RESULTS.md](docs/TEST_RESULTS.md) ì ì ë¦¬íìµëë¤.

## Observability
Grafana / Prometheus ìì ìë í­ëª©ì íì¸í  ì ììµëë¤.
- API request count / latency
- worker processed count / processing latency
- queue depth
- DB pool usage / reconnect / failure
- Redis reconnect state
- component health status
- alert firing / resolution

## Performance
`k6` ë¶í íì¤í¸ ìì²´ë ëìíì§ë§, íì¬ latency threshold ë ìì§ íµê³¼íì§ ëª»íê³  ììµëë¤.

ìµê·¼ ì¸¡ì  ìì:
- ì´ê¸° ê¸°ì¤: `5434 req`, avg `3660ms`, p95 `8175ms`
- 1ì°¨ ê°ì  í: `7966 req`, avg `2285ms`, p95 `4936ms`
- 2ì°¨ ê°ì  í: `9102 req`, avg `1934ms`, p95 `3851ms`
- pgpool / DB pool ì¡°ì  í: `11314 req`, avg `1519ms`, p95 `3333ms`

## Backup and Restore
íì¬ ì´ì ë³´ê° ë²ì:
- manual backup: `scripts/backup_postgres_k8s.ps1`
- manual restore: `scripts/restore_postgres_k8s.ps1`
- weekly PostgreSQL backup `CronJob`

ê´ë ¨ ì´ì ì§ì¹¨ì [OPERATIONS.md](docs/OPERATIONS.md) ì ì ë¦¬íìµëë¤.

## GitOps / Argo CD
íì¬ ì ì¥ììë Argo CD ê¸°ë° GitOps ê²½ë¡ê° ì¶ê°ëì´ ììµëë¤.

- GitOps sync path: `k8s/gitops/overlays/local-ha`
- Argo CD bootstrap ì¤í¬ë¦½í¸:
  - `k8s/scripts/install-argocd.ps1`
  - `k8s/scripts/bootstrap-argocd-app.ps1`
- ë¡ì»¬ GitOps quick start:
  - `powershell -ExecutionPolicy Bypass -File scripts/quick_start_gitops.ps1 -RepoUrl https://github.com/<your-account>/<your-repo>.git -Revision ops`

íì¬ ê²ì¦ ê¸°ì¤ì ìëì ê°ìµëë¤.
- ë¡ì»¬ `kind` í´ë¬ì¤í°ìì Argo CD ì¤ì¹
- `ops` ë¸ëì¹ë¥¼ ë°ë¼ë³´ë `Application` ìì±
- commit / push í Argo CD ê° ì revision ì ì½ê³  ë°°í¬ ë¦¬ìì¤ë¥¼ ê°±ì íë ê²ê¹ì§ íì¸

ì¦ ì´ íë¡ì í¸ë ë¬¸ììì¼ë¡ë§ GitOps ë¥¼ ì¤ëªíë ê²ì´ ìëë¼, ë¡ì»¬ Kubernetes íê²½ìì ì¤ì  sync ëìê¹ì§ ê²ì¦í ìíìëë¤.

## Branch Strategy
- `master`
  - ì¤ì  ë°°í¬ ê¸°ì¤ ë¸ëì¹ìëë¤.
  - ì´í EKS ì ì°ê²°í  ë ì´ì ë°°í¬ ê¸°ì¤ì ì¼ë¡ ì¬ì©í  ì ììµëë¤.
- `dev`
  - ê°ë° íµí©ì© ë¸ëì¹ìëë¤.
  - ê¸°ë¥ ê°ë°ì ëª¨ì¼ê³  ì ë¦¬íë ì©ëë¡ ì¬ì©í©ëë¤.
- `ops`
  - ë¡ì»¬ `kind` + Argo CD ê²ì¦ì© ë¸ëì¹ìëë¤.
  - GitOps íë¦ê³¼ ì´ì ì ì°¨ë¥¼ ì¤ííê³  íì¸íë ì©ëë¡ ì¬ì©í©ëë¤.

íì¬ ë¡ì»¬ GitOps ê²ì¦ì `ops` ë¸ëì¹ ê¸°ì¤ì¼ë¡ ìííìµëë¤.

## CI
íì¬ ì ì¥ììë ê¸°ë³¸ `GitHub Actions` CI êµ¬ì±ì ì¶ê°íìµëë¤.

- Python ë¬¸ë² ê²ì¦
- Docker image build íì¸
- Kustomize manifest render íì¸

ì´ ë¨ê³ë ìì§ EKS ì§ì  ë°°í¬ì ì°ê²°ëì´ ìì§ë ìì§ë§, ì½ë ë³ê²½ì´ ìµìí ë°°í¬ ê°ë¥í ííì¸ì§ ë¹ ë¥´ê² íì¸íë ì­í ì í©ëë¤.

## Current Limits
- HTTPS is local self-signed TLS, not production-issued certificates
- `k6` latency threshold is still failing
- stream ë¨ì event ordering guarantee ë ì¶ê° ê²ì¦ ê³¼ì ê° ë¨ì ììµëë¤
- ì´ì UI ë ë°ëª¨ì ê²ì¦ ëª©ì ì ë§ì¶° ë¸ì¶ ë²ìë¥¼ ì´ì´ë ìíì´ë©°, production access control ê¹ì§ë êµ¬ííì§ ìììµëë¤
- EKS / ECR / external secret manager ì°ëì ìì§ ë¡ì»¬ ì¤ì¬ ê²ì¦ ë¨ê³ìëë¤

## Documents
- ì¤í ê°ì´ë: [QUICK_START.md](docs/QUICK_START.md)
- êµ¬ì¡°ì ì²ë¦¬ íë¦: [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- ì´ì ì§ì¹¨: [OPERATIONS.md](docs/OPERATIONS.md)
- GitOps / Argo CD: [GITOPS.md](docs/GITOPS.md)
- ê²ì¦ ê²°ê³¼: [TEST_RESULTS.md](docs/TEST_RESULTS.md)
- ë³ê²½ ì´ë ¥: [PATCH_NOTES.md](docs/PATCH_NOTES.md)
- ì ì¥ì êµ¬ì¡°: [REPOSITORY_STRUCTURE.md](docs/REPOSITORY_STRUCTURE.md)

## Suggested Reading Order
1. README ìì ì ì²´ êµ¬ì¡°ì íì¬ ìí íì
2. [QUICK_START.md](docs/QUICK_START.md) ë¡ ì¤í ë°©ë² íì¸
3. [ARCHITECTURE.md](docs/ARCHITECTURE.md) ë¡ êµ¬ì±ê³¼ ì²ë¦¬ íë¦ íì¸
4. [GITOPS.md](docs/GITOPS.md) ë¡ GitOps / Argo CD êµ¬ì± íì¸
5. [TEST_RESULTS.md](docs/TEST_RESULTS.md) ë¡ ì¤ì  ê²ì¦ ìí íì¸
