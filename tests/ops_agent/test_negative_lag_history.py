from copy import deepcopy

import pytest

from scripts.audit_negative_lag_history import METRICS, reconcile


def evidence():
    bundle = {"evidence": []}
    raw = {"queries": []}
    for name, values in zip(METRICS, ((100, 110), (102, 110), (-2, 0))):
        labels = {"partition": "0", "topic": "message-ingress"}
        samples = [{"timestamp": i, "value": str(v)} for i, v in enumerate(values)]
        bundle["evidence"].append({"metric": {"name": name, "value": samples},
            "labels": labels, "source_timestamp": "2026-08-16T00:00:00Z"})
        raw["queries"].append({"metric_name": name,
            "series": [{"labels": labels, "samples": deepcopy(samples)}]})
    return bundle, raw


def test_negative_raw_value_preserved_with_arithmetic_context():
    bundle, raw = evidence()
    original = deepcopy(bundle)
    result = reconcile(bundle, raw)
    assert result["negative_points"][0]["exporter_lag"] == -2
    assert result["negative_points"][0]["end_minus_committed"] == -2
    assert not result["offset_decreases_within_range"]
    assert bundle == original


def test_range_mismatch_is_not_repaired():
    bundle, raw = evidence()
    raw["queries"][-1]["series"][0]["samples"][0]["value"] = "0"
    with pytest.raises(ValueError, match="mismatch"):
        reconcile(bundle, raw)


def test_duplicate_raw_series_rejected():
    bundle, raw = evidence()
    raw["queries"][0]["series"] *= 2
    with pytest.raises(ValueError, match="mismatch"):
        reconcile(bundle, raw)


def test_unaligned_offsets_do_not_get_nearest_timestamp_substitution():
    bundle, raw = evidence()
    bundle["evidence"][0]["metric"]["value"][0]["timestamp"] = 0.1
    raw["queries"][0]["series"][0]["samples"][0]["timestamp"] = 0.1
    assert reconcile(bundle, raw)["negative_points"][0]["end_minus_committed"] is None


def test_offset_decrease_is_reported_separately():
    bundle, raw = evidence()
    bundle["evidence"][0]["metric"]["value"][1]["value"] = "90"
    raw["queries"][0]["series"][0]["samples"][1]["value"] = "90"
    assert reconcile(bundle, raw)["offset_decreases_within_range"] == [METRICS[0] + ":0"]
