from io import StringIO
import json
from urllib.error import HTTPError
import pytest

from ops_agent.models import EvidenceBundle
from scripts.watch_local_until_error import capture_errors
from scripts import run_local_event_probe as probe
from tests.ops_agent.test_incident_followup_audit import sequence


def test_valid_backlog_or_low_traffic_is_not_an_error():
    for doc in (sequence()[2], sequence()[3]):
        errors, warnings, capture = capture_errors(EvidenceBundle.model_validate(doc))
        assert capture.required_evidence_usable
        assert not errors


def test_invalid_lag_is_observation_error_not_asserted_service_failure():
    doc = sequence()[3]
    item = next(e for e in doc["evidence"] if e["metric"]["name"] == "kafka_consumergroup_lag")
    item["metric"]["value"][0]["value"] = "-1"
    errors, warnings, _ = capture_errors(EvidenceBundle.model_validate(doc))
    assert not errors
    assert "OBSERVATION_QUALITY" in [e["kind"] for e in warnings]


def test_unreachable_readiness_is_warning_not_type_error():
    doc = sequence()[0]
    item = next(e for e in doc["evidence"] if e["metric"]["name"] == "application_readiness_observation")
    item["metric"]["value"]["http_status"] = None
    _, warnings, _ = capture_errors(EvidenceBundle.model_validate(doc))
    assert "READINESS_TRANSPORT_UNAVAILABLE" in {w["kind"] for w in warnings}


def test_duplicate_accepted_ids_fail_and_keep_receipts(tmp_path, monkeypatch, capsys):
    class Response(StringIO):
        def __init__(self, body, status=200):
            super().__init__(json.dumps(body)); self.status = status

    class Opener:
        def open(self, request, timeout):
            path = request.full_url
            if path.endswith('/health/ready'): return Response({'status':'ready'})
            if path.endswith('/v1/users'): return Response({'id':1})
            if path.endswith('/v1/auth/login'): return Response({'access_token':'PRIVATE'})
            if path.endswith('/v1/streams'): return Response({'id':2})
            return Response({'request_id':'same', 'schema_version':2, 'event_type':'portfolio.observation.probe'},202)
    monkeypatch.setattr(probe, 'build_opener', lambda *a: Opener())
    monkeypatch.setattr(probe, 'sleep', lambda _: None)
    assert probe.run(tmp_path/'run', count=3) == 1
    summary = json.loads((tmp_path/'run/summary.json').read_text())
    assert summary['status'] == 'FAILED' and summary['accepted_count'] == 2
    assert len(list((tmp_path/'run/accepted').glob('*.json'))) == 2


@pytest.mark.parametrize("first_status", [200, 404, 503])
def test_stop_signal_stops_new_events_but_checks_already_accepted(tmp_path, monkeypatch, capsys, first_status):
    stop = tmp_path / "STOP"
    events = []
    polls = []

    class Response(StringIO):
        def __init__(self, body, status=200):
            super().__init__(json.dumps(body))
            self.status = status

    class Opener:
        def open(self, request, timeout):
            path = request.full_url
            if path.endswith("/health/ready"):
                return Response({"status": "ready"})
            if path.endswith("/v1/users"):
                return Response({"id": 1})
            if path.endswith("/v1/auth/login"):
                return Response({"access_token": "PRIVATE_TEST_TOKEN"})
            if path.endswith("/v1/streams"):
                return Response({"id": 2})
            if path.endswith("/events"):
                events.append(path)
                stop.touch()
                return Response({"request_id": "req-1", "schema_version": 2,
                    "event_type": "portfolio.observation.probe"}, 202)
            assert path.endswith("/event-requests/req-1")
            polls.append(path)
            if len(polls) == 1 and first_status != 200:
                raise HTTPError(path, first_status, "test", {}, None)
            return Response({"status": "persisted", "event_id": 3})

    monkeypatch.setattr(probe, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(probe, "sleep", lambda seconds: None)
    output = tmp_path / "probe"
    assert probe.run(output, count=3, stop_file=stop) == (1 if first_status == 503 else 0)
    result = json.loads((output / "summary.json").read_text())
    if first_status == 503:
        assert result["status"] == "FAILED" and result["http_status"] == 503
    else:
        assert result["status"] == "STOPPED"
        assert result["accepted_count"] == result["persisted_count"] == len(events) == 1
    assert "PRIVATE_TEST_TOKEN" not in capsys.readouterr().out
    assert all("PRIVATE_TEST_TOKEN" not in p.read_text() for p in output.rglob("*.json"))
