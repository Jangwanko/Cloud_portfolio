"""Read-only Argo CD Application status collection."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from ops_agent.collectors.kubernetes import CommandRunner, run_kubectl_get_json


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _error(error_type: str, message: str) -> dict[str, Any]:
    return {"type": error_type, "message": message}


def _normalize_application(document: Mapping[str, Any]) -> dict[str, Any]:
    metadata = document.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    spec = document.get("spec")
    spec = spec if isinstance(spec, Mapping) else {}
    source = spec.get("source")
    source = source if isinstance(source, Mapping) else {}
    destination = spec.get("destination")
    destination = destination if isinstance(destination, Mapping) else {}
    status = document.get("status")
    status = status if isinstance(status, Mapping) else {}
    sync = status.get("sync")
    sync = sync if isinstance(sync, Mapping) else {}
    health = status.get("health")
    health = health if isinstance(health, Mapping) else {}
    revisions = sync.get("revisions")
    revisions = revisions if isinstance(revisions, list) else []
    return {
        "metadata": {
            "name": metadata.get("name"),
            "namespace": metadata.get("namespace"),
            "uid": metadata.get("uid"),
            "resource_version": metadata.get("resourceVersion"),
            "generation": metadata.get("generation"),
        },
        "sync_status": sync.get("status"),
        "health_status": health.get("status"),
        "revision": sync.get("revision"),
        "revisions": revisions,
        "reconciled_at": status.get("reconciledAt"),
        "target_revision": source.get("targetRevision"),
        "source_path": source.get("path"),
        "destination_namespace": destination.get("namespace"),
    }


def collect_argocd(
    *,
    applicable: bool,
    namespace: str = "argocd",
    application_name: str = "messaging-portfolio-local-ha",
    context: str | None = None,
    kubectl_path: str | None = None,
    timeout_seconds: float = 10.0,
    runner: CommandRunner | None = None,
    collected_at: str | None = None,
) -> dict[str, Any]:
    """Collect an Application CR, or explicitly report profile non-applicability."""

    timestamp = collected_at or _utc_now()
    if not applicable:
        return {
            "status": "NOT_APPLICABLE",
            "source": "argocd_application_cr",
            "collected_at": timestamp,
            "partial": False,
            "data": {
                "applicability": "NOT_APPLICABLE",
                "application": None,
            },
            "semantic": {
                "type": "gitops_reconciliation_observation",
                "notes": "Argo CD is not part of this cluster profile",
            },
            "error": None,
        }

    try:
        response = run_kubectl_get_json(
            resource="applications.argoproj.io",
            namespace=namespace,
            name=application_name,
            context=context,
            kubectl_path=kubectl_path,
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
    except ValueError as exc:
        response = {
            "status": "ERROR",
            "document": None,
            "error": _error(type(exc).__name__, str(exc)),
        }

    if response["status"] != "OK":
        return {
            "status": response["status"],
            "source": "argocd_application_cr",
            "collected_at": timestamp,
            "partial": False,
            "data": {
                "applicability": "APPLICABLE",
                "application": None,
            },
            "semantic": {
                "type": "gitops_reconciliation_observation",
                "notes": "collection only; sync and health are not evaluated",
            },
            "error": response["error"],
        }

    document = response["document"]
    assert isinstance(document, Mapping)
    application = _normalize_application(document)
    return {
        "status": "OK",
        "source": "argocd_application_cr",
        "collected_at": timestamp,
        "partial": False,
        "data": {
            "applicability": "APPLICABLE",
            "application": application,
        },
        "semantic": {
            "type": "gitops_reconciliation_observation",
            "notes": "collection only; sync and health are not evaluated",
        },
        "source_timestamp": application.get("reconciled_at"),
        "error": None,
    }
