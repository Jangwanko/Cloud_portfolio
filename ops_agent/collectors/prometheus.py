"""Read-only collection of the fixed Prometheus evidence used by the Ops Agent.

The collector deliberately does not accept PromQL from callers.  It preserves
the range samples returned by Prometheus so later phases can distinguish a
missing series, an uninitialised ``-1`` offset, and an offset decrease from a
real zero value.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


DEFAULT_TOPIC = "message-ingress"
DEFAULT_CONSUMER_GROUP = "message-worker"
DEFAULT_RANGE_SECONDS = 60
DEFAULT_STEP_SECONDS = 5
DEFAULT_SAMPLE_MAX_AGE_SECONDS = 15
DEFAULT_EXPECTED_PARTITION_IDS = tuple(str(value) for value in range(8))
_FUTURE_TIMESTAMP_TOLERANCE_SECONDS = 0.001

_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_METRIC_COMPONENT_LABEL = "ops_metric_component"
_SAFE_LABEL_VALUE = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_HOST_HEADER = re.compile(r"^[A-Za-z0-9.-]+(?::[0-9]{1,5})?$", flags=re.ASCII)
_SENSITIVE_LABEL_FRAGMENTS = (
    "authorization",
    "cookie",
    "credential",
    "kubeconfig",
    "password",
    "secret",
    "token",
)


class PrometheusCollectionError(RuntimeError):
    """A fixed Prometheus query could not be collected or parsed."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def _open_no_redirect(request: Request, *, timeout: float):
    return build_opener(ProxyHandler({}), _NoRedirectHandler()).open(
        request, timeout=timeout
    )


@dataclass(frozen=True)
class _FixedQuery:
    query_id: str
    metric_name: str
    promql: str
    unit: str
    semantic_type: str
    semantic_notes: str
    partitioned: bool = False
    decrease_anomaly_type: str | None = None
    semantic_flags: tuple[tuple[str, bool], ...] = ()
    freshness_selectors: tuple[tuple[str, str], ...] = ()


def _rfc3339(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _validate_label_value(name: str, value: str) -> str:
    if not _SAFE_LABEL_VALUE.fullmatch(value):
        raise ValueError(f"{name} contains unsupported characters")
    return value


def _label_matcher(value: str) -> str:
    # Values have already passed the strict allowlist.  Keeping this helper
    # makes the boundary explicit if the allowed character set changes later.
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _normalise_expected_partitions(
    values: Sequence[int | str] | None,
) -> tuple[str, ...] | None:
    if values is None:
        return None
    normalised = tuple(str(value) for value in values)
    if len(set(normalised)) != len(normalised):
        raise ValueError("expected_partition_ids must be unique")
    return tuple(sorted(normalised, key=_partition_sort_key))


def _partition_sort_key(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value))
    except ValueError:
        return (1, value)


