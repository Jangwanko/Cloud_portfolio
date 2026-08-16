"""Fixed, read-only Kubernetes collectors for the core Worker path."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any


_ALLOWED_GET_RESOURCES = frozenset(
    {
        "deployment",
        "pods",
        "scaledobject.keda.sh",
        "applications.argoproj.io",
    }
)
_KUBERNETES_NAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$",
    flags=re.ASCII,
)
_KUBERNETES_CONTEXT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,252}$",
    flags=re.ASCII,
)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _error(error_type: str, message: str) -> dict[str, Any]:
    return {"type": error_type, "message": message}


def _validate_name(value: str, field: str) -> str:
    if len(value) > 253 or _KUBERNETES_NAME.fullmatch(value) is None:
        raise ValueError(f"invalid Kubernetes {field}")
    return value


def _validate_context(value: str) -> str:
    if _KUBERNETES_CONTEXT.fullmatch(value) is None:
        raise ValueError("invalid Kubernetes context")
    return value


def validate_kubernetes_context(value: str) -> str:
    """Validate the restricted context syntax accepted by the CLI boundary."""

    return _validate_context(value)


def _validate_kubectl_executable(value: str, platform_name: str) -> str:
    if platform_name == "nt" and Path(value).suffix.lower() != ".exe":
        raise ValueError("Windows kubectl executable must be an .exe file")
    if Path(value).suffix.lower() in {".bat", ".cmd"}:
        raise ValueError("batch-file kubectl launchers are not allowed")
    return value


def _command_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def resolve_kubectl_path(
    explicit: str | None = None,
    *,
    repo_root: Path | None = None,
    platform_name: str | None = None,
) -> str:
    """Prefer the repository-pinned Windows kubectl without executing it."""

    effective_platform = platform_name or os.name
    if explicit:
        resolved_explicit = explicit
        if effective_platform == "nt" and Path(explicit).suffix == "":
            resolved_explicit = shutil.which(explicit) or explicit
        return _validate_kubectl_executable(resolved_explicit, effective_platform)
    root = repo_root or Path(__file__).resolve().parents[2]
    if effective_platform == "nt":
        repository_kubectl = root / "tools" / "kubectl.exe"
        if repository_kubectl.is_file():
            return str(repository_kubectl)
        discovered = shutil.which("kubectl")
        if discovered:
            return _validate_kubectl_executable(discovered, effective_platform)
        return "kubectl.exe"
    return _validate_kubectl_executable(
        shutil.which("kubectl") or "kubectl", effective_platform
    )


def get_current_context(
    *,
    kubectl_path: str | None = None,
    timeout_seconds: float = 5.0,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Read only the selected context name, never kubeconfig or auth material."""

    try:
        resolved_kubectl = resolve_kubectl_path(kubectl_path)
    except ValueError as exc:
        return {
            "status": "ERROR",
            "context": None,
            "error": _error(type(exc).__name__, str(exc)),
        }
    command = [resolved_kubectl, "config", "current-context"]
    command_runner = runner or subprocess.run
    try:
        completed = command_runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return {
            "status": "ERROR",
            "context": None,
            "error": _error("CommandNotFound", "kubectl executable was not found"),
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "ERROR",
            "context": None,
            "error": _error("CommandTimeout", "kubectl current-context timed out"),
        }
    except OSError:
        return {
            "status": "ERROR",
            "context": None,
            "error": _error("CommandError", "kubectl current-context could not be executed"),
        }

    if completed.returncode != 0:
        return {
            "status": "ERROR",
            "context": None,
            "error": _error(
                "KubectlContextFailed",
                "kubectl current-context failed",
            ),
        }
    context = _command_text(completed.stdout).strip()
    if not context:
        return {
            "status": "MISSING",
            "context": None,
            "error": _error("ContextMissing", "kubectl has no current context"),
        }
    try:
        _validate_context(context)
    except ValueError as exc:
        return {
            "status": "ERROR",
            "context": None,
            "error": _error(type(exc).__name__, str(exc)),
        }
    return {"status": "OK", "context": context, "error": None}


