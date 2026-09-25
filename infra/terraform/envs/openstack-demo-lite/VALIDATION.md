# Local OpenStack validation — 2026-09-25

Implementation branch: existing demo-dev; no new branch was created.
Root: infra/terraform/envs/openstack-demo-lite.
Terraform 1.15.8, OpenStack provider 3.3.0 (lock file included).

## Results

- fmt/init/validate passed; mock plan tests 3 passed.
- Python AST, Bash -n, PowerShell parser passed.
- Live plan: 11 create, 0 change, 0 delete.
- Approved old lab-vm01/lab-vm02 and private lab networking removed.
- First apply: 11 created; app bootstrap/event smoke eventually passed.
- Terraform destroy: exactly 11 managed resources deleted; state list empty.
- Second run from empty state: single run.ps1 invocation, 11 created.
- No guest configuration correction or runner restart during second run.
- In-VM smoke: HTTP 202 -> persisted -> exactly one matching read-model event;
  event_type, payload and metadata preserved.
- Request ID: f0abf1c6-965d-42f1-8e74-1f5ef5601b16.
- External Windows HTTP readiness passed.
- Final terraform plan -detailed-exitcode: 0 (No changes).
- Runner process exit: 0.

Final VM: tf-demo-lite, 9ab86b6e-730d-4562-8bce-f49224af272b.
Final floating IP: 192.168.100.197.
Demo: http://192.168.100.197:30080/demo/order-dashboard.html
Cloud-init evidence: /var/log/demo-bootstrap.log and /opt/demo/status (PASS).

## Corrections before the successful clean run

The initial Windows launch was blocked by PowerShell execution policy. The tested
command sets ExecutionPolicy Bypass only on the child process. The first runner
also stopped on SSH stderr while the fresh guest was booting; the wait loop was
corrected to handle temporary failures. PowerShell plan filename arguments were
quoted. These initial attempts are not presented as uninterrupted success.

## Tested command

Set OS_CLIENT_CONFIG_FILE to the private clouds.yaml path and load the operator
key into ssh-agent before invocation. From this root:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./run.ps1 -SshKey "$env:USERPROFILE/.ssh/openstack_lab" -Terraform "C:/Users/rhwkd/VSC/Cloud_portfolio-demo-dev/tools/openstack-lab/terraform.exe"

## Boundaries

The pre-existing Hyper-V VM, OpenStack control plane, public network, image,
keypair and flavor were retained. Their construction is outside this Terraform
root. AWS Terraform deployment was not tested.

This proves the core demo-lite profile on one local OpenStack VM, not full
GitOps/monitoring/autoscaling, HA/failover or production reliability. Cloud-init
owns initial guest setup; Terraform's clean plan does not detect all in-guest
configuration drift. Future dependency/network availability can affect rebuilds.

State and credentials are ignored local files, not part of the source change.
The final successful environment is running; it has not been destroyed.
## In-place CPU resize — 2026-09-25

- Change: resize_vcpus = 3; RAM 4096 MiB and disk 40 GiB retained.
- New managed private flavor: tf-demo-lite-cpu3.
- Plan/apply: 1 flavor added, 1 VM updated in-place, 0 destroyed.
- Apply duration reported by Terraform: 1m40s (not measured outage duration).
- VM ID remained 9ab86b6e-730d-4562-8bce-f49224af272b.
- Floating IP remained 192.168.100.197.
- Guest nproc: 2 -> 3; free -m total remained 3915 MiB.
- Root filesystem remained 38 GiB; no disk resize was attempted.
- Before/after probe: request e2798b3d-b11b-4bef-831d-e5d1618051c3,
  event_id 2; exactly one matching event, identical type/payload/metadata.
- Post-resize new event: b0a22462-0246-4cbf-bd21-9eb61e571963;
  HTTP 202 -> persisted -> matching event PASS.
- Node Ready, application workloads 1/1 Running, HTTP readiness ready.
- Guest services restarted during resize; this was NOT a zero-downtime test.
- Final Terraform plan -detailed-exitcode: 0 (No changes).
- Current VM is intentionally left at 3 vCPU / 4 GiB / 40 GiB.
- Resize probe credentials are in ignored resize-probe.private.json only.
- Existing three mock tests cover baseline and input guards. CPU resize evidence
  is the live plan/apply and guest checks above; mock optional data attributes
  did not model provider-populated flavor RAM/disk reliably.