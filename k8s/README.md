# Kubernetes HA Design (Local Practice)

ì´ í´ëë DB ìë failover ì quorum êµ¬ì¡°ë¥¼ ë¡ì»¬ìì ì¤ìµíê¸° ìí ì¤ì ìëë¤.

## ëª©í
- PostgreSQL: `primary 1 + replicas 2` ê¸°ë° ìë failover
- Redis: `master 1 + replicas 2` + Sentinel quorum ê¸°ë° ìë failover
- App ì PostgreSQL / Redis ë¥¼ ëìì ì¬ì©

## êµ¬ì±
- PostgreSQL HA: `bitnami/postgresql-ha` chart + `bitnamilegacy/*` runtime images
  - total postgres nodes: 3
  - topology: primary 1 + replicas 2
  - pgpool enabled
  - `synchronousCommit` + `numSynchronousReplicas: 1`
  - 3ë¸ë ì¤ ê³¼ë° ìì¡´ì ê¸°ì¤ì¼ë¡ ì primary ì¹ê²© íë¨
- Redis HA: `bitnami/redis`
  - master 1 + replicas 2
  - sentinel 3
  - quorum 2

## 1) kind í´ë¬ì¤í° ìì±
```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/setup-kind.ps1
```

## 2) HA ì¤í ì¤ì¹
```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/install-ha.ps1 -Namespace messaging-app
```

## 3) ì± ì°ê²° í¬ì¸í¸
- PostgreSQL endpoint: `messaging-postgresql-ha-pgpool.messaging-app.svc.cluster.local:5432`
- Redis endpoint (sentinel):
  - `messaging-redis-node-0.messaging-redis-headless.messaging-app.svc.cluster.local:26379`
  - `messaging-redis-node-1.messaging-redis-headless.messaging-app.svc.cluster.local:26379`
  - `messaging-redis-node-2.messaging-redis-headless.messaging-app.svc.cluster.local:26379`

ì± ë°°í¬:
```powershell
kubectl apply -f k8s/app/manifests-ha.yaml
```

## 4) íì¼ì¤ë² íì¤í¸
- PostgreSQL primary pod ê°ì  ì­ì  -> quorum ì¶©ì¡± replica ê° ì primary ë¡ ì¹ê²©
- Redis master pod ê°ì  ì­ì  -> sentinel quorum ì¼ë¡ replica ì¹ê²©

## 5) ê´ì¸¡ ì¤í
Prometheus + Grafana ë¡ ìë í­ëª©ì ê´ì¸¡í©ëë¤.

- API: request rate, latency p50/p95/p99, error rate, readiness ì¤í¨ íì
- PostgreSQL: up/down, active connections, replication lag, transaction rate, failover event
- Redis: memory usage, queue length, ops/sec, connected clients, reconnect event
- Worker: event processed count, success/failure rate, processing latency, retry count, queue lag
- Kubernetes: pod restart count, CPU/memory, node disk usage, network I/O

## GitOps / Argo CD
ì´ ì ì¥ìë ê¸°ì¡´ `kubectl apply -f k8s/app/manifests-ha.yaml` ê²½ë¡ ì¸ì Argo CD ë¡ ê´ë¦¬í  ì ìë GitOps ê²½ë¡ë í¬í¨í©ëë¤.

- GitOps sync path: `k8s/gitops/overlays/local-ha`
- Argo CD project manifest: `k8s/argocd/project-messaging-portfolio.yaml`

Argo CD ì¤ì¹:

```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/install-argocd.ps1
```

Argo CD application bootstrap:

```powershell
powershell -ExecutionPolicy Bypass -File k8s/scripts/bootstrap-argocd-app.ps1 `
  -RepoUrl https://github.com/<your-account>/<your-repo>.git `
  -Revision ops
```

ë¶í¸ì¤í¸ë© ë¨ê³ììë ì¬ì í cluster, ingress, metrics-server, TLS, HA data store ì¤ì¹ë¥¼ ë¨¼ì  í´ì¼ í©ëë¤.
ê·¸ ì´í ì± ë§¤ëíì¤í¸ ë°ìì Argo CDê° Git ìíë ìí(`desired state`) ê¸°ì¤ì¼ë¡ ëê¸°íí©ëë¤.

ë¡ì»¬ ê²ì¦ ê¸°ì¤ ë¸ëì¹ë íì¬ `ops` ë¡ ëê³  ìì¼ë©°, ì´í ì¤ì  ì´ì ë°°í¬ ê¸°ì¤ ë¸ëì¹ë `master` ì¼ë¡ ì°ê²°í  ì ììµëë¤.

## ì°¸ê³ 
- ê¸°ë³¸ ì¤í êµ¬ì±ì ë¨ì¼ DB / Redis ìëë¤
- HA ì¤ìµì quorum ê¸°ë° íì¥ ìëë¦¬ì¤ ê²ì¦ì ì´ì ì ë¡ëë¤
