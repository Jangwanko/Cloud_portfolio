from io import StringIO
import json
from types import SimpleNamespace
import pytest
from scripts import verify_postgres_recovery as runner


@pytest.mark.parametrize("pod_count,http_status,expected", [(1, 200, "INCOMPLETE"), (3, 200, "COMPLETE"), (3, 503, "INCOMPLETE")])
def test_runner_retains_identity_and_http_evidence_and_requires_full_coverage(tmp_path, monkeypatch, pod_count, http_status, expected):
    clock = [0.0]
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    from datetime import datetime, timedelta, timezone
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(runner, "now", lambda: (start + timedelta(seconds=clock[0])).isoformat())
    pods = [{"metadata": {"name": f"{runner.ST}-{i}", "uid": f"uid-{i}", "creationTimestamp": "2026-11-01T00:00:00Z"},
        "status": {"containerStatuses": [{"name": "postgresql", "ready": True, "restartCount": 0}]}}
        for i in range(pod_count)]
    monkeypatch.setattr(runner, "kube", lambda *args: {"items": pods})
    class Response(StringIO):
        status = http_status
    monkeypatch.setattr(runner, "build_opener", lambda *args: SimpleNamespace(
        open=lambda *args, **kwargs: Response(json.dumps({"status": "ready"}))))
    output = tmp_path / "verification"
    code = runner.run(output, "uid-0", 0, duration=200)
    assert code == (0 if expected == "COMPLETE" else 1)
    assert json.loads((output / "summary.json").read_text())["status"] == expected
    sample = json.loads((output / "samples/000.json").read_text())
    assert sample["pods"][0]["uid"] == "uid-0"
    assert sample["http_status"] == http_status
