from math import inf

from portfolio.metrics import event_persist_lag_seconds, queue_wait_seconds


def test_event_persist_lag_histogram_covers_full_backlog_drain_window():
    metric = event_persist_lag_seconds.collect()[0]
    bucket_bounds = [
        float(sample.labels["le"])
        for sample in metric.samples
        if sample.name == "messaging_event_persist_lag_seconds_bucket"
    ]

    assert bucket_bounds == [
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.0,
        5.0,
        10.0,
        30.0,
        60.0,
        120.0,
        300.0,
        600.0,
        900.0,
        1200.0,
        inf,
    ]


def test_queue_wait_metric_help_matches_the_observed_timestamps():
    metric = queue_wait_seconds.collect()[0]

    assert metric.documentation == (
        "Time from API queued_at before Kafka append to Worker handler start in seconds"
    )
