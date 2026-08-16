import inspect
import json
import threading
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, Request

import pytest

from ops_agent.collectors.prometheus import PrometheusCollector


NOW = 1_725_000_060.0


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]


def matrix(*series):
    return {
        "status": "success",
        "data": {"resultType": "matrix", "result": list(series)},
    }


def vector(*series):
    return {
        "status": "success",
        "data": {"resultType": "vector", "result": list(series)},
    }


def metric_series(metric_name, values, **labels):
    return {
        "metric": {"__name__": metric_name, **labels},
        "values": values,
    }


def rfc3339(epoch_seconds):
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def query_result(bundle, query_id):
    return next(item for item in bundle["queries"] if item["query_id"] == query_id)


def fixed_payload(query):
    if query.startswith("kafka_topic_partition_current_offset"):
        return matrix(
            metric_series(
                "kafka_topic_partition_current_offset",
                [[NOW - 60, "100"], [NOW - 5, "110"]],
                topic="message-ingress",
                partition="0",
            )
        )
    if query.startswith("kafka_consumergroup_current_offset"):
        return matrix(
            metric_series(
                "kafka_consumergroup_current_offset",
                [[NOW - 60, "90"], [NOW - 5, "105"]],
                topic="message-ingress",
                consumergroup="message-worker",
                partition="0",
            )
        )
    if query.startswith("kafka_consumergroup_lag"):
        return matrix(
            metric_series(
                "kafka_consumergroup_lag",
                [[NOW - 60, "10"], [NOW - 5, "5"]],
                topic="message-ingress",
                consumergroup="message-worker",
                partition="0",
            )
        )
    if query.startswith("messaging_worker_processed_total"):
        return matrix(
            metric_series(
                "messaging_worker_processed_total",
                [[NOW - 60, "20"], [NOW - 5, "25"]],
                job="worker",
                result="success",
            )
        )
    if "messaging_queue_wait_seconds_" in query:
        return matrix(
            metric_series(
                "messaging_queue_wait_seconds_count",
                [[NOW - 60, "20"], [NOW - 5, "25"]],
                job="worker",
            )
        )
    if "messaging_event_persist_lag_seconds_" in query:
        return matrix(
            metric_series(
                "messaging_event_persist_lag_seconds_count",
                [[NOW - 60, "20"], [NOW - 5, "25"]],
                job="worker",
            )
        )
    if "messaging_worker_stage_latency_seconds_" in query:
        return matrix(
            metric_series(
                "messaging_worker_stage_latency_seconds_count",
                [[NOW - 60, "20"], [NOW - 5, "25"]],
                job="worker",
                stage="db_persist",
            )
        )
    raise AssertionError(f"unexpected fixed query: {query}")


def fixed_response(
    request,
    *,
    payload_factory=fixed_payload,
    source_timestamp=NOW - 7,
):
    parsed = urlsplit(request.full_url)
    query = parse_qs(parsed.query)["query"][0]
    if parsed.path.endswith("/query_range"):
        return FakeResponse(payload_factory(query))
    assert parsed.path.endswith("/query")
    assert "label_replace(timestamp(" in query
    assert "timestamp(label_replace(" not in query
    if "kafka_topic_partition_current_offset" in query:
        source_query = 'kafka_topic_partition_current_offset{topic="message-ingress"}'
    elif "kafka_consumergroup_current_offset" in query:
        source_query = (
            'kafka_consumergroup_current_offset{consumergroup="message-worker",'
            'topic="message-ingress"}'
        )
    elif "kafka_consumergroup_lag" in query:
        source_query = (
            'kafka_consumergroup_lag{consumergroup="message-worker",'
            'topic="message-ingress"}'
        )
    elif "messaging_worker_processed_total" in query:
        source_query = (
            'messaging_worker_processed_total{job="worker",'
            'result=~"success|rejected|dlq"}'
        )
    elif "messaging_queue_wait_seconds_" in query:
        source_query = (
            '{__name__=~"messaging_queue_wait_seconds_(bucket|count|sum)",'
            'job="worker"}'
        )
    elif "messaging_event_persist_lag_seconds_" in query:
        source_query = (
            '{__name__=~"messaging_event_persist_lag_seconds_(bucket|count|sum)",'
            'job="worker"}'
        )
    elif "messaging_worker_stage_latency_seconds_" in query:
        source_query = (
            '{__name__=~"messaging_worker_stage_latency_seconds_(bucket|count|sum)",'
            'job="worker",stage="db_persist"}'
        )
    else:
        raise AssertionError(f"unexpected freshness query: {query}")
    range_payload = payload_factory(source_query)
    timestamp_series = [
        {
            "metric": {
                **{
                    key: value
                    for key, value in item["metric"].items()
                    if key != "__name__"
                },
                "ops_metric_component": item["metric"]["__name__"],
            },
            "value": [NOW, str(source_timestamp)],
        }
        for item in range_payload["data"]["result"]
    ]
    return FakeResponse(vector(*timestamp_series))