def _redact_labels(labels: Mapping[object, object]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for raw_key, raw_value in labels.items():
        key = str(raw_key)
        if any(fragment in key.lower() for fragment in _SENSITIVE_LABEL_FRAGMENTS):
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = str(raw_value)
    return redacted


def _fixed_queries(topic: str, consumer_group: str) -> tuple[_FixedQuery, ...]:
    topic = _label_matcher(topic)
    consumer_group = _label_matcher(consumer_group)
    return (
        _FixedQuery(
            query_id="kafka_topic_partition_current_offset",
            metric_name="kafka_topic_partition_current_offset",
            promql=(
                f'kafka_topic_partition_current_offset{{topic="{topic}"}}'
            ),
            unit="records",
            semantic_type="kafka_partition_end_offset_gauge",
            semantic_notes=(
                "Raw topic end offsets. Values cover every producer that appends "
                "to the ingress topic; they are not API-only accepted counts."
            ),
            partitioned=True,
            decrease_anomaly_type="offset_decrease",
        ),
        _FixedQuery(
            query_id="kafka_consumergroup_current_offset",
            metric_name="kafka_consumergroup_current_offset",
            promql=(
                "kafka_consumergroup_current_offset{"
                f'consumergroup="{consumer_group}",topic="{topic}"'
                "}"
            ),
            unit="records",
            semantic_type="kafka_consumer_committed_offset_gauge",
            semantic_notes=(
                "Raw committed offsets. A commit can follow persistence, an "
                "idempotency hit, rejection, or terminal DLQ handling."
            ),
            partitioned=True,
            decrease_anomaly_type="offset_decrease",
        ),
        _FixedQuery(
            query_id="kafka_consumergroup_lag",
            metric_name="kafka_consumergroup_lag",
            promql=(
                "kafka_consumergroup_lag{"
                f'consumergroup="{consumer_group}",topic="{topic}"'
                "}"
            ),
            unit="records",
            semantic_type="kafka_consumer_partition_lag_gauge",
            semantic_notes=(
                "Raw per-partition lag. Negative exporter sentinels are retained "
                "and must not be included as zero by this collector."
            ),
            partitioned=True,
        ),
        _FixedQuery(
            query_id="messaging_worker_processed_total",
            metric_name="messaging_worker_processed_total",
            promql=(
                'messaging_worker_processed_total{job="worker",'
                'result=~"success|rejected|dlq"}'
            ),
            unit="records",
            semantic_type="worker_terminal_processing_counter",
            semantic_notes=(
                "Counts success, rejected, and DLQ outcomes after Kafka offset "
                "commit. The failure outcome is excluded because it is rewound "
                "without offset commit. This is not PostgreSQL commit or insert rate."
            ),
            decrease_anomaly_type="counter_reset_or_decrease",
            semantic_flags=(("is_db_commit_rate", False),),
        ),
        _FixedQuery(
            query_id="messaging_queue_wait_seconds",
            metric_name="messaging_queue_wait_seconds",
            promql=(
                '{__name__=~"messaging_queue_wait_seconds_(bucket|count|sum)",'
                'job="worker"}'
            ),
            unit="seconds",
            semantic_type="queued_at_to_worker_handler_start_lag_histogram",
            semantic_notes=(
                "Measures API queued_at before Kafka append to Worker handler "
                "start and includes Kafka publish time."
            ),
            freshness_selectors=tuple(
                (
                    f'messaging_queue_wait_seconds_{component}{{job="worker"}}',
                    f"messaging_queue_wait_seconds_{component}",
                )
                for component in ("bucket", "count", "sum")
            ),
        ),
        _FixedQuery(
            query_id="messaging_event_persist_lag_seconds",
            metric_name="messaging_event_persist_lag_seconds",
            promql=(
                '{__name__=~"messaging_event_persist_lag_seconds_(bucket|count|sum)",'
                'job="worker"}'
            ),
            unit="seconds",
            semantic_type="api_queued_at_to_post_commit_observed_lag_histogram",
            semantic_notes=(
                "Measures payload queued_at to the timestamp refreshed after "
                "PostgreSQL commit returns. It includes queueing and processing "
                "and is not isolated PostgreSQL commit latency."
            ),
            semantic_flags=(("is_isolated_postgresql_commit_latency", False),),
            freshness_selectors=tuple(
                (
                    f'messaging_event_persist_lag_seconds_{component}{{job="worker"}}',
                    f"messaging_event_persist_lag_seconds_{component}",
                )
                for component in ("bucket", "count", "sum")
            ),
        ),
        _FixedQuery(
            query_id="messaging_worker_db_persist_stage_latency_seconds",
            metric_name="messaging_worker_stage_latency_seconds",
            promql=(
                '{__name__=~"messaging_worker_stage_latency_seconds_(bucket|count|sum)",'
                'job="worker",stage="db_persist"}'
            ),
            unit="seconds",
            semantic_type="persist_stage_latency_histogram",
            semantic_notes=(
                "Measures _persist_message_with_cursor only. It excludes request "
                "status work and the enclosing transaction commit."
            ),
            semantic_flags=(("includes_transaction_commit", False),),
            freshness_selectors=tuple(
                (
                    "messaging_worker_stage_latency_seconds_"
                    f'{component}{{job="worker",stage="db_persist"}}',
                    f"messaging_worker_stage_latency_seconds_{component}",
                )
                for component in ("bucket", "count", "sum")
            ),
        ),
    )


class PrometheusCollector:
    """Collect a fixed, read-only set of Prometheus range queries."""

    def __init__(
        self,
        base_url: str,
        *,
        topic: str = DEFAULT_TOPIC,
        consumer_group: str = DEFAULT_CONSUMER_GROUP,
        expected_partition_ids: Sequence[int | str] | None = DEFAULT_EXPECTED_PARTITION_IDS,
        range_seconds: int = DEFAULT_RANGE_SECONDS,
        step_seconds: int = DEFAULT_STEP_SECONDS,
        sample_max_age_seconds: float = DEFAULT_SAMPLE_MAX_AGE_SECONDS,
        timeout_seconds: float = 3.0,
        host_header: str | None = None,
        opener: Callable[..., object] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Prometheus base_url must be an HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Prometheus base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("Prometheus base_url must not contain a query or fragment")
        if range_seconds <= 0 or step_seconds <= 0 or sample_max_age_seconds <= 0:
            raise ValueError(
                "range_seconds, step_seconds, and sample_max_age_seconds must be positive"
            )
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if host_header is not None and _SAFE_HOST_HEADER.fullmatch(host_header) is None:
            raise ValueError("Prometheus Host header is invalid")

        self.base_url = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
        )
        self.topic = _validate_label_value("topic", topic)
        self.consumer_group = _validate_label_value(
            "consumer_group", consumer_group
        )
        self.expected_partition_ids = _normalise_expected_partitions(
            expected_partition_ids
        )
        self.range_seconds = int(range_seconds)
        self.step_seconds = int(step_seconds)
        self.sample_max_age_seconds = float(sample_max_age_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.host_header = host_header
        self._opener = opener or _open_no_redirect
        self._clock = clock
        self._queries = _fixed_queries(self.topic, self.consumer_group)

    def collect(self, *, end_time: float | None = None) -> dict[str, object]:
        collected_epoch = float(self._clock())
        range_end = collected_epoch if end_time is None else float(end_time)
        range_start = range_end - self.range_seconds

        def collect_query(spec: _FixedQuery) -> dict[str, object]:
            try:
                payload = self._query_range(spec, range_start, range_end)
                result = self._normalise_query_result(
                    spec,
                    payload,
                    collected_epoch=collected_epoch,
                    range_start=range_start,
                    range_end=range_end,
                )
            except (PrometheusCollectionError, HTTPError, URLError, OSError) as exc:
                result = self._error_result(
                    spec,
                    str(exc),
                    collected_epoch=collected_epoch,
                    range_start=range_start,
                    range_end=range_end,
                )
            try:
                freshness_payload = self._query_source_timestamps(spec, range_end)
                freshness = self._normalise_freshness_result(
                    freshness_payload,
                    collected_epoch=collected_epoch,
                    range_series=result["series"],
                )
            except (PrometheusCollectionError, HTTPError, URLError, OSError) as exc:
                freshness = self._freshness_error(
                    str(exc), range_series=result["series"]
                )
            result["freshness"] = freshness
            return result

        # Fixed queries are independent. Bounded parallelism prevents one
        # unavailable local endpoint from multiplying the per-request timeout.
        with ThreadPoolExecutor(max_workers=len(self._queries)) as executor:
            results = list(executor.map(collect_query, self._queries))

        statuses = [str(result["status"]) for result in results]
        ok_count = statuses.count("OK")
        if ok_count:
            overall_status = "OK"
        elif "ERROR" in statuses:
            overall_status = "ERROR"
        else:
            overall_status = "MISSING"

        freshness_query_statuses = [
            str(result["freshness"]["query_status"])  # type: ignore[index]
            for result in results
        ]
        freshness_statuses = [
            str(result["freshness"]["status"])  # type: ignore[index]
            for result in results
        ]
        partial_failure = bool(
            ok_count
            and (
                any(status != "OK" for status in statuses)
                or any(status != "OK" for status in freshness_query_statuses)
                or any(status == "UNKNOWN" for status in freshness_statuses)
            )
        )

        return {
            "source": "prometheus",
            "status": overall_status,
            "partial": partial_failure,
            "collected_at": _rfc3339(collected_epoch),
            "range": {
                "start": _rfc3339(range_start),
                "end": _rfc3339(range_end),
                "duration_seconds": self.range_seconds,
                "step_seconds": self.step_seconds,
            },
            "scope": {
                "topic": self.topic,
                "consumer_group": self.consumer_group,
            },
            "queries": results,
            "partition_coverage": self._cross_query_partition_coverage(results),
        }

    def _query_range(
        self, spec: _FixedQuery, range_start: float, range_end: float
    ) -> Mapping[str, object]:
        query = urlencode(
            {
                "query": spec.promql,
                "start": f"{range_start:.3f}",
                "end": f"{range_end:.3f}",
                "step": str(self.step_seconds),
            }
        )
        headers = {"Accept": "application/json"}
        if self.host_header is not None:
            headers["Host"] = self.host_header
        request = Request(
            f"{self.base_url}/api/v1/query_range?{query}",
            headers=headers,
            method="GET",
        )
        return self._read_json(request)

    def _query_source_timestamps(
        self, spec: _FixedQuery, evaluation_time: float
    ) -> Mapping[str, object]:
        selectors = spec.freshness_selectors or ((spec.promql, spec.metric_name),)
        timestamp_source = " or ".join(
            (
                f'label_replace(timestamp({selector}), '
                f'"{_METRIC_COMPONENT_LABEL}", "{component}", "", ".*")'
            )
            for selector, component in selectors
        )
        query = urlencode(
            {
                "query": timestamp_source,
                "time": f"{evaluation_time:.6f}",
            }
        )
        headers = {"Accept": "application/json"}
        if self.host_header is not None:
            headers["Host"] = self.host_header
        request = Request(
            f"{self.base_url}/api/v1/query?{query}",
            headers=headers,
            method="GET",
        )
        return self._read_json(request)

    def _read_json(self, request: Request) -> Mapping[str, object]:
        outcome: list[tuple[bool, object]] = []

        def read_response() -> None:
            try:
                with self._opener(request, timeout=self.timeout_seconds) as response:
                    outcome.append((True, response.read(_MAX_RESPONSE_BYTES + 1)))
            except BaseException as exc:  # noqa: BLE001 - re-raised below.
                outcome.append((False, exc))

        worker = threading.Thread(target=read_response, daemon=True)
        worker.start()
        worker.join(self.timeout_seconds)
        if worker.is_alive():
            raise PrometheusCollectionError(
                "Prometheus request exceeded the total time limit"
            )
        if not outcome:
            raise PrometheusCollectionError("Prometheus request returned no result")
        succeeded, value = outcome[0]
        if not succeeded:
            raise value  # type: ignore[misc]
        body = value
        if not isinstance(body, bytes):
            raise PrometheusCollectionError("Prometheus response body must be bytes")

        if len(body) > _MAX_RESPONSE_BYTES:
            raise PrometheusCollectionError("Prometheus response exceeded size limit")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PrometheusCollectionError("Prometheus returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise PrometheusCollectionError("Prometheus response must be an object")
        return payload

    def _normalise_query_result(
        self,
        spec: _FixedQuery,
        payload: Mapping[str, object],
        *,
        collected_epoch: float,
        range_start: float,
        range_end: float,
    ) -> dict[str, object]:
        if payload.get("status") != "success":
            error_type = str(payload.get("errorType") or "query_error")
            error = str(payload.get("error") or "Prometheus query failed")[:500]
            raise PrometheusCollectionError(f"{error_type}: {error}")

        data = payload.get("data")
        if not isinstance(data, Mapping) or data.get("resultType") != "matrix":
            raise PrometheusCollectionError(
                "Prometheus range query returned a non-matrix result"
            )
        raw_result = data.get("result")
        if not isinstance(raw_result, list):
            raise PrometheusCollectionError("Prometheus result must be a list")

        series: list[dict[str, object]] = []
        for raw_series in raw_result:
            if not isinstance(raw_series, Mapping):
                raise PrometheusCollectionError("Prometheus series must be an object")
            raw_labels = raw_series.get("metric")
            raw_samples = raw_series.get("values")
            if not isinstance(raw_labels, Mapping) or not isinstance(raw_samples, list):
                raise PrometheusCollectionError(
                    "Prometheus series labels or samples are malformed"
                )
            samples = [self._normalise_sample(sample) for sample in raw_samples]
            series.append(
                {
                    "labels": _redact_labels(raw_labels),
                    "samples": samples,
                }
            )

        latest_sample = max(
            (
                float(sample["timestamp"])
                for item in series
                for sample in item["samples"]  # type: ignore[index]
            ),
            default=None,
        )
        anomalies = self._sample_anomalies(
            series, decrease_anomaly_type=spec.decrease_anomaly_type
        )
        sample_count = sum(len(item["samples"]) for item in series)
        status = "OK" if sample_count else "MISSING"
        semantic: dict[str, object] = {
            "type": spec.semantic_type,
            "notes": spec.semantic_notes,
        }
        semantic.update(dict(spec.semantic_flags))

        result: dict[str, object] = {
            "query_id": spec.query_id,
            "metric_name": spec.metric_name,
            "status": status,
            "result_type": "matrix",
            "unit": spec.unit,
            "window": {
                "start": _rfc3339(range_start),
                "end": _rfc3339(range_end),
                "duration_seconds": self.range_seconds,
                "step_seconds": self.step_seconds,
            },
            "latest_range_evaluation_timestamp": (
                _rfc3339(latest_sample) if latest_sample is not None else None
            ),
            "series": series,
            "sample_count": sample_count,
            "coverage": self._coverage(series, partitioned=spec.partitioned),
            "semantic": semantic,
            "anomalies": anomalies,
            "error": None,
        }
        warnings = payload.get("warnings")
        if isinstance(warnings, list):
            result["warnings"] = [str(warning)[:500] for warning in warnings]
        return result

    def _normalise_freshness_result(
        self,
        payload: Mapping[str, object],
        *,
        collected_epoch: float,
        range_series: object,
    ) -> dict[str, object]:
        if payload.get("status") != "success":
            error_type = str(payload.get("errorType") or "query_error")
            error = str(payload.get("error") or "Prometheus query failed")[:500]
            raise PrometheusCollectionError(f"{error_type}: {error}")

        data = payload.get("data")
        if not isinstance(data, Mapping) or data.get("resultType") != "vector":
            raise PrometheusCollectionError(
                "Prometheus timestamp query returned a non-vector result"
            )
        raw_result = data.get("result")
        if not isinstance(raw_result, list):
            raise PrometheusCollectionError(
                "Prometheus timestamp result must be a list"
            )

        series: list[dict[str, object]] = []
        source_epochs: list[float] = []
        for raw_series in raw_result:
            if not isinstance(raw_series, Mapping):
                raise PrometheusCollectionError(
                    "Prometheus timestamp series must be an object"
                )
            raw_labels = raw_series.get("metric")
            raw_value = raw_series.get("value")
            if not isinstance(raw_labels, Mapping):
                raise PrometheusCollectionError(
                    "Prometheus timestamp labels are malformed"
                )
            sample = self._normalise_sample(raw_value)
            raw_source_timestamp = str(sample["value"])
            try:
                source_decimal = Decimal(raw_source_timestamp)
                if not source_decimal.is_finite():
                    raise InvalidOperation
                source_epoch = float(source_decimal)
            except (InvalidOperation, ValueError, OverflowError) as exc:
                raise PrometheusCollectionError(
                    "Prometheus timestamp() value is invalid"
                ) from exc
            source_epochs.append(source_epoch)
            series.append(
                {
                    "labels": _redact_labels(raw_labels),
                    "evaluation_timestamp": sample["timestamp"],
                    "source_timestamp_value": raw_source_timestamp,
                    "source_timestamp": _rfc3339(source_epoch),
                }
            )

        range_labels = self._series_label_counts(range_series)
        freshness_labels = self._series_label_counts(series)
        coverage = {
            "range_series_count": sum(range_labels.values()),
            "freshness_series_count": sum(freshness_labels.values()),
            "labels_match_range": (
                range_labels == freshness_labels if range_labels else None
            ),
        }
        if not source_epochs:
            return {
                "query_status": "MISSING",
                "status": "UNKNOWN",
                "basis": "prometheus_timestamp_function",
                "source_timestamp": None,
                "newest_source_timestamp": None,
                "age_seconds": None,
                "max_age_seconds": self.sample_max_age_seconds,
                "series": [],
                "coverage": coverage,
                "error": None,
            }

        oldest_epoch = min(source_epochs)
        newest_epoch = max(source_epochs)
        age_seconds = collected_epoch - oldest_epoch
        if coverage["labels_match_range"] is False:
            return {
                "query_status": "OK",
                "status": "UNKNOWN",
                "basis": "prometheus_timestamp_function",
                "source_timestamp": None,
                "newest_source_timestamp": None,
                "age_seconds": None,
                "max_age_seconds": self.sample_max_age_seconds,
                "series": series,
                "coverage": coverage,
                "error": "timestamp series labels do not cover the range series",
            }
        if age_seconds < -_FUTURE_TIMESTAMP_TOLERANCE_SECONDS:
            return {
                "query_status": "OK",
                "status": "UNKNOWN",
                "basis": "prometheus_timestamp_function",
                "source_timestamp": _rfc3339(oldest_epoch),
                "newest_source_timestamp": _rfc3339(newest_epoch),
                "age_seconds": None,
                "max_age_seconds": self.sample_max_age_seconds,
                "series": series,
                "coverage": coverage,
                "error": "source timestamp is later than collector time",
            }
        age_seconds = max(0.0, age_seconds)

        return {
            "query_status": "OK",
            "status": (
                "FRESH"
                if age_seconds <= self.sample_max_age_seconds
                else "STALE"
            ),
            "basis": "prometheus_timestamp_function",
            "source_timestamp": _rfc3339(oldest_epoch),
            "newest_source_timestamp": _rfc3339(newest_epoch),
            "age_seconds": age_seconds,
            "max_age_seconds": self.sample_max_age_seconds,
            "series": series,
            "coverage": coverage,
            "error": None,
        }

    def _freshness_error(
        self, error: str, *, range_series: object
    ) -> dict[str, object]:
        return {
            "query_status": "ERROR",
            "status": "UNKNOWN",
            "basis": "prometheus_timestamp_function",
            "source_timestamp": None,
            "newest_source_timestamp": None,
            "age_seconds": None,
            "max_age_seconds": self.sample_max_age_seconds,
            "series": [],
            "coverage": {
                "range_series_count": sum(
                    self._series_label_counts(range_series).values()
                ),
                "freshness_series_count": 0,
                "labels_match_range": None,
            },
            "error": error[:500],
        }

    @staticmethod
    def _series_label_counts(
        series: object,
    ) -> Counter[tuple[tuple[str, str], ...]]:
        if not isinstance(series, list):
            return Counter()
        values: Counter[tuple[tuple[str, str], ...]] = Counter()
        for item in series:
            if not isinstance(item, Mapping):
                continue
            labels = item.get("labels")
            if not isinstance(labels, Mapping):
                continue
            component = labels.get(_METRIC_COMPONENT_LABEL) or labels.get("__name__")
            identity = [
                (str(key), str(value))
                for key, value in labels.items()
                if str(key) not in {"__name__", _METRIC_COMPONENT_LABEL}
            ]
            if component is not None:
                identity.append((_METRIC_COMPONENT_LABEL, str(component)))
            values[tuple(sorted(identity))] += 1
        return values

    @staticmethod
    def _normalise_sample(raw_sample: object) -> dict[str, object]:
        if not isinstance(raw_sample, (list, tuple)) or len(raw_sample) < 2:
            raise PrometheusCollectionError("Prometheus sample is malformed")
        try:
            timestamp = float(raw_sample[0])
        except (TypeError, ValueError) as exc:
            raise PrometheusCollectionError(
                "Prometheus sample timestamp is invalid"
            ) from exc
        if raw_sample[1] is None:
            raise PrometheusCollectionError("Prometheus sample value is missing")
        return {"timestamp": timestamp, "value": str(raw_sample[1])}

    def _coverage(
        self, series: Iterable[dict[str, object]], *, partitioned: bool
    ) -> dict[str, object]:
        materialised = list(series)
        coverage: dict[str, object] = {"observed_series": len(materialised)}
        if not partitioned:
            return coverage

        partitions: list[str] = []
        missing_partition_label = 0
        for item in materialised:
            labels = item.get("labels")
            partition = labels.get("partition") if isinstance(labels, Mapping) else None
            if partition is None:
                missing_partition_label += 1
            else:
                partitions.append(str(partition))

        observed = tuple(sorted(set(partitions), key=_partition_sort_key))
        duplicate_partitions = tuple(
            sorted(
                {partition for partition in partitions if partitions.count(partition) > 1},
                key=_partition_sort_key,
            )
        )
        expected = self.expected_partition_ids
        if expected is None:
            missing = None
            unexpected = None
            complete = None
        else:
            missing = [partition for partition in expected if partition not in observed]
            unexpected = [
                partition for partition in observed if partition not in expected
            ]
            complete = not (
                missing
                or unexpected
                or duplicate_partitions
                or missing_partition_label
            )

        coverage.update(
            {
                "expected_partition_ids": list(expected) if expected is not None else None,
                "observed_partition_ids": list(observed),
                "missing_partition_ids": missing,
                "unexpected_partition_ids": unexpected,
                "duplicate_partition_ids": list(duplicate_partitions),
                "series_without_partition_label": missing_partition_label,
                "complete": complete,
            }
        )
        return coverage

    @staticmethod
    def _sample_anomalies(
        series: Iterable[dict[str, object]], *, decrease_anomaly_type: str | None
    ) -> list[dict[str, object]]:
        anomalies: list[dict[str, object]] = []
        for item in series:
            labels = item.get("labels")
            samples = item.get("samples")
            if not isinstance(labels, Mapping) or not isinstance(samples, list):
                continue
            ordered_samples = sorted(samples, key=lambda sample: float(sample["timestamp"]))
            previous: tuple[float, str, Decimal] | None = None
            for sample in ordered_samples:
                raw_value = str(sample["value"])
                try:
                    numeric_value = Decimal(raw_value)
                    if not numeric_value.is_finite():
                        raise InvalidOperation
                except InvalidOperation:
                    anomalies.append(
                        {
                            "type": "non_finite_or_non_numeric_value",
                            "labels": dict(labels),
                            "timestamp": sample["timestamp"],
                            "value": raw_value,
                        }
                    )
                    previous = None
                    continue

                if numeric_value < 0:
                    anomalies.append(
                        {
                            "type": "negative_value",
                            "labels": dict(labels),
                            "timestamp": sample["timestamp"],
                            "value": raw_value,
                        }
                    )
                if (
                    decrease_anomaly_type is not None
                    and previous is not None
                    and numeric_value < previous[2]
                ):
                    anomalies.append(
                        {
                            "type": decrease_anomaly_type,
                            "labels": dict(labels),
                            "previous_timestamp": previous[0],
                            "previous_value": previous[1],
                            "timestamp": sample["timestamp"],
                            "value": raw_value,
                        }
                    )
                previous = (float(sample["timestamp"]), raw_value, numeric_value)
        return anomalies

    def _error_result(
        self,
        spec: _FixedQuery,
        error: str,
        *,
        collected_epoch: float,
        range_start: float,
        range_end: float,
    ) -> dict[str, object]:
        semantic: dict[str, object] = {
            "type": spec.semantic_type,
            "notes": spec.semantic_notes,
        }
        semantic.update(dict(spec.semantic_flags))
        return {
            "query_id": spec.query_id,
            "metric_name": spec.metric_name,
            "status": "ERROR",
            "result_type": "matrix",
            "unit": spec.unit,
            "window": {
                "start": _rfc3339(range_start),
                "end": _rfc3339(range_end),
                "duration_seconds": self.range_seconds,
                "step_seconds": self.step_seconds,
            },
            "latest_range_evaluation_timestamp": None,
            "series": [],
            "sample_count": 0,
            "coverage": self._coverage([], partitioned=spec.partitioned),
            "semantic": semantic,
            "anomalies": [],
            "error": error[:500],
        }

    @staticmethod
    def _cross_query_partition_coverage(
        results: Iterable[dict[str, object]],
    ) -> dict[str, object]:
        kafka_query_ids = {
            "kafka_topic_partition_current_offset",
            "kafka_consumergroup_current_offset",
            "kafka_consumergroup_lag",
        }
        by_query: dict[str, object] = {}
        observed_sets: list[frozenset[str]] = []
        all_queries_observed = True
        for result in results:
            query_id = str(result.get("query_id"))
            if query_id not in kafka_query_ids:
                continue
            coverage = result.get("coverage")
            observed = (
                coverage.get("observed_partition_ids")
                if isinstance(coverage, Mapping)
                else None
            )
            by_query[query_id] = coverage
            if result.get("status") != "OK" or not isinstance(observed, list):
                all_queries_observed = False
                continue
            observed_sets.append(frozenset(str(value) for value in observed))

        mismatch: bool | None
        if not all_queries_observed or len(observed_sets) != len(kafka_query_ids):
            mismatch = None
        else:
            mismatch = len(set(observed_sets)) > 1
        return {
            "by_query": by_query,
            "partition_mismatch": mismatch,
        }


def collect_prometheus(
    base_url: str,
    **collector_options: object,
) -> dict[str, object]:
    """Convenience entry point for the Phase 1 controller."""

    return PrometheusCollector(base_url, **collector_options).collect()
