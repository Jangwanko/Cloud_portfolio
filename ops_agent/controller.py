from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any
from uuid import uuid4

from ops_agent import __version__
from ops_agent.artifacts import redact, sanitize_text, write_raw_artifact
from ops_agent.models import (
    BundleContext,
    CollectionMetadata,
    CollectionStatus,
    Coverage,
    EvidenceBundle,
    EvidenceError,
    EvidenceItem,
    EvidenceStatus,
    Freshness,
    FreshnessStatus,
    MetricObservation,
    Scope,
    Semantic,
)
from ops_agent.collectors.application import collect_application
from ops_agent.collectors.argocd import collect_argocd
from ops_agent.collectors.kubernetes import (
    collect_kubernetes,
    get_current_context,
    resolve_kubectl_path,
    validate_kubernetes_context,
)
from ops_agent.collectors.prometheus import PrometheusCollector
from ops_agent.endpoint_provenance import safe_endpoint_provenance


TOOL_REGISTRY_VERSION = "ops.readonly.v1"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:/@+=-]{1,1024}$", flags=re.ASCII)
_GIT_REVISION = re.compile(r"^[0-9a-fA-F]{6,64}$", flags=re.ASCII)
_UNAVAILABLE_SIGNALS = {
    "exact_postgres_commit_insert_rate": (
        "exact_db_persistence_rate",
        "No post-commit inserted/deduplicated counter is currently instrumented.",
    ),
    "postgres_transaction_commit_latency": (
        "postgres_commit_latency",
        "The db_persist stage excludes request-status update and conn.commit().",
    ),
    "consumer_rebalance_event": (
        "consumer_rebalance_observation",
        "The Worker has no rebalance listener metric or event source.",
    ),
    "cpu_throttling": (
        "container_cpu_throttling",
        "Prometheus does not currently scrape kubelet/cAdvisor throttling metrics.",
    ),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _status(value: Any) -> EvidenceStatus:
    try:
        return EvidenceStatus(str(value))
    except ValueError:
        return EvidenceStatus.ERROR


def _error(value: Any, fallback: str) -> EvidenceError | None:
    if isinstance(value, str) and value:
        return EvidenceError(type="CollectionError", message=sanitize_text(value)[:1000])
    if not isinstance(value, Mapping):
        return None
    return EvidenceError(
        type=str(value.get("type") or "CollectionError"),
        message=sanitize_text(str(value.get("message") or fallback))[:1000],
    )


def _evidence_id(
    source: str,
    metric_name: str,
    labels: Mapping[str, str],
    *,
    discriminator: str = "",
) -> str:
    identity = json.dumps(
        {
            "source": source,
            "metric": metric_name,
            "labels": dict(sorted(labels.items())),
            "discriminator": discriminator,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    prefix = f"{source}.{metric_name}".lower()
    prefix = "".join(char if char.isalnum() or char in "._-" else "_" for char in prefix)
    return f"{prefix}.{suffix}"


def _freshness(
    status: EvidenceStatus,
    source_timestamp: datetime | None,
    collected_at: datetime,
    max_age_seconds: float,
    basis: str,
    *,
    as_of: datetime,
    upstream_age_bound_seconds: float = 0,
) -> Freshness:
    if status == EvidenceStatus.NOT_APPLICABLE:
        return Freshness(
            status=FreshnessStatus.NOT_APPLICABLE,
            basis=basis,
        )
    if status != EvidenceStatus.OK or source_timestamp is None:
        return Freshness(
            status=FreshnessStatus.UNKNOWN,
            max_age_seconds=max_age_seconds,
            basis=basis,
        )
    age = max(0.0, (as_of - source_timestamp).total_seconds())
    if basis.startswith("collector_time"):
        age = max(age, max(0.0, (as_of - collected_at).total_seconds()))
    age += upstream_age_bound_seconds
    return Freshness(
        status=(
            FreshnessStatus.FRESH
            if age <= max_age_seconds
            else FreshnessStatus.STALE
        ),
        age_seconds=round(age, 6),
        max_age_seconds=max_age_seconds,
        basis=basis,
    )


def _item(
    *,
    source: str,
    tool_id: str,
    metric_name: str,
    value: Any,
    status: EvidenceStatus,
    collected_at: datetime,
    source_timestamp: datetime | None,
    max_age_seconds: float,
    freshness_basis: str,
    semantic_type: str,
    semantic_notes: str,
    labels: Mapping[str, str] | None = None,
    unit: str | None = None,
    window: str | None = None,
    aggregation: str | None = None,
    sample_count: int | None = None,
    coverage: Coverage | None = None,
    flags: list[str] | None = None,
    is_db_commit_rate: bool | None = None,
    raw: tuple[str, str] | None = None,
    error: Any = None,
    as_of: datetime,
    upstream_age_bound_seconds: float = 0,
    discriminator: str = "",
) -> EvidenceItem:
    safe_labels = redact({str(key): str(item) for key, item in (labels or {}).items()})
    normalized_labels = {str(key): str(item) for key, item in safe_labels.items()}
    normalized_error = _error(error, f"{source} collection failed")
    if status == EvidenceStatus.ERROR and normalized_error is None:
        normalized_error = EvidenceError(
            type="CollectionError",
            message=f"{source} collector returned ERROR without detail",
        )
    return EvidenceItem(
        evidence_id=_evidence_id(
            source,
            metric_name,
            normalized_labels,
            discriminator=discriminator,
        ),
        status=status,
        source=source,
        tool_id=tool_id,
        source_timestamp=source_timestamp,
        collected_at=collected_at,
        freshness=_freshness(
            status,
            source_timestamp,
            collected_at,
            max_age_seconds,
            freshness_basis,
            as_of=as_of,
            upstream_age_bound_seconds=upstream_age_bound_seconds,
        ),
        metric=MetricObservation(
            name=metric_name,
            value=redact(value),
            unit=unit,
            window=window,
            aggregation=aggregation,
            sample_count=sample_count,
        ),
        labels=normalized_labels,
        coverage=coverage or Coverage(),
        semantic=Semantic(
            type=semantic_type,
            notes=sanitize_text(semantic_notes),
            is_db_commit_rate=is_db_commit_rate,
            flags=flags or [],
        ),
        raw_ref=raw[0] if raw else None,
        raw_sha256=raw[1] if raw else None,
        error=normalized_error if status == EvidenceStatus.ERROR else None,
    )


def _application_evidence(
    result: Mapping[str, Any],
    policy: Mapping[str, Any],
    raw: tuple[str, str] | None,
    as_of: datetime,
) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    application_policy = policy["application"]
    for endpoint_name, max_age_key in (
        ("readiness", "readiness_max_age_seconds"),
        ("ops_summary", "ops_summary_max_age_seconds"),
    ):
        endpoint = data.get(endpoint_name) if isinstance(data, Mapping) else None
        endpoint = endpoint if isinstance(endpoint, Mapping) else {}
        status = _status(endpoint.get("status") or result.get("status"))
        collected_at = _parse_timestamp(endpoint.get("collected_at")) or as_of
        source_timestamp = _parse_timestamp(endpoint.get("source_timestamp")) or collected_at
        cache_bound = (
            float(application_policy["ops_summary_cache_seconds"])
            if endpoint_name == "ops_summary" and status == EvidenceStatus.OK
            else 0
        )
        value = {
            "http_status": endpoint.get("http_status"),
            "body_status": endpoint.get("body_status"),
            "body": endpoint.get("body"),
        }
        flags = ["advisory_source", "upstream_cache_15s"] if endpoint_name == "ops_summary" else []
        items.append(
            _item(
                source="application",
                tool_id=f"application.{endpoint_name}.get.v1",
                metric_name=f"application_{endpoint_name}_observation",
                value=value,
                status=status,
                collected_at=collected_at,
                source_timestamp=source_timestamp,
                max_age_seconds=float(application_policy[max_age_key]),
                freshness_basis=(
                    "collector_time_with_upstream_cache_bound"
                    if endpoint_name == "ops_summary"
                    else "collector_time_http_response"
                ),
                semantic_type=(
                    "application_ops_summary_advisory"
                    if endpoint_name == "ops_summary"
                    else "application_readiness"
                ),
                semantic_notes=(
                    "HTTP status and body state are preserved independently; no readiness condition is evaluated."
                    if endpoint_name == "readiness"
                    else "Advisory Worker summary backed by a 15 second in-process cache."
                ),
                labels={"endpoint": str(endpoint.get("path") or endpoint_name)},
                flags=flags,
                raw=raw,
                error=endpoint.get("error") or result.get("error"),
                as_of=as_of,
                upstream_age_bound_seconds=cache_bound,
            )
        )
        body = endpoint.get("body")
        if endpoint_name == "readiness" and status == EvidenceStatus.OK and isinstance(body, Mapping):
            for component, semantic_type, notes in (
                (
                    "kafka",
                    "application_kafka_readiness_observation",
                    "Kafka bootstrap reachability from the application readiness response; no condition evaluation.",
                ),
                (
                    "postgres",
                    "application_postgres_runtime_observation",
                    "PostgreSQL HA mode, primary, standby, sync standby, and replication-delay values; no degradation evaluation.",
                ),
            ):
                component_value = body.get(component)
                component_status = EvidenceStatus.OK if isinstance(component_value, Mapping) else EvidenceStatus.MISSING
                items.append(
                    _item(
                        source="application",
                        tool_id="application.readiness.get.v1",
                        metric_name=f"application_{component}_runtime_observation",
                        value=component_value if isinstance(component_value, Mapping) else None,
                        status=component_status,
                        collected_at=collected_at,
                        source_timestamp=source_timestamp,
                        max_age_seconds=float(application_policy["readiness_max_age_seconds"]),
                        freshness_basis="collector_time_http_response",
                        semantic_type=semantic_type,
                        semantic_notes=notes,
                        labels={"endpoint": str(endpoint.get("path") or endpoint_name)},
                        raw=raw,
                        as_of=as_of,
                    )
                )
    return items


def _resource_evidence(
    *,
    source: str,
    tool_id: str,
    metric_name: str,
    resource: Mapping[str, Any],
    semantic_type: str,
    semantic_notes: str,
    max_age_seconds: float,
    raw: tuple[str, str] | None,
    as_of: datetime,
    labels: Mapping[str, str],
    coverage: Coverage | None = None,
) -> EvidenceItem:
    status = _status(resource.get("status"))
    collected_at = _parse_timestamp(resource.get("collected_at")) or as_of
    return _item(
        source=source,
        tool_id=tool_id,
        metric_name=metric_name,
        value=resource.get("data"),
        status=status,
        collected_at=collected_at,
        source_timestamp=collected_at,
        max_age_seconds=max_age_seconds,
        freshness_basis="collector_time_kubernetes_api_observation",
        semantic_type=semantic_type,
        semantic_notes=semantic_notes,
        labels=labels,
        coverage=coverage,
        raw=raw,
        error=resource.get("error"),
        as_of=as_of,
    )


def _kubernetes_evidence(
    result: Mapping[str, Any],
    policy: Mapping[str, Any],
    raw: tuple[str, str] | None,
    as_of: datetime,
) -> list[EvidenceItem]:
    data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    max_age = float(policy["kubernetes"]["observation_max_age_seconds"])
    namespace = str(policy["scope"]["namespace"])
    deployment = data.get("deployment") if isinstance(data, Mapping) else None
    pods = data.get("pods") if isinstance(data, Mapping) else None
    scaled_object = data.get("scaled_object") if isinstance(data, Mapping) else None
    deployment = deployment if isinstance(deployment, Mapping) else {"status": result.get("status")}
    pods = pods if isinstance(pods, Mapping) else {"status": result.get("status")}
    scaled_object = (
        scaled_object if isinstance(scaled_object, Mapping) else {"status": result.get("status")}
    )
    desired = deployment.get("data") if isinstance(deployment.get("data"), Mapping) else {}
    pod_values = pods.get("data") if isinstance(pods.get("data"), list) else []
    desired_count = desired.get("desired_replicas")
    pod_names = [
        str(pod.get("metadata", {}).get("name"))
        for pod in pod_values
        if isinstance(pod, Mapping) and pod.get("metadata", {}).get("name")
    ]
    pod_coverage = Coverage(
        expected_count=desired_count if isinstance(desired_count, int) else None,
        observed_count=len(pod_names),
        complete=(len(pod_names) == desired_count if isinstance(desired_count, int) else None),
        observed_items=pod_names,
        notes="Observed Worker pods compared with Deployment spec replicas; no grace evaluation.",
    )
    return [
        _resource_evidence(
            source="kubernetes",
            tool_id="k8s.worker_deployment.get.v1",
            metric_name="kubernetes_worker_deployment_observation",
            resource=deployment,
            semantic_type="worker_deployment_raw_observation",
            semantic_notes="Desired, current, ready, available, and unavailable replicas are preserved without incident evaluation.",
            max_age_seconds=max_age,
            raw=raw,
            as_of=as_of,
            labels={"namespace": namespace, "deployment": str(policy["kubernetes"]["worker_deployment"])},
        ),
        _resource_evidence(
            source="kubernetes",
            tool_id="k8s.worker_pods.list.v1",
            metric_name="kubernetes_worker_pod_observations",
            resource=pods,
            semantic_type="worker_pod_runtime_observation",
            semantic_notes="Pod phase, readiness, restart count, termination, image, and imageID observations.",
            max_age_seconds=max_age,
            raw=raw,
            as_of=as_of,
            labels={"namespace": namespace, "selector": str(policy["kubernetes"]["worker_label_selector"])},
            coverage=pod_coverage,
        ),
        _resource_evidence(
            source="kubernetes",
            tool_id="k8s.worker_scaled_object.get.v1",
            metric_name="kubernetes_worker_scaled_object_observation",
            resource=scaled_object,
            semantic_type="keda_scaled_object_raw_observation",
            semantic_notes="KEDA conditions and bounds are collected without interpreting scale-out as an incident.",
            max_age_seconds=max_age,
            raw=raw,
            as_of=as_of,
            labels={"namespace": namespace, "scaled_object": str(policy["kubernetes"]["worker_scaled_object"])},
        ),
    ]


def _prometheus_coverage(value: Any) -> Coverage:
    if not isinstance(value, Mapping):
        return Coverage()
    expected = value.get("expected_partition_ids")
    observed = value.get("observed_partition_ids")
    missing = value.get("missing_partition_ids")
    unexpected = value.get("unexpected_partition_ids")
    expected_items = [str(item) for item in expected] if isinstance(expected, list) else []
    observed_items = [str(item) for item in observed] if isinstance(observed, list) else []
    missing_items = [str(item) for item in missing] if isinstance(missing, list) else []
    extra_items = [str(item) for item in unexpected] if isinstance(unexpected, list) else []
    notes: list[str] = []
    if value.get("duplicate_partition_ids"):
        notes.append("duplicate_partition_series")
    if value.get("series_without_partition_label"):
        notes.append("series_without_partition_label")
    return Coverage(
        expected_count=len(expected_items) if isinstance(expected, list) else None,
        observed_count=len(observed_items) if isinstance(observed, list) else value.get("observed_series"),
        complete=value.get("complete") if isinstance(value.get("complete"), bool) else None,
        expected_items=expected_items,
        observed_items=observed_items,
        missing_items=missing_items,
        extra_items=extra_items,
        notes=", ".join(notes) if notes else None,
    )


def _prometheus_flags(
    query: Mapping[str, Any],
    labels: Mapping[str, Any] | None,
    *,
    partition_mismatch: bool | None,
) -> list[str]:
    flags: set[str] = set()
    coverage = query.get("coverage")
    if isinstance(coverage, Mapping) and coverage.get("complete") is False:
        flags.add("partial_partition_coverage")
    if partition_mismatch is True:
        flags.add("partition_set_mismatch")
    for anomaly in query.get("anomalies", []):
        if not isinstance(anomaly, Mapping):
            continue
        anomaly_labels = anomaly.get("labels")
        if labels is not None and isinstance(anomaly_labels, Mapping) and dict(anomaly_labels) != dict(labels):
            continue
        anomaly_type = str(anomaly.get("type") or "metric_anomaly")
        if anomaly_type == "negative_value":
            flags.add("negative_value_preserved_not_zero")
        else:
            flags.add(anomaly_type)
    semantic = query.get("semantic")
    if isinstance(semantic, Mapping):
        if semantic.get("includes_transaction_commit") is False:
            flags.add("excludes_transaction_commit")
        if semantic.get("is_isolated_postgresql_commit_latency") is False:
            flags.add("not_isolated_postgresql_commit_latency")
    freshness = query.get("freshness")
    if isinstance(freshness, Mapping):
        if freshness.get("query_status") == "ERROR":
            flags.add("source_timestamp_query_error")
        elif freshness.get("query_status") == "MISSING":
            flags.add("source_timestamp_missing")
        freshness_coverage = freshness.get("coverage")
        if isinstance(freshness_coverage, Mapping) and freshness_coverage.get("labels_match_range") is False:
            flags.add("source_timestamp_coverage_mismatch")
    return sorted(flags)


def _prometheus_source_timestamp(query: Mapping[str, Any]) -> datetime | None:
    freshness = query.get("freshness")
    if not isinstance(freshness, Mapping):
        return None
    coverage = freshness.get("coverage")
    if not isinstance(coverage, Mapping) or coverage.get("labels_match_range") is not True:
        return None
    if freshness.get("query_status") != "OK" or freshness.get("status") not in {"FRESH", "STALE"}:
        return None
    return _parse_timestamp(freshness.get("source_timestamp"))


def _prometheus_evidence(
    result: Mapping[str, Any],
    policy: Mapping[str, Any],
    raw: tuple[str, str] | None,
    as_of: datetime,
) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    queries = result.get("queries") if isinstance(result.get("queries"), list) else []
    prom_policy = policy["prometheus"]
    partition_coverage = result.get("partition_coverage")
    partition_mismatch = (
        partition_coverage.get("partition_mismatch")
        if isinstance(partition_coverage, Mapping)
        else None
    )
    for query in queries:
        if not isinstance(query, Mapping):
            continue
        query_status = _status(query.get("status"))
        semantic = query.get("semantic") if isinstance(query.get("semantic"), Mapping) else {}
        series = query.get("series") if isinstance(query.get("series"), list) else []
        window = query.get("window") if isinstance(query.get("window"), Mapping) else {}
        window_seconds = int(window.get("duration_seconds") or prom_policy["range_window_seconds"])
        coverage = _prometheus_coverage(query.get("coverage"))
        source_timestamp = _prometheus_source_timestamp(query)
        if not series:
            collected_at = _parse_timestamp(result.get("collected_at")) or as_of
            items.append(
                _item(
                    source="prometheus",
                    tool_id=f"prometheus.{query.get('query_id')}.range.v1",
                    metric_name=str(query.get("metric_name") or query.get("query_id") or "unknown_metric"),
                    value=[],
                    status=query_status,
                    collected_at=collected_at,
                    source_timestamp=source_timestamp,
                    max_age_seconds=float(prom_policy["sample_max_age_seconds"]),
                    freshness_basis="prometheus_timestamp_function",
                    semantic_type=str(semantic.get("type") or "prometheus_metric_range"),
                    semantic_notes=str(semantic.get("notes") or "Fixed Prometheus range query."),
                    labels={
                        "topic": str(policy["scope"]["topic"]),
                        "consumer_group": str(policy["scope"]["consumer_group"]),
                    },
                    unit=str(query.get("unit")) if query.get("unit") else None,
                    window=f"{window_seconds}s",
                    aggregation="raw_range_samples",
                    sample_count=int(query.get("sample_count") or 0),
                    coverage=coverage,
                    flags=_prometheus_flags(query, None, partition_mismatch=partition_mismatch),
                    is_db_commit_rate=(
                        bool(semantic.get("is_db_commit_rate"))
                        if "is_db_commit_rate" in semantic
                        else None
                    ),
                    raw=raw,
                    error=query.get("error"),
                    as_of=as_of,
                )
            )
            continue

        for raw_series in series:
            if not isinstance(raw_series, Mapping):
                continue
            labels = raw_series.get("labels") if isinstance(raw_series.get("labels"), Mapping) else {}
            samples = raw_series.get("samples") if isinstance(raw_series.get("samples"), list) else []
            metric_name = str(labels.get("__name__") or query.get("metric_name") or query.get("query_id"))
            collected_at = _parse_timestamp(result.get("collected_at")) or as_of
            items.append(
                _item(
                    source="prometheus",
                    tool_id=f"prometheus.{query.get('query_id')}.range.v1",
                    metric_name=metric_name,
                    value=samples,
                    status=query_status,
                    collected_at=collected_at,
                    source_timestamp=source_timestamp,
                    max_age_seconds=float(prom_policy["sample_max_age_seconds"]),
                    freshness_basis="prometheus_timestamp_function",
                    semantic_type=str(semantic.get("type") or "prometheus_metric_range"),
                    semantic_notes=str(semantic.get("notes") or "Fixed Prometheus range query."),
                    labels={str(key): str(value) for key, value in labels.items()},
                    unit=str(query.get("unit")) if query.get("unit") else None,
                    window=f"{window_seconds}s",
                    aggregation="raw_range_samples",
                    sample_count=len(samples),
                    coverage=coverage,
                    flags=_prometheus_flags(query, labels, partition_mismatch=partition_mismatch),
                    is_db_commit_rate=(
                        bool(semantic.get("is_db_commit_rate"))
                        if "is_db_commit_rate" in semantic
                        else None
                    ),
                    raw=raw,
                    error=query.get("error"),
                    as_of=as_of,
                )
            )
    if items:
        return items
    collected_at = _parse_timestamp(result.get("collected_at")) or as_of
    return [
        _item(
            source="prometheus",
            tool_id="prometheus.fixed_registry.collect.v1",
            metric_name="prometheus_collection",
            value=None,
            status=_status(result.get("status")),
            collected_at=collected_at,
            source_timestamp=None,
            max_age_seconds=float(prom_policy["sample_max_age_seconds"]),
            freshness_basis="prometheus_timestamp_function",
            semantic_type="prometheus_fixed_registry_collection",
            semantic_notes="No fixed query result was returned.",
            raw=raw,
            error=result.get("error") or "Prometheus collector returned no query results",
            as_of=as_of,
        )
    ]
def _argocd_evidence(
    result: Mapping[str, Any],
    policy: Mapping[str, Any],
    raw: tuple[str, str] | None,
    as_of: datetime,
) -> list[EvidenceItem]:
    status = _status(result.get("status"))
    collected_at = _parse_timestamp(result.get("collected_at")) or as_of
    source_timestamp = (
        _parse_timestamp(result.get("source_timestamp"))
        if status == EvidenceStatus.OK
        else None
    )
    return [
        _item(
            source="argocd",
            tool_id="argocd.application.get.v1",
            metric_name="argocd_application_observation",
            value=result.get("data"),
            status=status,
            collected_at=collected_at,
            source_timestamp=source_timestamp,
            max_age_seconds=float(
                policy["argocd"]["reconciliation_max_age_seconds"]
            ),
            freshness_basis="argocd_application_reconciled_at",
            semantic_type="gitops_reconciliation_observation",
            semantic_notes="Sync, health, and revision freshness uses the Application reconciledAt source timestamp.",
            labels={
                "namespace": str(policy["argocd"]["namespace"]),
                "application": str(policy["argocd"]["application"]),
            },
            raw=raw,
            error=result.get("error"),
            as_of=as_of,
        )
    ]


def _unavailable_evidence(as_of: datetime) -> list[EvidenceItem]:
    return [
        _item(
            source="instrumentation",
            tool_id=f"unsupported.{metric_name}.v1",
            metric_name=metric_name,
            value=None,
            status=EvidenceStatus.UNAVAILABLE,
            collected_at=as_of,
            source_timestamp=None,
            max_age_seconds=1,
            freshness_basis="instrumentation_not_present",
            semantic_type=semantic_type,
            semantic_notes=notes,
            as_of=as_of,
        )
        for metric_name, (semantic_type, notes) in _UNAVAILABLE_SIGNALS.items()
    ]


def _source_revision() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def _source_dirty() -> bool | None:
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(_REPO_ROOT),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return bool(completed.stdout.strip())


def _collector_tree_sha256() -> str | None:
    collector_root = _REPO_ROOT / "ops_agent"
    try:
        paths = sorted(
            path
            for path in collector_root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in {".py", ".yaml"}
            and "__pycache__" not in path.parts
        )
        digest = hashlib.sha256()
        for path in paths:
            relative = path.relative_to(_REPO_ROOT).as_posix().encode("utf-8")
            digest.update(relative)
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    except OSError:
        return None
    return digest.hexdigest()


def _safe_runtime_identifier(value: object) -> str | None:
    if not isinstance(value, str) or _RUNTIME_IDENTIFIER.fullmatch(value) is None:
        return None
    sanitized = sanitize_text(value)
    if sanitized != value or "[REDACTED]" in sanitized:
        return None
    return value


def _safe_git_revision(value: object) -> str | None:
    return value if isinstance(value, str) and _GIT_REVISION.fullmatch(value) else None


def _runtime_context(results: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, list[str], str | None]:
    kubernetes = results.get("kubernetes", {})
    k8s_data = kubernetes.get("data") if isinstance(kubernetes.get("data"), Mapping) else {}
    deployment = k8s_data.get("deployment") if isinstance(k8s_data, Mapping) else None
    deployment_data = (
        deployment.get("data") if isinstance(deployment, Mapping) and isinstance(deployment.get("data"), Mapping) else {}
    )
    desired_images = [
        safe_image
        for container in deployment_data.get("desired_containers", [])
        if isinstance(container, Mapping)
        and (safe_image := _safe_runtime_identifier(container.get("image")))
    ]
    pods = k8s_data.get("pods") if isinstance(k8s_data, Mapping) else None
    pod_data = pods.get("data") if isinstance(pods, Mapping) and isinstance(pods.get("data"), list) else []
    image_ids = sorted(
        {
            safe_image_id
            for pod in pod_data
            if isinstance(pod, Mapping)
            for container in pod.get("containers", [])
            if isinstance(container, Mapping)
            and (safe_image_id := _safe_runtime_identifier(container.get("image_id")))
        }
    )
    argocd = results.get("argocd", {})
    argo_data = argocd.get("data") if isinstance(argocd.get("data"), Mapping) else {}
    application = argo_data.get("application") if isinstance(argo_data, Mapping) else None
    revision = (
        _safe_git_revision(application.get("revision"))
        if isinstance(application, Mapping)
        else None
    )
    return (desired_images[0] if len(desired_images) == 1 else None, image_ids, revision)


def _collection_status(results: Mapping[str, Mapping[str, Any]]) -> CollectionStatus:
    applicable = {
        source: result
        for source, result in results.items()
        if result.get("status") != "NOT_APPLICABLE"
    }
    failed = [
        result
        for result in applicable.values()
        if result.get("status") != "OK" or bool(result.get("partial"))
    ]
    if not failed:
        return CollectionStatus.COMPLETE

    observed = False
    application = applicable.get("application", {})
    application_data = application.get("data")
    if isinstance(application_data, Mapping):
        observed = any(
            isinstance(item, Mapping) and item.get("status") == "OK"
            for item in application_data.values()
        )
    prometheus = applicable.get("prometheus", {})
    queries = prometheus.get("queries")
    if isinstance(queries, list):
        observed = observed or any(
            isinstance(query, Mapping) and query.get("status") == "OK"
            for query in queries
        )
    kubernetes = applicable.get("kubernetes", {})
    kubernetes_data = kubernetes.get("data")
    if isinstance(kubernetes_data, Mapping):
        observed = observed or any(
            isinstance(item, Mapping) and item.get("status") == "OK"
            for item in kubernetes_data.values()
        )
    argocd = applicable.get("argocd", {})
    observed = observed or argocd.get("status") == "OK"
    return CollectionStatus.PARTIAL if observed else CollectionStatus.FAILED


def build_bundle_from_results(
    *,
    policy: Mapping[str, Any],
    collector_results: Mapping[str, Mapping[str, Any]],
    incident_id: str,
    cluster_context: str | None,
    artifact_root: Path,
    bundle_id: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    endpoint_provenance: Mapping[str, Mapping[str, Any]] | None = None,
) -> EvidenceBundle:
    started = started_at or utc_now()
    completed = completed_at or utc_now()
    actual_bundle_id = bundle_id or str(uuid4())
    raw_refs: dict[str, tuple[str, str]] = {}
    artifact_errors: dict[str, OSError] = {}
    for source, result in collector_results.items():
        try:
            raw_refs[source] = write_raw_artifact(
                artifact_root, actual_bundle_id, source, result
            )
        except OSError as exc:
            artifact_errors[source] = exc

    evidence: list[EvidenceItem] = []
    evidence.extend(
        _application_evidence(
            collector_results.get("application", {}), policy, raw_refs.get("application"), completed
        )
    )
    evidence.extend(
        _prometheus_evidence(
            collector_results.get("prometheus", {}), policy, raw_refs.get("prometheus"), completed
        )
    )
    evidence.extend(
        _kubernetes_evidence(
            collector_results.get("kubernetes", {}), policy, raw_refs.get("kubernetes"), completed
        )
    )
    evidence.extend(
        _argocd_evidence(
            collector_results.get("argocd", {}), policy, raw_refs.get("argocd"), completed
        )
    )
    desired_image, pod_image_ids, argocd_revision = _runtime_context(collector_results)
    evidence.extend(_unavailable_evidence(completed))
    for collector_source, provenance in sorted((endpoint_provenance or {}).items()):
        status = _status(provenance.get("status"))
        evidence.append(
            _item(
                source="collector_configuration",
                tool_id="collector.endpoint.identity.v1",
                metric_name="source_endpoint_identity",
                value=provenance.get("value") if status == EvidenceStatus.OK else None,
                status=status,
                collected_at=completed,
                source_timestamp=completed if status == EvidenceStatus.OK else None,
                max_age_seconds=1,
                freshness_basis="collector_time_endpoint_configuration",
                semantic_type="effective_source_endpoint_identity",
                semantic_notes=(
                    "Canonical transport URL and Host routing used by the collector; "
                    "this records configuration provenance, not remote server identity."
                ),
                labels={"collector_source": collector_source},
                flags=["configuration_provenance", "credentials_excluded"],
                error=provenance.get("error"),
                as_of=completed,
            )
        )
    evidence.extend(
        _item(
            source="artifact_store",
            tool_id="artifact.raw.write.v1",
            metric_name="raw_artifact_write",
            value=None,
            status=EvidenceStatus.ERROR,
            collected_at=completed,
            source_timestamp=None,
            max_age_seconds=1,
            freshness_basis="artifact_write_failed",
            semantic_type="raw_artifact_persistence_error",
            semantic_notes="Normalized evidence remains available; the redacted raw artifact was not persisted.",
            labels={"collector_source": source},
            error={
                "type": type(error).__name__,
                "message": "redacted raw artifact could not be written",
            },
            as_of=completed,
        )
        for source, error in artifact_errors.items()
    )
    collection_status = _collection_status(collector_results)
    if artifact_errors and collection_status == CollectionStatus.COMPLETE:
        collection_status = CollectionStatus.PARTIAL
    return EvidenceBundle(
        bundle_id=actual_bundle_id,
        incident_id=incident_id,
        cluster_profile=str(policy["profile"]),
        scope=Scope(
            context=cluster_context,
            namespace=str(policy["scope"]["namespace"]),
            topic=str(policy["scope"]["topic"]),
            consumer_group=str(policy["scope"]["consumer_group"]),
        ),
        context=BundleContext(
            source_sha=_source_revision(),
            source_dirty=_source_dirty(),
            collector_tree_sha256=_collector_tree_sha256(),
            desired_image=desired_image,
            pod_image_ids=pod_image_ids,
            argocd_revision=str(argocd_revision) if argocd_revision else None,
            collector_version=__version__,
            tool_registry_version=TOOL_REGISTRY_VERSION,
            policy_version=str(policy["version"]),
        ),
        collection=CollectionMetadata(
            started_at=started,
            completed_at=completed,
            status=collection_status,
        ),
        evidence=evidence,
    )


def _collector_error(source: str, error: Exception, collected_at: datetime) -> dict[str, Any]:
    return {
        "source": source,
        "status": "ERROR",
        "partial": False,
        "collected_at": collected_at.isoformat().replace("+00:00", "Z"),
        "data": {},
        "error": {
            "type": type(error).__name__,
            "message": sanitize_text(str(error) or f"{source} collection failed")[:500],
        },
    }


def collect_bundle(
    *,
    policy: Mapping[str, Any],
    incident_id: str,
    artifact_root: Path,
    application_url: str | None = None,
    prometheus_url: str | None = None,
    cluster_context: str | None = None,
    kubectl_path: str | None = None,
) -> EvidenceBundle:
    started_at = utc_now()
    effective_application_url = application_url or str(
        policy["application"]["base_url"]
    )
    effective_application_host = (
        None if application_url is not None else policy["application"].get("host_header")
    )
    effective_prometheus_url = prometheus_url or str(
        policy["prometheus"]["base_url"]
    )
    effective_prometheus_host = (
        None if prometheus_url is not None else policy["prometheus"].get("host_header")
    )
    endpoint_identities = {
        "application": safe_endpoint_provenance(
            base_url=effective_application_url,
            host_header=(
                str(effective_application_host)
                if effective_application_host is not None
                else None
            ),
            configuration_source=(
                "operator_override" if application_url is not None else "policy"
            ),
        ),
        "prometheus": safe_endpoint_provenance(
            base_url=effective_prometheus_url,
            host_header=(
                str(effective_prometheus_host)
                if effective_prometheus_host is not None
                else None
            ),
            configuration_source=(
                "operator_override" if prometheus_url is not None else "policy"
            ),
        ),
    }
    try:
        resolved_kubectl = resolve_kubectl_path(kubectl_path)
        if cluster_context is None:
            context_result = get_current_context(kubectl_path=resolved_kubectl)
            effective_context = (
                context_result.get("context")
                if context_result.get("status") == "OK"
                else None
            )
        else:
            effective_context = validate_kubernetes_context(cluster_context)
            context_result = {
                "status": "OK",
                "context": effective_context,
                "error": None,
            }
    except ValueError as exc:
        resolved_kubectl = None
        effective_context = None
        context_result = {
            "status": "ERROR",
            "context": None,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }

    results: dict[str, Mapping[str, Any]] = {}
    try:
        results["application"] = collect_application(
            effective_application_url,
            timeout_seconds=float(policy["application"]["timeout_seconds"]),
            host_header=(
                None
                if application_url is not None
                else effective_application_host
            ),
        )
    except Exception as exc:  # noqa: BLE001 - collector isolation is the contract.
        results["application"] = _collector_error("application_http", exc, utc_now())

    try:
        expected_count = int(policy["prometheus"]["expected_partitions"])
        results["prometheus"] = PrometheusCollector(
            effective_prometheus_url,
            topic=str(policy["scope"]["topic"]),
            consumer_group=str(policy["scope"]["consumer_group"]),
            expected_partition_ids=list(range(expected_count)),
            range_seconds=int(policy["prometheus"]["range_window_seconds"]),
            step_seconds=int(policy["prometheus"]["range_step_seconds"]),
            sample_max_age_seconds=float(policy["prometheus"]["sample_max_age_seconds"]),
            timeout_seconds=float(policy["prometheus"]["timeout_seconds"]),
            host_header=(
                None
                if prometheus_url is not None
                else effective_prometheus_host
            ),
        ).collect()
    except Exception as exc:  # noqa: BLE001 - collector isolation is the contract.
        results["prometheus"] = _collector_error("prometheus", exc, utc_now())

    if context_result.get("status") != "OK":
        collected_at = utc_now().isoformat().replace("+00:00", "Z")
        context_error = {
            "type": "ContextUnavailable",
            "message": "Kubernetes GET skipped because the context is unavailable",
        }
        results["kubernetes"] = {
            "source": "kubernetes_api_via_kubectl",
            "status": "ERROR",
            "partial": False,
            "collected_at": collected_at,
            "context": context_result,
            "data": {
                name: {
                    "status": "ERROR",
                    "collected_at": collected_at,
                    "data": None,
                    "error": context_error,
                }
                for name in ("deployment", "pods", "scaled_object")
            },
            "error": context_error,
        }
    else:
        try:
            results["kubernetes"] = collect_kubernetes(
                namespace=str(policy["scope"]["namespace"]),
                context=effective_context,
                deployment_name=str(policy["kubernetes"]["worker_deployment"]),
                pod_selector=str(policy["kubernetes"]["worker_label_selector"]),
                scaled_object_name=str(policy["kubernetes"]["worker_scaled_object"]),
                kubectl_path=resolved_kubectl,
                timeout_seconds=float(policy["kubernetes"]["timeout_seconds"]),
            )
        except Exception as exc:  # noqa: BLE001 - collector isolation is the contract.
            results["kubernetes"] = _collector_error("kubernetes_api_via_kubectl", exc, utc_now())

    if bool(policy["argocd"]["enabled"]) and context_result.get("status") != "OK":
        results["argocd"] = {
            "source": "argocd_application_cr",
            "status": "ERROR",
            "partial": False,
            "collected_at": utc_now().isoformat().replace("+00:00", "Z"),
            "data": {"applicability": "APPLICABLE", "application": None},
            "error": {
                "type": "ContextUnavailable",
                "message": "Argo CD GET skipped because the Kubernetes context is unavailable",
            },
        }
    else:
        try:
            results["argocd"] = collect_argocd(
                applicable=bool(policy["argocd"]["enabled"]),
                namespace=str(policy["argocd"]["namespace"]),
                application_name=str(policy["argocd"]["application"]),
                context=effective_context,
                kubectl_path=resolved_kubectl,
                timeout_seconds=float(policy["argocd"]["timeout_seconds"]),
            )
        except Exception as exc:  # noqa: BLE001 - collector isolation is the contract.
            results["argocd"] = _collector_error("argocd_application_cr", exc, utc_now())

    completed_at = utc_now()
    observed_context = None
    kubernetes_result = results.get("kubernetes", {})
    k8s_context = kubernetes_result.get("context")
    if isinstance(k8s_context, Mapping) and k8s_context.get("status") == "OK":
        observed_context = str(k8s_context.get("context"))
    elif context_result.get("status") == "OK" and effective_context:
        observed_context = str(effective_context)
    return build_bundle_from_results(
        policy=policy,
        collector_results=results,
        incident_id=incident_id,
        cluster_context=observed_context,
        artifact_root=artifact_root,
        started_at=started_at,
        completed_at=completed_at,
        endpoint_provenance=endpoint_identities,
    )