def test_collector_uses_only_fixed_range_queries_and_separates_window_from_age():
    requests = []

    def opener(request, timeout):
        assert timeout == 2.0
        requests.append(request)
        return fixed_response(request)

    collector = PrometheusCollector(
        "http://prometheus:9090",
        expected_partition_ids=[0],
        timeout_seconds=2,
        host_header="localhost",
        opener=opener,
        clock=lambda: NOW,
    )
    bundle = collector.collect()

    assert bundle["status"] == "OK"
    assert bundle["partial"] is False
    assert len(requests) == 14
    range_requests = [
        request
        for request in requests
        if urlsplit(request.full_url).path == "/api/v1/query_range"
    ]
    freshness_requests = [
        request
        for request in requests
        if urlsplit(request.full_url).path == "/api/v1/query"
    ]
    assert len(range_requests) == len(freshness_requests) == 7
    assert all(request.get_header("Host") == "localhost" for request in requests)
    for request in range_requests:
        params = parse_qs(urlsplit(request.full_url).query)
        assert params["start"] == [f"{NOW - 60:.3f}"]
        assert params["end"] == [f"{NOW:.3f}"]
        assert params["step"] == ["5"]
    worker_query = next(
        parse_qs(urlsplit(request.full_url).query)["query"][0]
        for request in range_requests
        if "messaging_worker_processed_total" in request.full_url
    )
    assert 'result=~"success|rejected|dlq"' in worker_query
    assert "failure" not in worker_query
    for request in freshness_requests:
        params = parse_qs(urlsplit(request.full_url).query)
        assert "label_replace(timestamp(" in params["query"][0]
        assert "timestamp(label_replace(" not in params["query"][0]
        assert params["time"] == [f"{NOW:.6f}"]

    topic_offset = query_result(bundle, "kafka_topic_partition_current_offset")
    assert topic_offset["window"]["duration_seconds"] == 60
    assert topic_offset["latest_range_evaluation_timestamp"] == rfc3339(NOW - 5)
    assert topic_offset["freshness"]["source_timestamp"] == rfc3339(NOW - 7)
    assert topic_offset["freshness"]["age_seconds"] == 7.0
    assert topic_offset["freshness"]["status"] == "FRESH"
    assert topic_offset["freshness"]["basis"] == "prometheus_timestamp_function"
    assert topic_offset["freshness"]["coverage"]["labels_match_range"] is True
    assert "query" not in topic_offset
    assert "promql" not in topic_offset
    assert "query" not in inspect.signature(collector.collect).parameters


def test_fixed_queries_start_concurrently_to_bound_source_timeout():
    range_barrier = threading.Barrier(7)

    def opener(request, timeout):
        del timeout
        if urlsplit(request.full_url).path.endswith("/query_range"):
            range_barrier.wait(timeout=1)
        return fixed_response(request)

    result = PrometheusCollector(
        "http://prometheus:9090",
        expected_partition_ids=[0],
        opener=opener,
        clock=lambda: NOW,
    ).collect()

    assert result["status"] == "OK"


def test_prometheus_response_has_a_total_wall_deadline() -> None:
    release = threading.Event()

    class SlowResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            release.wait(1)
            return b"{}"

    def opener(_request, timeout):
        assert timeout == 0.01
        return SlowResponse()

    result = PrometheusCollector(
        "http://prometheus:9090",
        expected_partition_ids=[0],
        timeout_seconds=0.01,
        opener=opener,
        clock=lambda: NOW,
    ).collect()
    release.set()

    assert result["status"] == "ERROR"
    assert len(result["queries"]) == 7
    assert all(query["status"] == "ERROR" for query in result["queries"])
    assert all(
        "total time limit" in query["error"] for query in result["queries"]
    )


