# GitOps / Argo CD

ì´ ë¬¸ìë ì´ ì ì¥ìì ì¶ê°í `GitOps` / `Argo CD` ê²½ë¡ë¥¼ ì ë¦¬í ë¬¸ììëë¤.
íì¬ íë¡ì í¸ë ê¸°ì¡´ `kubectl apply` ê¸°ë° ì¤í ê²½ë¡ë¥¼ ì ì§íë©´ìë, ë©´ì ìì `GitOps` ìêµ¬ì¬í­ì ì¤ëªí  ì ìëë¡ ë³ë Git ê¸°ë° ëê¸°í ê²½ë¡ë¥¼ í¨ê» ì ê³µí©ëë¤.

## ëª©ì 
- ë¡ì»¬ `kind` íê²½ììë GitOps íë¦ì ì¬í
- Argo CD ë¥¼ íµí´ Git ì ìíë ìí(`desired state`)ë¥¼ í´ë¬ì¤í°ì ë°ì
- ì´í AWS `EKS` ê°ì ì¸ë¶ í´ë¬ì¤í°ë¡ íì¥ ê°ë¥í êµ¬ì¡°ë¥¼ ì¤ëª

## ì¶ê°í êµ¬ì± ìì
- `k8s/gitops/base`
  - ê¸°ì¡´ HA ì íë¦¬ì¼ì´ì ë§¤ëíì¤í¸ë¥¼ GitOps ì§ìì ì¼ë¡ ë¬¶ë `Kustomize base`
- `k8s/gitops/overlays/local-ha`
  - ë¡ì»¬ `kind` HA íê²½ìì Argo CDê° ë°ë¼ë³´ë sync path
- `k8s/argocd/project-messaging-portfolio.yaml`
  - Argo CD `AppProject`
- `k8s/argocd/application-messaging-portfolio-local-ha.example.yaml`
  - ìì `Application` ë§¤ëíì¤í¸
- `k8s/scripts/install-argocd.ps1`
  - í´ë¬ì¤í°ì Argo CDë¥¼ ì¤ì¹íë ì¤í¬ë¦½í¸
- `k8s/scripts/bootstrap-argocd-app.ps1`
  - Git repository URLê³¼ revisionì ë°ì `Application` ì ìì±íë ì¤í¬ë¦½í¸
- `scripts/quick_start_gitops.ps1`
  - ë¡ì»¬ GitOps íë¦ì í ë²ì ì¤ííë quick start ì¤í¬ë¦½í¸

## Bootstrap ê³¼ GitOps ë¥¼ ë¶ë¦¬í ì´ì 
`GitOps`ë¼ê³  í´ì ì²ìë¶í° ëê¹ì§ ì ë¶ Argo CDê° ëì íë ê²ì ìëëë¤.
ì´ê¸°ìë ì¬ì í ì¬ëì´ cluster ì controller ë¥¼ ì¤ë¹íë `bootstrap` ë¨ê³ê° íìí©ëë¤.

íì¬ ì ì¥ì ê¸°ì¤ ì­í  ë¶ë¦¬ë ìëì ê°ìµëë¤.

- bootstrap
  - cluster ìì±
  - ingress / metrics-server / TLS ì¤ì¹
  - HA PostgreSQL / HA Redis ì¤ì¹
  - Argo CD ì¤ì¹
- GitOps-managed runtime
  - Argo CDê° `k8s/gitops/overlays/local-ha` ë¥¼ ëê¸°í
  - ì± ë§¤ëíì¤í¸ ë³ê²½ì ì§ì  `kubectl apply` íì§ ìê³  Git ìíë ìí(`desired state`) ê¸°ì¤ì¼ë¡ ë°ì

ì¦ ì´ íë¡ì í¸ë
`ì´ê¸° 1í bootstrap ì ìë`,
`ê·¸ ì´í ì íë¦¬ì¼ì´ì ë°ìì GitOps`
ë¼ë êµ¬ì¡°ë¥¼ ë³´ì¬ì£¼ëë¡ ì¤ê³íìµëë¤.

## ë¸ëì¹ ì ëµ
- `master`
  - ì¤ì  ë°°í¬ ê¸°ì¤ ë¸ëì¹
- `dev`
  - ê°ë° íµí©ì© ë¸ëì¹