def run_kubectl_get_json(
    *,
    resource: str,
    namespace: str,
    name: str | None = None,
    selector: str | None = None,
    context: str | None = None,
    kubectl_path: str | None = None,
    timeout_seconds: float = 10.0,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Run one allowlisted Kubernetes GET and return JSON or a structured error."""

    if resource not in _ALLOWED_GET_RESOURCES:
        raise ValueError("Kubernetes resource is not allowlisted for collection")
    _validate_name(namespace, "namespace")
    if name is not None:
        _validate_name(name, "resource name")
    if resource != "pods" and name is None:
        raise ValueError("a resource name is required for this Kubernetes GET")
    if selector is not None and resource != "pods":
        raise ValueError("selectors are only supported for the fixed pod-list query")
    if context is not None:
        _validate_context(context)

    try:
        resolved_kubectl = resolve_kubectl_path(kubectl_path)
    except ValueError as exc:
        return {
            "status": "ERROR",
            "document": None,
            "error": _error(type(exc).__name__, str(exc)),
        }
    command = [resolved_kubectl]
    if context is not None:
        command.append(f"--context={context}")
    command.extend([f"--namespace={namespace}", "get", resource])
    if name is not None:
        command.append(name)
    if selector is not None:
        command.append(f"--selector={selector}")
    command.append("--output=json")

    command_runner = runner or subprocess.run
    try:
        completed = command_runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return {
            "status": "ERROR",
            "document": None,
            "error": _error("CommandNotFound", "kubectl executable was not found"),
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "ERROR",
            "document": None,
            "error": _error("CommandTimeout", "kubectl GET timed out"),
        }
    except OSError:
        return {
            "status": "ERROR",
            "document": None,
            "error": _error("CommandError", "kubectl GET could not be executed"),
        }

    stderr = _command_text(completed.stderr)
    if completed.returncode != 0:
        missing = "notfound" in stderr.lower() or "not found" in stderr.lower()
        return {
            "status": "MISSING" if missing else "ERROR",
            "document": None,
            "error": _error(
                "ResourceNotFound" if missing else "KubectlGetFailed",
                f"kubectl GET for {resource} failed",
            ),
        }

    try:
        document = json.loads(_command_text(completed.stdout))
    except json.JSONDecodeError:
        return {
            "status": "ERROR",
            "document": None,
            "error": _error("InvalidJson", f"kubectl GET for {resource} returned invalid JSON"),
        }
    if not isinstance(document, Mapping):
        return {
            "status": "ERROR",
            "document": None,
            "error": _error("InvalidJsonShape", "kubectl JSON root must be an object"),
        }
    return {"status": "OK", "document": dict(document), "error": None}


def _metadata(document: Mapping[str, Any]) -> dict[str, Any]:
    metadata = document.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "uid": metadata.get("uid"),
        "resource_version": metadata.get("resourceVersion"),
        "generation": metadata.get("generation"),
        "creation_timestamp": metadata.get("creationTimestamp"),
        "deletion_timestamp": metadata.get("deletionTimestamp"),
    }


def _conditions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for condition in value:
        if not isinstance(condition, Mapping):
            continue
        normalized.append(
            {
                "type": condition.get("type"),
                "status": condition.get("status"),
                "reason": condition.get("reason"),
                "last_transition_time": condition.get("lastTransitionTime"),
                "last_update_time": condition.get("lastUpdateTime"),
            }
        )
    return normalized


def _normalize_deployment(document: Mapping[str, Any]) -> dict[str, Any]:
    spec = document.get("spec")
    spec = spec if isinstance(spec, Mapping) else {}
    status = document.get("status")
    status = status if isinstance(status, Mapping) else {}
    template = spec.get("template")
    template = template if isinstance(template, Mapping) else {}
    pod_spec = template.get("spec")
    pod_spec = pod_spec if isinstance(pod_spec, Mapping) else {}
    containers = pod_spec.get("containers")
    containers = containers if isinstance(containers, list) else []
    desired_containers = [
        {"name": container.get("name"), "image": container.get("image")}
        for container in containers
        if isinstance(container, Mapping)
    ]
    return {
        "metadata": _metadata(document),
        "observed_generation": status.get("observedGeneration"),
        "desired_replicas": spec.get("replicas"),
        "current_replicas": status.get("replicas"),
        "updated_replicas": status.get("updatedReplicas"),
        "ready_replicas": status.get("readyReplicas"),
        "available_replicas": status.get("availableReplicas"),
        "unavailable_replicas": status.get("unavailableReplicas"),
        "desired_containers": desired_containers,
        "conditions": _conditions(status.get("conditions")),
    }


def _normalize_terminated(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        "reason": value.get("reason"),
        "exit_code": value.get("exitCode"),
        "signal": value.get("signal"),
        "started_at": value.get("startedAt"),
        "finished_at": value.get("finishedAt"),
    }


def _normalize_container_state(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    for state_name in ("waiting", "running", "terminated"):
        state = value.get(state_name)
        if not isinstance(state, Mapping):
            continue
        normalized: dict[str, Any] = {"type": state_name}
        if state_name == "waiting":
            normalized["reason"] = state.get("reason")
        elif state_name == "running":
            normalized["started_at"] = state.get("startedAt")
        else:
            normalized.update(_normalize_terminated(state) or {})
        return normalized
    return None


def _normalize_container_status(value: Mapping[str, Any]) -> dict[str, Any]:
    last_state = value.get("lastState")
    last_state = last_state if isinstance(last_state, Mapping) else {}
    return {
        "name": value.get("name"),
        "ready": value.get("ready"),
        "started": value.get("started"),
        "restart_count": value.get("restartCount"),
        "image": value.get("image"),
        "image_id": value.get("imageID"),
        "state": _normalize_container_state(value.get("state")),
        "last_termination": _normalize_terminated(last_state.get("terminated")),
    }


def _normalize_pod(document: Mapping[str, Any]) -> dict[str, Any]:
    status = document.get("status")
    status = status if isinstance(status, Mapping) else {}
    raw_conditions = status.get("conditions")
    conditions = _conditions(raw_conditions)
    ready_condition = next(
        (condition for condition in conditions if condition.get("type") == "Ready"),
        None,
    )
    container_statuses = status.get("containerStatuses")
    container_statuses = container_statuses if isinstance(container_statuses, list) else []
    return {
        "metadata": _metadata(document),
        "phase": status.get("phase"),
        "reason": status.get("reason"),
        "start_time": status.get("startTime"),
        "ready_condition_status": (
            ready_condition.get("status") if ready_condition is not None else None
        ),
        "conditions": conditions,
        "containers": [
            _normalize_container_status(item)
            for item in container_statuses
            if isinstance(item, Mapping)
        ],
    }


def _normalize_pods(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = document.get("items")
    if not isinstance(items, list):
        return []
    return [_normalize_pod(item) for item in items if isinstance(item, Mapping)]


def _normalize_scaled_object(document: Mapping[str, Any]) -> dict[str, Any]:
    spec = document.get("spec")
    spec = spec if isinstance(spec, Mapping) else {}
    status = document.get("status")
    status = status if isinstance(status, Mapping) else {}
    target = spec.get("scaleTargetRef")
    target = target if isinstance(target, Mapping) else {}
    triggers = spec.get("triggers")
    triggers = triggers if isinstance(triggers, list) else []
    health = status.get("health")
    normalized_health: dict[str, Any] = {}
    if isinstance(health, Mapping):
        for key, value in health.items():
            if not isinstance(value, Mapping):
                continue
            normalized_health[str(key)] = {
                "status": value.get("status"),
                "number_of_failures": value.get("numberOfFailures"),
            }
    return {
        "metadata": _metadata(document),
        "scale_target_name": target.get("name"),
        "polling_interval_seconds": spec.get("pollingInterval"),
        "cooldown_period_seconds": spec.get("cooldownPeriod"),
        "min_replicas": spec.get("minReplicaCount"),
        "max_replicas": spec.get("maxReplicaCount"),
        "trigger_types": [
            trigger.get("type") for trigger in triggers if isinstance(trigger, Mapping)
        ],
        "conditions": _conditions(status.get("conditions")),
        "hpa_name": status.get("hpaName"),
        "last_active_time": status.get("lastActiveTime"),
        "original_replica_count": status.get("originalReplicaCount"),
        "health": normalized_health,
    }


def _resource_item(
    response: Mapping[str, Any],
    *,
    collected_at: str,
    normalizer: Callable[[Mapping[str, Any]], Any],
) -> dict[str, Any]:
    status = response.get("status")
    document = response.get("document")
    if status == "OK" and isinstance(document, Mapping):
        return {
            "status": "OK",
            "collected_at": collected_at,
            "data": normalizer(document),
            "error": None,
        }
    return {
        "status": status,
        "collected_at": collected_at,
        "data": None,
        "error": response.get("error"),
    }


def collect_kubernetes(
    *,
    namespace: str = "messaging-app",
    context: str | None = None,
    deployment_name: str = "worker",
    pod_selector: str = "app=worker",
    scaled_object_name: str = "worker-keda",
    kubectl_path: str | None = None,
    timeout_seconds: float = 10.0,
    runner: CommandRunner | None = None,
    collected_at: str | None = None,
) -> dict[str, Any]:
    """Collect Worker deployment, pod, and KEDA observations without evaluation."""

    timestamp = collected_at or _utc_now()
    resolved_kubectl = resolve_kubectl_path(kubectl_path)
    if context is None:
        context_observation = get_current_context(
            kubectl_path=resolved_kubectl,
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
        effective_context = context_observation.get("context")
    else:
        try:
            effective_context = _validate_context(context)
            context_observation = {
                "status": "OK",
                "context": effective_context,
                "source": "configured",
                "error": None,
            }
        except ValueError as exc:
            effective_context = None
            context_observation = {
                "status": "ERROR",
                "context": None,
                "source": "configured",
                "error": _error(type(exc).__name__, str(exc)),
            }
    queries = {
        "deployment": {
            "resource": "deployment",
            "name": deployment_name,
            "normalizer": _normalize_deployment,
        },
        "pods": {
            "resource": "pods",
            "name": None,
            "selector": pod_selector,
            "normalizer": _normalize_pods,
        },
        "scaled_object": {
            "resource": "scaledobject.keda.sh",
            "name": scaled_object_name,
            "normalizer": _normalize_scaled_object,
        },
    }
    data: dict[str, Any] = {}
    for key, query in queries.items():
        if context_observation["status"] != "OK":
            response = {
                "status": "ERROR",
                "document": None,
                "error": _error(
                    "ContextUnavailable",
                    "Kubernetes GET skipped because the context is unavailable",
                ),
            }
        else:
            try:
                response = run_kubectl_get_json(
                    resource=str(query["resource"]),
                    namespace=namespace,
                    name=query.get("name"),
                    selector=query.get("selector"),
                    context=effective_context,
                    kubectl_path=resolved_kubectl,
                    timeout_seconds=timeout_seconds,
                    runner=runner,
                )
            except ValueError as exc:
                response = {
                    "status": "ERROR",
                    "document": None,
                    "error": _error(type(exc).__name__, str(exc)),
                }
        data[key] = _resource_item(
            response,
            collected_at=timestamp,
            normalizer=query["normalizer"],
        )

    statuses = [context_observation["status"]]
    statuses.extend(item["status"] for item in data.values())
    failed = [key for key, item in data.items() if item["status"] != "OK"]
    if context_observation["status"] != "OK":
        failed.insert(0, "context")
    aggregate_status = "OK"
    if failed:
        aggregate_status = "MISSING" if all(status == "MISSING" for status in statuses) else "ERROR"
    partial = bool(failed) and any(status == "OK" for status in statuses)
    return {
        "status": aggregate_status,
        "source": "kubernetes_api_via_kubectl",
        "collected_at": timestamp,
        "partial": partial,
        "context": context_observation,
        "data": data,
        "semantic": {
            "type": "worker_runtime_observation",
            "notes": "raw replica availability and KEDA state; no incident or grace-period evaluation",
        },
        "error": (
            None
            if not failed
            else _error("PartialCollectionError", f"failed resources: {', '.join(failed)}")
        ),
    }