def test_histogram_freshness_requires_every_metric_component() -> None:
    def payload_factory(query):
        if "messaging_event_persist_lag_seconds_" in query:
            return matrix(
                metric_series(
                    "messaging_event_persist_lag_seconds_count",
                    [[NOW - 5, "25"]],
                    job="worker",
                ),
                metric_series(
                    "messaging_event_persist_lag_seconds_sum",
                    [[NOW - 5, "10"]],
                    job="worker",
                ),
            )
        return fixed_payload(query)

    def opener(request, timeout):
        del timeout
        parsed = urlsplit(request.full_url)
        query = parse_qs(parsed.query)["query"][0]
        response = fixed_response(request, payload_factory=payload_factory)
        if parsed.path.endswith("/query") and "event_persist_lag" in query:
            payload = json.loads(response.body)
            payload["data"]["result"] = payload["data"]["result"][:1]
            return FakeResponse(payload)
        return response

    result = PrometheusCollector(
        "http://prometheus:9090",
        expected_partition_ids=[0],
        opener=opener,
        clock=lambda: NOW,
    ).collect()
    freshness = query_result(
        result, "messaging_event_persist_lag_seconds"
    )["freshness"]

    assert freshness["coverage"] == {
        "range_series_count": 2,
        "freshness_series_count": 1,
        "labels_match_range": False,
    }
    assert freshness["status"] == "UNKNOWN"
    assert freshness["source_timestamp"] is None


def test_raw_negative_values_partial_coverage_and_offset_decrease_are_preserved():
    def payload_factory(query):
        if query.startswith("kafka_topic_partition_current_offset"):
            return matrix(
                metric_series(
                    "kafka_topic_partition_current_offset",
                    [[NOW - 60, "10"], [NOW - 5, "9"]],
                    topic="message-ingress",
                    partition="0",
                ),
                metric_series(
                    "kafka_topic_partition_current_offset",
                    [[NOW - 60, "5"], [NOW - 5, "7"]],
                    topic="message-ingress",
                    partition="1",
                ),
            )
        elif query.startswith("kafka_consumergroup_current_offset"):
            return matrix(
                metric_series(
                    "kafka_consumergroup_current_offset",
                    [[NOW - 60, "-1"], [NOW - 5, "-1"]],
                    topic="message-ingress",
                    consumergroup="message-worker",
                    partition="0",
                )
            )
        elif query.startswith("kafka_consumergroup_lag"):
            return matrix(
                metric_series(
                    "kafka_consumergroup_lag",
                    [[NOW - 60, "-1"], [NOW - 5, "4"]],
                    topic="message-ingress",
                    consumergroup="message-worker",
                    partition="0",
                ),
                metric_series(
                    "kafka_consumergroup_lag",
                    [[NOW - 60, "3"], [NOW - 5, "2"]],
                    topic="message-ingress",
                    consumergroup="message-worker",
                    partition="1",
                ),
            )
        return fixed_payload(query)

    def opener(request, timeout):
        del timeout
        return fixed_response(request, payload_factory=payload_factory)

    bundle = PrometheusCollector(
        "http://prometheus:9090",
        expected_partition_ids=[0, 1, 2],
        opener=opener,
        clock=lambda: NOW,
    ).collect()

    topic_offset = query_result(bundle, "kafka_topic_partition_current_offset")
    committed = query_result(bundle, "kafka_consumergroup_current_offset")
    lag = query_result(bundle, "kafka_consumergroup_lag")

    assert topic_offset["series"][0]["samples"][1]["value"] == "9"
    assert topic_offset["coverage"]["missing_partition_ids"] == ["2"]
    assert topic_offset["coverage"]["complete"] is False
    assert any(
        anomaly["type"] == "offset_decrease"
        and anomaly["previous_value"] == "10"
        and anomaly["value"] == "9"
        for anomaly in topic_offset["anomalies"]
    )
    assert committed["series"][0]["samples"][0]["value"] == "-1"
    assert any(anomaly["value"] == "-1" for anomaly in committed["anomalies"])
    assert lag["series"][0]["samples"][0]["value"] == "-1"
    assert bundle["partition_coverage"]["partition_mismatch"] is True