- `ops`
  - ë¡ì»¬ `kind` + Argo CD ê²ì¦ì© ë¸ëì¹

íì¬ GitOps ì¤ê²ì¦ì `ops` ë¸ëì¹ ê¸°ì¤ì¼ë¡ ìííìµëë¤.

## ë¡ì»¬ ì¤í ë°©ë²
1. ì´ ì ì¥ìë¥¼ í´ë¬ì¤í°ìì ì ê·¼ ê°ë¥í Git remote ì push í©ëë¤.
2. ìë ëªë ¹ì¼ë¡ GitOps quick start ë¥¼ ì¤íí©ëë¤.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_gitops.ps1 `
  -RepoUrl https://github.com/<your-account>/<your-repo>.git `
  -Revision ops
```

3. ì¤í¬ë¦½í¸ë ìë ììì ììëë¡ ìíí©ëë¤.
- local cluster bootstrap
- HA PostgreSQL / Redis ì¤ì¹
- Argo CD ì¤ì¹
- `messaging-portfolio-local-ha` Application ìì±
- readiness íì¸
- smoke test ì¤í

## íì¸í ëì
íì¬ ì ì¥ìììë ìë íë¦ì ì¤ì ë¡ íì¸íìµëë¤.

- `ops` ë¸ëì¹ë¥¼ ìê²© `origin/ops` ë¡ push
- Argo CD `Application` ì `ops` ë¸ëì¹ì ì°ê²°
- ì´ê¸° sync ë¡ ì íë¦¬ì¼ì´ì ì¤í ìì±
- ìì ë§¤ëíì¤í¸ ë³ê²½ commit / push
- Argo CDê° ì revision ì ì½ê³  deployment ìíë¥¼ ê°±ì 
- ê²ì¦ í ìë³µ commit / push
- í´ë¬ì¤í° ìíë ë¤ì ìë ê°ì¼ë¡ ë³µê·

ì¦ ì´ íë¡ì í¸ë ë¬¸ììì¼ë¡ë§ GitOps ë¥¼ ì¤ëªíë ê²ì´ ìëë¼, ë¡ì»¬ Kubernetes íê²½ìì ì¤ì  sync ëìê¹ì§ ê²ì¦í ìíìëë¤.

## GitHub Actions ìì ê´ê³
íì¬ ì ì¥ììë ê¸°ë³¸ `GitHub Actions` CI êµ¬ì±ì ì¶ê°íìµëë¤.

- Python ë¬¸ë² ê²ì¦
- Docker image build íì¸
- Kustomize manifest render íì¸

ì´ ë¨ê³ë ìì§ EKS ì§ì  ë°°í¬ì ì°ê²°ëì´ ìì§ë ìì§ë§, ì½ë ë³ê²½ì´ ìµìí ë°°í¬ ê°ë¥í ííì¸ì§ ë¹ ë¥´ê² íì¸íë ì­í ì í©ëë¤.

ì´í EKS ê¹ì§ íì¥í  ëë ë³´íµ ìë ë¨ê³ê° ì´ì´ì§ëë¤.
- image registry / ECR push
- ì´ë¯¸ì§ íê·¸ ê°±ì 
- Argo CD ìë ëê¸°í

## íì¬ íê³ì ë¤ì ë¨ê³
- íì¬ ì± ì´ë¯¸ì§ë `messaging-portfolio:local` ì´ë¯ë¡, ë¡ì»¬ ë°ëª¨ììë image build í kind ì load íë ë¨ê³ê° ë¨¼ì  íìí©ëë¤.
- Argo CDë `deployment controller` ì´ì§ íì¤í¸ íë ììí¬ë ìëëë¤.
  - lint / test / image build ê²ì¦ì `CI` ìì ë¶ë¦¬íë ê²ì´ ë§ìµëë¤.
- AWS ë ë¤ë¥¸ cloud íê²½ì¼ë¡ íì¥íë ¤ë©´ ì´í ë¨ê³ìì ìë í­ëª©ì´ ì¶ê°ë¡ íìí  ì ììµëë¤.
  - image registry
  - external secret management
  - environment ë¶ë¦¬
  - CI ì ì´ë¯¸ì§ íê·¸ ì ëµ
