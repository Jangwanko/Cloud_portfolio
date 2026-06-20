# Demo Lite Profile

`demo-lite`는 2코어 2스레드급 서버에서 포트폴리오 데모를 띄우기 위한 축소 배포 프로파일입니다.

이 프로파일은 HA 성능 증명을 목표로 하지 않습니다. API -> Kafka -> Worker -> PostgreSQL 흐름, 브라우저 데모, Grafana 운영 화면을 저사양 환경에서 확인하는 것이 목적입니다.

## When To Use

| Profile | Purpose | Recommended host |
| --- | --- | --- |
| `full-ha` | Kafka 3 broker, PostgreSQL HA, Worker KEDA scale-out, failure/recovery validation | 8 CPU / 16GB 이상 권장 |
| `demo-lite` | 저사양 서버에서 주문 이후 이벤트 흐름 시연 | 2 CPU / 4GB 이상 최소, 8GB 권장 |

## What Changes

| Area | full-ha | demo-lite |
| --- | --- | --- |
| Kafka | 3 brokers, RF 3, min ISR 2 | 1 broker, RF 1, min ISR 1 |
| Kafka partitions | 8 | 3 |
| PostgreSQL | 3 PostgreSQL + 2 Pgpool | 1 PostgreSQL + 1 Pgpool |
| API | min 3 / max 8 | min 1 / max 2 |
| Worker | min 2 / max 8 | min 1 / max 2 |
| notification-worker | 1 | 0 |
| dlq-replayer | 1 | 0 |
| Prometheus / Grafana | enabled | enabled with lower resource requests |

## Run

Local kind:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/quick_start_lite.ps1
```

2-core Linux server with k3s:

```bash
HOST_NAME=your.domain.example BASE_URL=http://your.domain.example bash scripts/deploy_lite_k3s.sh
```

If you access the server by public IP, set `HOST_NAME` to that IP or leave it empty and use the server-local `localhost` path through SSH port forwarding.

GitOps on k3s:

First bootstrap the non-GitOps dependencies once: PostgreSQL lite profile, runtime secret, kube-state-metrics, and KEDA. `deploy_lite_k3s.sh` already does that direct bootstrap. After that, Argo CD can manage the application runtime from Git.

```bash
REPO_URL=https://github.com/Jangwanko/Cloud_portfolio.git \
REVISION=demo-lite \
bash scripts/bootstrap_argocd_lite_k3s.sh
```

Argo CD watches:

```text
k8s/gitops/overlays/demo-lite-k3s
```

After this, pushing a new commit to `demo-lite` updates the demo stack through Argo CD automated sync.

After completion:

- Demo UI: `http://localhost/demo/order-dashboard.html`
- API docs: `http://localhost/docs`
- Grafana: `http://localhost/grafana/d/messaging-portfolio-overview/messaging-portfolio-operations-overview?orgId=1&refresh=5s`
- Readiness: `http://localhost/health/ready`

For server deployment, replace `localhost` with `HOST_NAME` or the public address used in `BASE_URL`.

## Server Prerequisites

For the k3s deployment script, prepare:

- Linux server with `2 vCPU` and at least `4GB RAM` (`8GB` is more comfortable)
- Docker
- k3s
- kubectl access to the k3s cluster
- Helm 3
- curl, python3, openssl
- inbound HTTP `80` open on the server firewall or cloud security group

The script builds the local Docker image and imports it into k3s containerd, so it does not require pushing the image to a registry.

On a fresh k3s server, configure kubectl before deployment:

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$USER:$USER" ~/.kube/config
kubectl get nodes
```

## Trade-offs

- Kafka broker failure tolerance is removed. A single broker is enough for flow demonstration, not HA proof.
- PostgreSQL standby validation is disabled by setting `POSTGRES_MIN_READY_STANDBYS=0`.
- Worker scale-out is capped at `2`, so backlog drain is slower than `full-ha`.
- DLQ replay automation is disabled to save resources. DLQ summary can still be inspected, but automatic replay is not the focus of this profile.
- Performance numbers from `demo-lite` must not be mixed with the Kafka baseline in `docs/TEST_RESULTS.md`.

## Interview Positioning

Korean:

> 2코어 서버에서는 demo-lite 프로파일로 API -> Kafka -> Worker -> DB 흐름을 시연합니다. HA와 성능 기준선은 full-ha 프로파일에서 검증했고, lite는 저사양 서버에서 같은 구조의 핵심 흐름을 보여주기 위한 실행 모드입니다.

English:

> On a 2-core host, I use the demo-lite profile to demonstrate the API -> Kafka -> Worker -> PostgreSQL flow. HA and performance baselines remain validated in the full-ha profile; lite is a reduced runtime for constrained demo hosts.