def test_empty_series_is_missing_and_is_not_represented_as_zero():
    def payload_factory(query):
        if query.startswith("kafka_consumergroup_current_offset"):
            return matrix()
        return fixed_payload(query)

    def opener(request, timeout):
        del timeout
        return fixed_response(request, payload_factory=payload_factory)

    bundle = PrometheusCollector(
        "http://prometheus:9090",
        expected_partition_ids=[0],
        opener=opener,
        clock=lambda: NOW,
    ).collect()
    committed = query_result(bundle, "kafka_consumergroup_current_offset")

    assert bundle["status"] == "OK"
    assert bundle["partial"] is True
    assert committed["status"] == "MISSING"
    assert committed["series"] == []
    assert committed["sample_count"] == 0
    assert committed["latest_range_evaluation_timestamp"] is None
    assert committed["freshness"]["query_status"] == "MISSING"
    assert committed["freshness"]["status"] == "UNKNOWN"
    assert committed["freshness"]["source_timestamp"] is None
    assert committed["freshness"]["age_seconds"] is None
    assert bundle["partition_coverage"]["partition_mismatch"] is None


def test_one_query_failure_is_structured_without_discarding_other_results():
    def opener(request, timeout):
        del timeout
        path = urlsplit(request.full_url).path
        query = parse_qs(urlsplit(request.full_url).query)["query"][0]
        if path.endswith("/query_range") and query.startswith("kafka_consumergroup_lag"):
            raise URLError("connection refused")
        return fixed_response(request)

    bundle = PrometheusCollector(
        "http://prometheus:9090",
        expected_partition_ids=[0],
        opener=opener,
        clock=lambda: NOW,
    ).collect()
    lag = query_result(bundle, "kafka_consumergroup_lag")

    assert bundle["status"] == "OK"
    assert bundle["partial"] is True
    assert lag["status"] == "ERROR"
    assert lag["series"] == []
    assert "connection refused" in lag["error"]
    assert lag["freshness"]["query_status"] == "OK"
    assert lag["freshness"]["status"] == "FRESH"
    assert query_result(bundle, "kafka_topic_partition_current_offset")["status"] == "OK"


def test_timestamp_query_marks_stale_without_using_range_evaluation_timestamp():
    def opener(request, timeout):
        del timeout
        return fixed_response(request, source_timestamp=NOW - 30)

    bundle = PrometheusCollector(
        "http://prometheus:9090",
        expected_partition_ids=[0],
        sample_max_age_seconds=15,
        opener=opener,
        clock=lambda: NOW,
    ).collect()
    lag = query_result(bundle, "kafka_consumergroup_lag")

    assert bundle["status"] == "OK"
    assert bundle["partial"] is False
    assert lag["latest_range_evaluation_timestamp"] == rfc3339(NOW - 5)
    assert lag["freshness"]["query_status"] == "OK"
    assert lag["freshness"]["status"] == "STALE"
    assert lag["freshness"]["source_timestamp"] == rfc3339(NOW - 30)
    assert lag["freshness"]["age_seconds"] == 30.0
    assert lag["freshness"]["max_age_seconds"] == 15.0
    assert lag["freshness"]["series"][0]["source_timestamp_value"] == str(
        NOW - 30
    )


def test_submillisecond_query_time_rounding_does_not_make_freshness_unknown():
    source_timestamp = NOW + 0.0005

    def opener(request, timeout):
        del timeout
        return fixed_response(request, source_timestamp=source_timestamp)

    bundle = PrometheusCollector(
        "http://prometheus:9090",
        expected_partition_ids=[0],
        opener=opener,
        clock=lambda: NOW,
    ).collect()
    lag = query_result(bundle, "kafka_consumergroup_lag")

    assert lag["freshness"]["status"] == "FRESH"
    assert lag["freshness"]["age_seconds"] == 0.0
    assert lag["freshness"]["error"] is None


def test_freshness_query_failure_keeps_raw_values_and_marks_collection_partial():
    def opener(request, timeout):
        del timeout
        path = urlsplit(request.full_url).path
        query = parse_qs(urlsplit(request.full_url).query)["query"][0]
        if path.endswith("/query") and "kafka_consumergroup_lag" in query:
            raise URLError("timestamp query unavailable")
        return fixed_response(request)

    bundle = PrometheusCollector(
        "http://prometheus:9090",
        expected_partition_ids=[0],
        opener=opener,
        clock=lambda: NOW,
    ).collect()
    lag = query_result(bundle, "kafka_consumergroup_lag")

    assert bundle["status"] == "OK"
    assert bundle["partial"] is True
    assert lag["status"] == "OK"
    assert lag["series"][0]["samples"][-1]["value"] == "5"
    assert lag["freshness"]["query_status"] == "ERROR"
    assert lag["freshness"]["status"] == "UNKNOWN"
    assert lag["freshness"]["source_timestamp"] is None
    assert lag["freshness"]["age_seconds"] is None
    assert "timestamp query unavailable" in lag["freshness"]["error"]


