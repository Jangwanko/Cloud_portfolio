# Demo Lite GitOps Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `demo-lite` pushes produce a registry image and a GitOps-visible image tag change so Argo CD can deploy code changes automatically.

**Architecture:** GitHub Actions builds the app image on `demo-lite` pushes, pushes it to GHCR with a commit SHA tag, updates the `demo-lite-k3s` kustomize image tag, and commits that manifest change back to `demo-lite`. Argo CD watches `k8s/gitops/overlays/demo-lite-k3s`, sees the manifest update, and syncs the server without manual image import.

**Tech Stack:** GitHub Actions, GHCR, Docker Buildx, kustomize image override, Argo CD.

---

### Task 1: Kustomize Image Override

**Files:**
- Modify: `k8s/gitops/overlays/demo-lite/kustomization.yaml`

- [ ] Add an `images` block matching the base image name `messaging-portfolio`.
- [ ] Set `newName` to `ghcr.io/jangwanko/cloud_portfolio`.
- [ ] Set `newTag` to a placeholder that GitHub Actions can replace.
- [ ] Verify `kubectl kustomize k8s/gitops/overlays/demo-lite-k3s` renders app workloads with the GHCR image.

### Task 2: GitHub Actions Build And Tag Update

**Files:**
- Create: `.github/workflows/demo-lite-image.yml`

- [ ] Trigger on pushes to `demo-lite`.
- [ ] Skip runs created by the workflow itself with commit marker `[skip demo-lite image]`.
- [ ] Login to GHCR with `GITHUB_TOKEN`.
- [ ] Build and push `ghcr.io/jangwanko/cloud_portfolio:<short-sha>`.
- [ ] Update `k8s/gitops/overlays/demo-lite/kustomization.yaml` with the new tag.
- [ ] Commit and push the manifest tag update back to `demo-lite`.

### Task 3: Documentation

**Files:**
- Modify: `docs/DEMO_LITE.md`
- Modify: `docs/GITOPS.md`

- [ ] Replace the claim that Argo CD directly reflects code-only commits.
- [ ] Document that automatic reflection happens through GHCR image tag commits.
- [ ] Keep first-time bootstrap responsibilities separate: secrets, PostgreSQL chart, KEDA, kube-state-metrics.

### Task 4: Verification

**Files:**
- Test: `tests/test_portfolio_readiness.py`

- [ ] Add contract checks for the demo-lite image override and workflow.
- [ ] Run `.venv\Scripts\python.exe -m pytest -q`.
- [ ] Render `kubectl kustomize k8s/gitops/overlays/demo-lite-k3s` and confirm app workloads use the GHCR image.
