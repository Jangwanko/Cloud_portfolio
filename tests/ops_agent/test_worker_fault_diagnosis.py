import json
from types import SimpleNamespace
import pytest

from scripts import run_worker_fault_diagnosis as drill
from ops_agent.diagnosis_agent import ModelResponse


def test_restore_only_changes_original_template_strategy_and_policy(monkeypatch):
    patches = []
    monkeypatch.setattr(drill, "patch", lambda *a, **kw: patches.append((a, kw)))
    monkeypatch.setattr(drill, "kube", lambda *a, **kw: "rolled out")
    snapshot = {"strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": "25%"}},
                "containers": [{"name": "worker", "command": ["python", "-m", "worker.main"]}],
                "automated": {"selfHeal": True, "prune": True}}
    drill.restore(snapshot)
    drill.restore_policy(snapshot)
    spec = patches[0][0][2]["spec"]
    assert set(spec) == {"strategy", "template"}
    assert spec["template"]["spec"]["containers"] == snapshot["containers"]
    assert patches[1][0][2]["spec"]["syncPolicy"]["automated"] == snapshot["automated"]


def test_blind_tools_exclude_private_plan_and_reject_arbitrary_command(tmp_path):
    (tmp_path / "plan-private.json").write_text('{"secret_truth":"DO_NOT_SEND"}')
    requests = []

    def create(payload):
        requests.append(json.loads(json.dumps(payload)))
        return ModelResponse("r1", [{"type": "function_call", "name": "get_worker_logs",
            "arguments": "{}", "call_id": "c1"}], None, 1, 1, 2) if len(requests) == 1 else ModelResponse(
                "r2", [], json.dumps({"root_cause":"startup failure", "evidence_ids":["E4"],
                    "alternatives":["other cause unconfirmed"],"recommended_action":"inspect command",
                    "verification_steps":["check process ready"],"uncertainty":"image contents unknown"}), 1, 1, 2)

    drill.diagnose(tmp_path, {"get_worker_logs": {"evidence_id": "E4", "result": "actual log"}},
                   {"accepted_count": 20}, SimpleNamespace(create=create), "gpt-test")
    wire = json.dumps(requests)
    assert "DO_NOT_SEND" not in wire
    assert "worker.consumer" not in wire
    assert "actual log" in wire
    assert {t["name"] for t in requests[0]["tools"]} == {
        "get_readiness", "get_worker_status", "get_worker_logs", "get_kafka_lag"}
    assert all(t["parameters"]["properties"] == {} for t in requests[0]["tools"])


def test_watchdog_does_not_patch_when_restore_already_confirmed(tmp_path, monkeypatch):
    (tmp_path / "rollback.json").write_text('{"rollback_deadline_epoch":9999999999}')
    (tmp_path / "restored.json").write_text('{}')
    monkeypatch.setattr(drill, "restore", lambda _: (_ for _ in ()).throw(AssertionError("unexpected mutation")))
    drill.watchdog(tmp_path)


def test_model_cannot_execute_an_arbitrary_tool(tmp_path):
    client = SimpleNamespace(create=lambda _: ModelResponse("r1", [{"type": "function_call",
        "name": "kubectl_delete", "arguments": "{}", "call_id": "c1"}], None, 1, 1, 2))
    with pytest.raises(ValueError, match="invalid model tool request"):
        drill.diagnose(tmp_path, {}, {}, client, "gpt-test")


def test_custom_tool_registry_cannot_expose_unadvertised_evidence(tmp_path):
    client = SimpleNamespace(create=lambda _: ModelResponse("r1", [{"type": "function_call",
        "name": "private_metadata", "arguments": "{}", "call_id": "c1"}], None, 1, 1, 2))
    with pytest.raises(ValueError, match="invalid model tool request"):
        drill.diagnose(tmp_path, {"private_metadata": "DO_NOT_SEND"}, {}, client, "gpt-test",
                       descriptions={"get_pg_pods": "Read PostgreSQL status"})
    assert "DO_NOT_SEND" not in (tmp_path / "model/0-request.json").read_text()


def test_watchdog_retries_restore_before_marking_success(tmp_path, monkeypatch):
    (tmp_path / "rollback.json").write_text('{"rollback_deadline_epoch":0}')
    attempts = []

    def restore(_):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("temporary API failure")

    monkeypatch.setattr(drill, "restore", restore)
    monkeypatch.setattr(drill, "restore_policy", lambda _: None)
    monkeypatch.setattr(drill, "verify_restored", lambda _: None)
    monkeypatch.setattr(drill.time, "sleep", lambda _: None)
    drill.watchdog(tmp_path)
    assert len(attempts) == 2
    assert (tmp_path / "watchdog-restored.json").exists()


def test_empty_model_response_is_not_a_completed_diagnosis(tmp_path):
    client = SimpleNamespace(create=lambda _: ModelResponse("r1", [], None, 0, 0, 0))
    with pytest.raises(ValueError, match="investigation"):
        drill.diagnose(tmp_path, {}, {}, client, "gpt-test")
    assert not (tmp_path / "model-result.json").exists()


def test_watchdog_does_not_claim_success_when_field_verification_fails(tmp_path, monkeypatch):
    (tmp_path / "rollback.json").write_text('{"rollback_deadline_epoch":0}')
    monkeypatch.setattr(drill, "restore", lambda _: None)
    monkeypatch.setattr(drill, "restore_policy", lambda _: None)
    monkeypatch.setattr(drill, "verify_restored", lambda _: (_ for _ in ()).throw(ValueError("drift")))
    monkeypatch.setattr(drill.time, "sleep", lambda _: None)
    drill.watchdog(tmp_path)
    assert (tmp_path / "watchdog-failed.json").exists()
    assert not (tmp_path / "watchdog-restored.json").exists()


def test_restore_verifier_rejects_config_drift(monkeypatch):
    snapshot = {"strategy": {}, "containers": [], "automated": None}
    monkeypatch.setattr(drill, "kube", lambda *a, **k: {"spec": {"strategy": {}, "template": {"spec": {
        "containers": [{"name": "unexpected"}]}}, "syncPolicy": {}}})
    with pytest.raises(ValueError, match="do not match"):
        drill.verify_restored(snapshot)