def test_all_raw_query_errors_are_failed_not_partial_even_if_timestamp_queries_work():
    def opener(request, timeout):
        del timeout
        if urlsplit(request.full_url).path.endswith("/query_range"):
            raise URLError("range endpoint unavailable")
        return fixed_response(request)

    bundle = PrometheusCollector(
        "http://prometheus:9090",
        expected_partition_ids=[0],
        opener=opener,
        clock=lambda: NOW,
    ).collect()

    assert bundle["status"] == "ERROR"
    assert bundle["partial"] is False
    assert all(query["status"] == "ERROR" for query in bundle["queries"])
    assert all(
        query["freshness"]["query_status"] == "OK" for query in bundle["queries"]
    )


def test_worker_counter_and_latency_semantics_cannot_be_read_as_commit_rate():
    def opener(request, timeout):
        del timeout
        return fixed_response(request)

    bundle = PrometheusCollector(
        "http://prometheus:9090",
        expected_partition_ids=[0],
        opener=opener,
        clock=lambda: NOW,
    ).collect()

    terminal = query_result(bundle, "messaging_worker_processed_total")
    observed_lag = query_result(bundle, "messaging_event_persist_lag_seconds")
    persist_stage = query_result(
        bundle, "messaging_worker_db_persist_stage_latency_seconds"
    )

    assert terminal["semantic"] == {
        "type": "worker_terminal_processing_counter",
        "notes": (
            "Counts success, rejected, and DLQ outcomes after Kafka offset commit. "
            "The failure outcome is excluded because it is rewound without offset "
            "commit. This is not PostgreSQL commit or insert rate."
        ),
        "is_db_commit_rate": False,
    }
    assert observed_lag["semantic"]["type"] == (
        "api_queued_at_to_post_commit_observed_lag_histogram"
    )
    assert observed_lag["semantic"]["is_isolated_postgresql_commit_latency"] is False
    assert persist_stage["semantic"]["type"] == "persist_stage_latency_histogram"
    assert persist_stage["semantic"]["includes_transaction_commit"] is False


def test_sensitive_unexpected_metric_labels_are_redacted():
    def payload_factory(query):
        payload = fixed_payload(query)
        if query.startswith("messaging_worker_processed_total"):
            payload["data"]["result"][0]["metric"]["access_token"] = "do-not-store"
        return payload

    def opener(request, timeout):
        del timeout
        return fixed_response(request, payload_factory=payload_factory)

    bundle = PrometheusCollector(
        "http://prometheus:9090",
        expected_partition_ids=[0],
        opener=opener,
        clock=lambda: NOW,
    ).collect()
    terminal = query_result(bundle, "messaging_worker_processed_total")

    assert terminal["series"][0]["labels"]["access_token"] == "[REDACTED]"
    assert "do-not-store" not in json.dumps(bundle)


@pytest.mark.parametrize(
    "base_url",
    [
        "prometheus:9090",
        "ftp://prometheus:9090",
        "http://user:pass@prometheus:9090",
        "http://prometheus:9090/prometheus?next=private",
        "http://prometheus:9090/prometheus#private",
    ],
)
def test_collector_rejects_unsafe_or_unsupported_base_urls(base_url):
    with pytest.raises(ValueError):
        PrometheusCollector(base_url)


def test_default_prometheus_http_handler_does_not_follow_redirects():
    from ops_agent.collectors import prometheus as prometheus_collector

    handler = prometheus_collector._NoRedirectHandler()

    assert handler.redirect_request(None, None, 302, "Found", {}, "http://invalid") is None


def test_default_prometheus_http_handler_disables_ambient_proxies(monkeypatch):
    from ops_agent.collectors import prometheus as prometheus_collector

    captured = []

    class Opener:
        def open(self, request, timeout):
            return request, timeout

    def build(*handlers):
        captured.extend(handlers)
        return Opener()

    monkeypatch.setattr(prometheus_collector, "build_opener", build)
    request = Request("http://127.0.0.1/prometheus/api/v1/query")

    assert prometheus_collector._open_no_redirect(request, timeout=1) == (request, 1)
    assert any(
        isinstance(handler, ProxyHandler) and handler.proxies == {}
        for handler in captured
    )
