"""Explicit local-only deployment fault drill; blind read-only diagnosis, then rollback.

Not the calibrated v3 backlog gate or an autonomous remediation agent.
Run with --execute; --watchdog is an internal independent rollback process.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ops_agent.diagnosis_agent import OpenAIResponsesClient, load_openai_configuration
from ops_agent.experiment_validation import require_stalled_requests, validate_diagnosis

CONTEXT = "kind-messaging-ha"
NAMESPACE = "messaging-app"
APP = "messaging-portfolio-local-ha"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def now():
    return datetime.now(timezone.utc).isoformat()


def kube(*args, namespace=NAMESPACE, json_output=True):
    result = subprocess.run([str(ROOT / "tools/kubectl.exe"), "--context", CONTEXT,
        "--request-timeout=15s", "-n", namespace, *args], capture_output=True,
        text=True, encoding="utf-8", timeout=25)
    if result.returncode:
        raise RuntimeError("kubectl failed: " + " ".join(args[:2]))
    return json.loads(result.stdout) if json_output else result.stdout


def patch(kind, name, value, namespace=NAMESPACE):
    return kube("patch", kind, name, "--type=merge", "-p", json.dumps(value),
                namespace=namespace, json_output=False)


def restore(snapshot):
    # Merge patch changes only the fields owned by this experiment.
    patch("deployment", "worker", {"spec": {
        "strategy": {**snapshot["strategy"],
                     "rollingUpdate": snapshot["strategy"].get("rollingUpdate")},
        "template": {"spec": {"containers": snapshot["containers"]}}}})
    kube("rollout", "status", "deployment/worker", "--timeout=15s", json_output=False)


def restore_policy(snapshot):
    patch("application", APP, {"spec": {"syncPolicy": {
        "automated": snapshot["automated"]}}}, namespace="argocd")


def verify_restored(snapshot):
    current = kube("get", "deployment", "worker", "-o", "json")
    app = kube("get", "application", APP, "-o", "json", namespace="argocd")
    if (current["spec"]["strategy"] != snapshot["strategy"]
            or current["spec"]["template"]["spec"]["containers"] != snapshot["containers"]
            or app["spec"].get("syncPolicy", {}).get("automated") != snapshot["automated"]):
        raise ValueError("restored settings do not match snapshot")


def watchdog(directory):
    snapshot = json.loads((directory / "rollback.json").read_text(encoding="utf-8"))
    while time.time() < snapshot["rollback_deadline_epoch"]:
        if (directory / "restored.json").exists():
            return
        time.sleep(2)
    # Keep retrying boundedly if the API is temporarily unavailable.
    for attempt in range(6):
        try:
            restore(snapshot)
            restore_policy(snapshot)
            verify_restored(snapshot)
            save(directory / "watchdog-restored.json", {"at": now(), "fields_verified": True})
            return
        except Exception:
            time.sleep(10)
    save(directory / "watchdog-failed.json", {"at": now(), "manual_restore_required": True})


def pod_projection(item):
    return {"name": item["metadata"]["name"], "uid": item["metadata"]["uid"],
            "created_at": item["metadata"]["creationTimestamp"],
            "phase": item.get("status", {}).get("phase"),
            "conditions": item.get("status", {}).get("conditions", []),
            "containers": [{key: c.get(key) for key in
                ("name", "ready", "restartCount", "state", "lastState")}
                for c in item.get("status", {}).get("containerStatuses", [])]}


def worker_status():
    deployment = kube("get", "deployment", "worker", "-o", "json")
    pods = kube("get", "pods", "-l", "app=worker", "-o", "json")["items"]
    return {"deployment_status": deployment["status"],
            "desired_replicas": deployment["spec"]["replicas"],
            "pods": [pod_projection(p) for p in pods]}


def diagnose(directory, evidence, symptom, client, model, descriptions=None):
    # No injection plan, rollback data, command config, rubric, or causal label here.
    descriptions = descriptions or {
        "get_readiness": "Read API, Kafka reachability and PostgreSQL readiness evidence.",
        "get_worker_status": "Read Worker deployment and pod runtime status.",
        "get_worker_logs": "Read recent Worker container startup output.",
        "get_kafka_lag": "Read Kafka consumer lag from Prometheus.",
    }
    tools = [{"type": "function", "name": name, "description": description,
        "parameters": {"type": "object", "properties": {}, "required": [],
                       "additionalProperties": False}, "strict": True}
        for name, description in descriptions.items()]
    conversation = [{"role": "developer", "content":
        "Diagnose an event-processing service incident using the read-only tools. "
        "API appends events to Kafka before returning 202; Worker consumes Kafka and "
        "persists to PostgreSQL. PostgreSQL is the durable read source. Readiness does "
        "not check Worker replicas. Tools return timestamped snapshots from this incident. "
        "You cannot execute changes. Investigate before concluding. Return a JSON object "
        "with root_cause, evidence_ids (array), alternatives, recommended_action, "
        "verification_steps, uncertainty. Distinguish symptoms from causes. "
        "Do not claim recovery or data loss without evidence. Use Korean."},
        {"role": "user", "content": json.dumps(symptom)}]
    trace = []
    acquired_ids = set()
    for index in range(4):
        payload = {"model": model, "store": False, "input": conversation,
                   "tools": tools, "max_output_tokens": 2500,
                   "tool_choice": "auto" if index < 3 else "none"}
        save(directory / "model" / f"{index}-request.json", payload)
        response = client.create(payload)
        save(directory / "model" / f"{index}-response.json", asdict(response))
        conversation.extend(response.output)
        calls = [o for o in response.output if o.get("type") == "function_call"]
        if not calls:
            decision = validate_diagnosis(response.output_text, acquired_ids | {symptom.get("evidence_id")}, trace)
            if not set(decision["evidence_ids"]) & acquired_ids:
                raise ValueError("diagnosis must cite acquired tool evidence")
            save(directory / "model-result.json", {"model": model,
                "calls": index + 1, "tool_trace": trace, "text": response.output_text, "contract_valid": True})
            return
        if index == 3 or len(trace) + len(calls) > 8:
            raise ValueError("model tool budget exceeded")
        for call in calls:
            name = call["name"]
            if name not in descriptions or name not in evidence or json.loads(call["arguments"]) != {}:
                raise ValueError("invalid model tool request")
            trace.append(name)
            acquired_ids.add(evidence[name]["evidence_id"])
            conversation.append({"type": "function_call_output", "call_id": call["call_id"],
                                 "output": json.dumps(evidence[name])})
    raise ValueError("no model conclusion")


def run(directory):
    key, model = load_openai_configuration(ROOT)
    directory.mkdir(parents=True, exist_ok=False)
    opener = build_opener(ProxyHandler({}))
    token = None

    def http(method, path, body=None, extra=None):
        headers = {"Host": "localhost", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        headers.update(extra or {})
        req = Request("http://127.0.0.1" + path, method=method, headers=headers,
                      data=None if body is None else json.dumps(body).encode())
        try:
            with opener.open(req, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    health = http("GET", "/health/ready")
    if health[0] != 200 or health[1].get("status") != "ready":
        raise ValueError("healthy baseline required")
    deployment = kube("get", "deployment", "worker", "-o", "json")
    app = kube("get", "application", APP, "-o", "json", namespace="argocd")
    if deployment["status"].get("availableReplicas", 0) != deployment["spec"]["replicas"]:
        raise ValueError("fully available worker baseline required")
    # Snapshot contains container configuration; do not send it to the model or print it.
    # Refuse plaintext env values so the persisted rollback record cannot expose secrets.
    containers = deployment["spec"]["template"]["spec"]["containers"]
    if any("value" in env and any(s in env["name"].upper() for s in
            ("PASSWORD", "SECRET", "TOKEN", "KEY")) for c in containers for env in c.get("env", [])):
        raise ValueError("rollback snapshot contains sensitive literal env")
    snapshot = {"strategy": deployment["spec"]["strategy"], "containers": containers,
        "automated": app["spec"].get("syncPolicy", {}).get("automated"),
        "rollback_deadline_epoch": time.time() + 300}
    save(directory / "rollback.json", snapshot)
    save(directory / "plan-private.json", {"at": now(), "model": model,
        "fault": "Worker startup module changed to a nonexistent module (deployment regression)",
        "injection_command": ["python", "-m", "worker.consumer"],
        "acceptance": ["20 accepted events remain unpersisted at least 30 seconds",
                       "model identifies startup module failure from observed logs",
                       "model recommends restoring valid startup configuration",
                       "settings restored and every accepted event persists"],
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "old_v3_activation_gate_used": False, "max_fault_seconds": 300})
    save(directory / "baseline.json", {"at": now(), "health": health,
                                      "worker": worker_status()})
    username, password = "drill" + secrets.token_hex(6), secrets.token_urlsafe(24)
    code, user = http("POST", "/v1/users", {"username": username, "password": password})
    if code not in (200, 201):
        raise ValueError("user creation failed")
    _, login = http("POST", "/v1/auth/login", {"username": username, "password": password})
    token = login["access_token"]
    _, stream = http("POST", "/v1/streams", {"name": "bounded-diagnosis", "member_ids": [user["id"]]})
    accepted = []

    def send_event(sequence):
        code, ack = http("POST", f"/v2/streams/{stream['id']}/events",
            {"event_type": "portfolio.observation.probe", "payload": {"sequence": sequence},
             "metadata": {}}, {"X-Idempotency-Key": secrets.token_hex(16)})
        if code != 202 or ack.get("schema_version") != 2:
            raise ValueError("event acceptance failed")
        return ack["request_id"]

    def states(ids):
        return [{"request_id": rid, "http_status": result[0], "body": result[1]}
            for rid in ids for result in [http("GET", f"/v2/event-requests/{rid}")]]

    baseline_id = send_event(-1)
    time.sleep(3)
    baseline_states = states([baseline_id])
    save(directory / "baseline-event.json", baseline_states)
    if baseline_states[0]["body"].get("status") != "persisted":
        raise ValueError("baseline event did not persist")
    log = (directory / "watchdog.log").open("w", encoding="utf-8")
    guard = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
        "--output-dir", str(directory.resolve()), "--watchdog"], stdout=log, stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log.close()
    failure = None
    try:
        patch("application", APP, {"spec": {"syncPolicy": {"automated": None}}}, namespace="argocd")
        broken = json.loads(json.dumps(containers))
        target = next(c for c in broken if c["name"] == "worker")
        target["command"] = ["python", "-m", "worker.consumer"]
        target.pop("args", None)
        patch("deployment", "worker", {"spec": {"strategy": {"type": "Recreate", "rollingUpdate": None},
            "template": {"spec": {"containers": broken}}}})
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            status = worker_status()
            pods = status["pods"]
            if (pods and not status["deployment_status"].get("availableReplicas", 0)
                    and all(not c["ready"] for p in pods for c in p["containers"])
                    and any(c["restartCount"] > 0 for p in pods for c in p["containers"])):
                break
            time.sleep(2)
        else:
            raise ValueError("startup outage was not observed")
        for i in range(20):
            accepted.append(send_event(i))
            save(directory / "accepted" / f"{i:02d}.json", {"request_id": accepted[-1], "at": now()})
            time.sleep(1)
        time.sleep(30)
        observed = states(accepted)
        symptom = {"evidence_id": "E1", "captured_at": now(), "accepted_count": len(accepted),
            "wait_after_last_accept_seconds": 30, "requests": observed}
        save(directory / "symptom.json", symptom)
        require_stalled_requests(accepted, observed)
        status = worker_status()
        logs = [{"pod": p["name"], "text": kube("logs", p["name"], "-c", "worker",
            "--tail=15", json_output=False)} for p in status["pods"]]
        query = 'kafka_consumergroup_lag{consumergroup="message-worker",topic="message-ingress"}'
        evidence = {
            "get_readiness": {"evidence_id": "E2", "captured_at": now(), "result": http("GET", "/health/ready")},
            "get_worker_status": {"evidence_id": "E3", "captured_at": now(), "result": status},
            "get_worker_logs": {"evidence_id": "E4", "captured_at": now(), "result": logs},
            "get_kafka_lag": {"evidence_id": "E5", "captured_at": now(), "result":
                http("GET", "/prometheus/api/v1/query?" + urlencode({"query": query}))}}
        save(directory / "evidence.json", evidence)
        print(json.dumps({"phase": "fault_observed", "accepted": len(accepted), "at": now()}), flush=True)
        diagnose(directory, evidence, symptom,
                 OpenAIResponsesClient(api_key=key, timeout_seconds=30, max_retries=0), model)
    except Exception as exc:
        failure = type(exc).__name__
        save(directory / "failure.json", {"type": failure, "at": now(),
            "detail": str(exc) if isinstance(exc, (RuntimeError, ValueError)) else "details omitted"})
    finally:
        # Do not mark restored until rollout AND original GitOps policy are verified.
        for attempt in range(6):
            try:
                restore(snapshot)
                restore_policy(snapshot)
                verify_restored(snapshot)
                save(directory / "restored.json", {"at": now(), "fields_verified": True})
                break
            except Exception:
                time.sleep(3)
        else:
            raise RuntimeError("restoration not verified; watchdog remains active")
    deadline = time.monotonic() + 90
    final = states(accepted)
    while any(s["body"].get("status") != "persisted" or not s["body"].get("event_id") for s in final):
        if time.monotonic() >= deadline:
            break
        time.sleep(2)
        final = states(accepted)
    save(directory / "recovery.json", {"at": now(), "requests": final,
        "health": http("GET", "/health/ready"), "worker": worker_status()})
    summary = {"model": model, "accepted": len(accepted),
        "persisted_after_restore": sum(s["body"].get("status") == "persisted" and bool(s["body"].get("event_id")) for s in final),
        "restored": True, "failure_type": failure, "model_output_recorded": (directory / "model-result.json").exists(),
        "model_executed_remediation": False}
    save(directory / "summary.json", summary)
    print(json.dumps(summary), flush=True)
    return int(failure is not None or summary["persisted_after_restore"] != len(accepted))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--execute", action="store_true")
    action.add_argument("--watchdog", action="store_true")
    args = parser.parse_args()
    if args.watchdog:
        watchdog(args.output_dir)
    else:
        raise SystemExit(run(args.output_dir))
