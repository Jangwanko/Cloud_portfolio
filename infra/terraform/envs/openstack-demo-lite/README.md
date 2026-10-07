# OpenStack demo-lite reproduction lab

Existing branch: demo-dev. This directory is independent of the AWS roots and can
later be included in master without replacing its full Kubernetes profile.

## Scope

Maintains the existing Hyper-V/OpenStack control plane. Reads the existing public
network, Ubuntu image and lab.medium flavor; uses the existing lab-key keypair.
Creates its own network, subnet, router/interface, security group/rules, port,
floating IP/association and one 2-vCPU/4-GiB/40-GiB VM (flavor dependent).

Cloud-init installs pinned k3s and the previously verified demo-lite source commit:
3509ed0f272984250f71a9d112450df8ef8b75fe (app tag a67f40e2a29b).
Order: PostgreSQL/Pgpool -> Kafka/topics -> migration -> workers -> API -> smoke.
Includes the core demo only. Argo CD, KEDA, monitoring, ingress and HA are NOT
installed by this lab. HTTP uses NodePort 30080 restricted to operator_cidr.
The k3s installer and apt repositories are network dependencies; this is not an
offline or fully hermetic build. Legacy Bitnami images match the proven lab.

## Prerequisites and credentials

- Terraform >=1.15.8,<1.16.0 and OpenSSH on Windows.
- Accessible OpenStack endpoints, functioning nested KVM and public-network NAT.
- clouds.yaml outside Git; set OS_CLIENT_CONFIG_FILE to its absolute path.
- Copy terraform.tfvars.example to terraform.tfvars; insert your Windows PUBLIC key.
- Keep the SSH PRIVATE key outside this directory; unlock it in ssh-agent for
  BatchMode connections if passphrase protected.
- Enough host capacity for lab.medium. Do not run a second 4-GiB VM blindly.
- The retained OpenStack keypair can differ from the extra Windows public key.
- Default operator_cidr is Windows 192.168.100.1/32; adjust for the actual source.
- Do not regenerate credentials or rerun deploy.py on an established application.
  This bootstrap is for fresh VMs. Fix failed scripts and replace only the lab VM.

## Run

Review first without creating anything:

    terraform init
    terraform fmt -check
    terraform validate
    terraform plan

Then, from Windows PowerShell:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -SshKey "$env:USERPROFILE\.ssh\openstack_lab"

Unlock the private key with ssh-add first. The runner accepts new SSH host keys (TOFU); it does not bypass changed-host-key checks.

The runner plans and applies, waits for the in-VM persistence smoke, checks HTTP
from Windows, and requires a second plan exit code of zero. Terraform apply alone
only confirms infrastructure creation, not completed cloud-init or app readiness.
No remote-exec provisioner or private key is put in Terraform state.
Local state/cloud user-data still contain infrastructure details; keep private.

## Existing manual lab deletion

This configuration NEVER deletes or imports the old lab automatically.
Before the separately authorized cleanup, inventory exact server/network/router/
floating-IP/security-group IDs and their attachments. Preserve public network,
images, flavors, keypairs and the entire OpenStack control plane.
Delete only the approved manual lab-vm01/lab-vm02 resources and private lab network
dependencies. This loses their app database. Never run a bulk project deletion.
Only perform cleanup once plan and operator access have been checked.

## Evidence and cleanup

Success requires all of: event HTTP 202, persisted status, identical event fields,
external readiness, and subsequent Terraform plan exit 0.
Cloud-init logs: /var/log/cloud-init-output.log, /var/log/demo-bootstrap.log.
Status: /opt/demo/status. kubectl: sudo k3s kubectl get pods -A.
A failed deploy retains resources for diagnosis. No automatic destroy on failure.

Review terraform plan -destroy before terraform destroy. This deletes this root's
VM and application data, not the read-only public network/image/flavor.
After destroy, terraform state list must be empty. Re-running run.ps1 should build
a fresh environment and pass again. Keep state until destruction is verified.

## Verification status

2026-09-25: Terraform 1.15.8 SHA256 verified; fmt/init/validate passed.
Mock tests: 3 passed. Python AST, Bash syntax, PowerShell parser passed.
Live plan: 11 creates, no changes/deletes. First apply: 11 resources created.
Live destroy: 11 deleted, empty state verified. Clean second run: 11 created,
bootstrap + event persistence + external readiness passed; final plan exit 0.
See VALIDATION.md for scope and first-run corrections.
Prior manual deployment success is not Terraform evidence.

## Small CPU resize

Set resize_vcpus = 3 in your private tfvars (base lab.medium remains 4 GiB/40 GiB).
Terraform creates a private flavor and resizes the existing VM in-place. Review
plan for replacements before apply. Admin permission to create flavors is needed.
The flavor is also Terraform-managed, so this configuration has 12 resources after
enabling resize, rather than the original 11.

Use scripts/resize-probe.py prepare URL resize-probe.private.json before changing
and verify with the same arguments afterwards. The ignored file includes a test
account password; do not publish it. Verify guest nproc, node/pod recovery and a
fresh smoke after resize. The initial /opt/demo/status PASS marker alone does NOT
prove current health or a new event after resize; run these checks explicitly.
See VALIDATION.md for the observed 2 -> 3 CPU change and preserved event.