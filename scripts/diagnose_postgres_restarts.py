"""Read-only local PostgreSQL restart evidence and bounded blind model diagnosis."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_worker_fault_diagnosis import kube, save, now, pod_projection, diagnose
from ops_agent.diagnosis_agent import load_openai_configuration, OpenAIResponsesClient

ST = "messaging-postgresql-ha-postgresql"


def run(directory):
    key, model = load_openai_configuration(ROOT)
    directory.mkdir(parents=True, exist_ok=False)
    pods = kube("get", "pods", "-l", "app.kubernetes.io/component=postgresql", "-o", "json")["items"]
    sts = kube("get", "statefulset", ST, "-o", "json")
    events = kube("get", "events", "-o", "json")["items"]
    descriptions = {
        "get_pg_pods": "Read PostgreSQL pod runtime states and termination/restart information.",
        "get_pg_events": "Read Kubernetes events concerning these PostgreSQL pods.",
        "get_pg_logs": "Read current and previous PostgreSQL startup logs.",
        "get_pg_configuration": "Read deployed container image, probes, resources and update strategy.",
        "get_probe_implementation": "Read actual probe shell scripts from the running image.",
        "get_change_history": "Read repository configuration change history and StatefulSet revisions.",
    }
    logs = []
    for pod in pods:
        for previous in (False, True):
            try:
                value = kube("logs", pod["metadata"]["name"], "--tail=65",
                             *(["--previous"] if previous else []), json_output=False)
                value = re.sub(r"\x1b\[[0-9;]*m", "", value)
            except RuntimeError:
                value = "UNAVAILABLE"
            logs.append({"pod": pod["metadata"]["name"], "previous": previous, "text": value})
    implementation = {}
    for name in ("liveness-probe.sh", "readiness-probe.sh"):
        implementation[name] = kube("exec", ST + "-0", "--", "cat", "/" + name, json_output=False)
    configuration = {"updateStrategy": sts["spec"].get("updateStrategy"),
        "podManagementPolicy": sts["spec"].get("podManagementPolicy"),
        "containers": [{k: c.get(k) for k in ("name", "image", "startupProbe", "livenessProbe",
            "readinessProbe", "resources")} for c in sts["spec"]["template"]["spec"]["containers"]]}
    history = subprocess.run(["git", "log", "-6", "--format=%h %aI %s", "--",
        "k8s/values/postgresql-ha-values.yaml"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    raw = {
        "get_pg_pods": [pod_projection(p) for p in pods],
        "get_pg_events": [{k: e.get(k) for k in ("reason", "message", "count", "firstTimestamp", "lastTimestamp", "involvedObject")}
            for e in events if e.get("involvedObject", {}).get("name", "").startswith(ST)],
        "get_pg_logs": logs, "get_pg_configuration": configuration,
        "get_probe_implementation": implementation,
        "get_change_history": {"git_log": history, "currentRevision": sts["status"].get("currentRevision"),
            "updateRevision": sts["status"].get("updateRevision"),
            "source_values": (ROOT / "k8s/values/postgresql-ha-values.yaml").read_text(encoding="utf-8"),
            "warning": "Repository history is not proof that each revision was deployed."}}
    evidence = {name: {"evidence_id": f"P{i}", "captured_at": now(), "result": value}
                for i, (name, value) in enumerate(raw.items(), 1)}
    save(directory / "evidence.json", evidence)
    save(directory / "plan.json", {"model": model, "read_only": True, "at": now(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "assessment": "First answer preserved; compare proposed cause/remedy to actual evidence and measured recovery."})
    symptom = {"observation": "PostgreSQL replicas have repeated restarts after local Docker restart. Investigate the cause and propose a minimal safe correction. Do not execute changes.",
               "evidence_id": "P0", "captured_at": now()}
    diagnose(directory, evidence, symptom,
        OpenAIResponsesClient(api_key=key, timeout_seconds=45, max_retries=0), model, descriptions)
    print(json.dumps({"model": model, "output_dir": str(directory), "status": "diagnosis_recorded"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    run(parser.parse_args().output_dir)
