"""Synthetic paired investigation pilot; no cluster I/O or remediation.

Three authored cases, one first attempt each. This is a closed-catalog development
pilot, not a held-out accuracy estimate. Baseline and rubric freeze before API calls.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import re
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ops_agent.diagnosis_agent import OpenAIResponsesClient, load_openai_configuration
from scripts.run_worker_fault_diagnosis import now, save

TOOLS = {
    "worker_status": "Worker desired/ready replicas, container states and restart counts.",
    "worker_logs": "Recent Worker startup/processing logs; unavailable is not a normal result.",
    "db_activity": "PostgreSQL connections, active persistence transactions and blocking sessions.",
    "kafka_progress": "Consumer assignment, committed offset movement and lag samples.",
}
CAUSES = ["STARTUP_FAILURE", "DB_LOCK_CONTENTION", "DB_CONNECTION_LIMIT",
          "KAFKA_CONNECTION", "CAPACITY_PRESSURE", "ABSTAIN"]
SYMPTOM = {"evidence_id": "S0", "synthetic": True,
    "observation": "20 distinct requests returned 202. After 45 seconds their status GETs return 404 Request not found. Consumer lag increased from 100 to 120. Cause is not known."}
PROMPT = """Investigate a synthetic event service case. API appends to Kafka before 202;
Worker persists to PostgreSQL; status GET may return 404 before persistence.
You can request one read-only tool per turn. Unavailable evidence is not evidence of
normal operation. No changes can be executed. Select discriminating checks, revise
your hypotheses when evidence changes, and stop when supported or evidence is exhausted.
Return ONLY JSON with exactly: hypotheses (nonempty array of short strings),
next_tool (available tool name or null), reason (short operational rationale),
decision (null when requesting a tool; otherwise an object with cause_code,
evidence_ids, recommended_action, verification_steps, uncertainty).
Final cause_code must be one of the provided codes. ABSTAIN when the available evidence
cannot discriminate causes. Cite only evidence you have received. A named cause needs
direct supporting evidence; a symptom alone is insufficient. No recovery or data-loss
claims. At most four distinct tools; after that give a final decision. Use Korean prose.
"""


def cases():
    def evidence(status, logs, db, kafka):
        return {name: {"evidence_id": f"E{i}", "synthetic": True, "result": result}
                for i, (name, result) in enumerate(zip(TOOLS, (status, logs, db, kafka)), 1)}
    ready = {"desired": 2, "ready": 2, "restarts": [0, 0]}
    lag = {"assigned_partitions": 3, "lag_samples": [100, 110, 120], "committed_offset_delta": 0}
    unavailable = {"available": False, "error": "collector RBAC denied"}
    return [
        {"id": "case-17", "expected": "STARTUP_FAILURE", "required_evidence": ["E2"],
         "evidence": evidence({"desired": 2, "ready": 0, "state": "CrashLoopBackOff", "restarts": [4, 4]},
             {"available": True, "lines": ["Both Worker containers: /usr/local/bin/python: No module named worker.consumer", "Exit code 1 before consumer initialization."]},
             {"connections": 12, "max_connections": 100, "blocked_worker_transactions": []},
             dict(lag, assigned_partitions=0))},
        {"id": "case-42", "expected": "DB_LOCK_CONTENTION", "required_evidence": ["E2", "E3"],
         "evidence": evidence(ready,
             {"available": True, "lines": ["Kafka partitions assigned; poll succeeded.", "Worker backend pid=211 persistence INSERT still awaiting completion after 40s; inline retry does not advance commit."]},
             {"connections": 12, "max_connections": 100, "worker_pid": 211, "wait_event_type": "Lock",
              "blocking_pid": 910, "blocker_state": "idle in transaction", "blocked_relation": "messages", "wait_seconds": 42}, lag)},
        {"id": "case-83", "expected": "ABSTAIN", "required_evidence": ["E2", "E3"],
         "evidence": evidence(ready, unavailable, unavailable, lag)},
    ]


def validate_step(text, acquired, used):
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {"hypotheses", "next_tool", "reason", "decision"}:
        raise ValueError("invalid step fields")
    if not isinstance(value["hypotheses"], list) or not value["hypotheses"] or any(
            not isinstance(x, str) or not x.strip() for x in value["hypotheses"]):
        raise ValueError("missing hypotheses")
    if not isinstance(value["reason"], str) or not value["reason"].strip():
        raise ValueError("missing check rationale")
    tool = value["next_tool"]
    if tool is not None:
        if tool not in TOOLS or tool in used or len(used) >= 4 or value["decision"] is not None:
            raise ValueError("invalid/repeated tool or budget exceeded")
    else:
        d = value["decision"]
        if not used or not isinstance(d, dict) or set(d) != {"cause_code", "evidence_ids", "recommended_action", "verification_steps", "uncertainty"}:
            raise ValueError("invalid final decision")
        refs = d["evidence_ids"]
        if d["cause_code"] not in CAUSES or not isinstance(refs, list) or not refs or any(not isinstance(r, str) for r in refs):
            raise ValueError("invalid cause or evidence")
        if len(set(refs)) != len(refs) or not set(refs) <= acquired or not set(refs) - {"S0"}:
            raise ValueError("unseen or missing investigation evidence")
        for key in ("recommended_action", "uncertainty"):
            if not isinstance(d[key], str) or not d[key].strip():
                raise ValueError("empty decision field")
        if not isinstance(d["verification_steps"], list) or not d["verification_steps"] or any(
                not isinstance(s, str) or not s.strip() for s in d["verification_steps"]):
            raise ValueError("missing verification")
    return value


def baseline(evidence):
    """Frozen adaptive runbook: status -> logs -> DB activity -> Kafka progress.

    Stop early on a concrete signature; never infer healthy from missing data.
    This explicit authored baseline is not claimed to be the entire ops Runbook.
    """
    used = []
    seen = {}
    for tool in TOOLS:
        used.append(tool)
        seen[tool] = evidence[tool]["result"]
        lines = " ".join(seen.get("worker_logs", {}).get("lines", []))
        if "No module named" in lines and "Exit code 1" in lines:
            return {"cause_code": "STARTUP_FAILURE", "evidence_ids": ["E2"], "tool_trace": used}
        db = seen.get("db_activity", {})
        worker_pids = {int(pid) for pid in re.findall(r"backend pid=(\d+)", lines)}
        if db.get("worker_pid") in worker_pids and db.get("wait_event_type") == "Lock" and db.get("blocking_pid"):
            return {"cause_code": "DB_LOCK_CONTENTION", "evidence_ids": ["E2", "E3"], "tool_trace": used}
    return {"cause_code": "ABSTAIN", "evidence_ids": ["E2", "E3", "E4"], "tool_trace": used}


def score(decision, case):
    return {"cause_matches": decision["cause_code"] == case["expected"],
            "required_evidence_cited": set(case["required_evidence"]) <= set(decision["evidence_ids"]),
            "free_text_quality": "requires human review; not automatically scored"}


def investigate(case, client, model, directory):
    # Explicit public input projection: private label and unqueried evidence excluded.
    conversation = [{"role": "developer", "content": PROMPT}, {"role": "user", "content": json.dumps(
        {"symptom": SYMPTOM, "available_tools": TOOLS, "cause_codes": CAUSES})}]
    used, acquired = [], {"S0"}
    totals = {"input_tokens": 0, "output_tokens": 0, "api_seconds": 0.0, "api_calls": 0}
    for index in range(5):
        payload = {"model": model, "store": False, "input": conversation, "max_output_tokens": 1800}
        save(directory / f"{index}-request.json", payload)
        start = time.monotonic()
        response = client.create(payload)
        elapsed = time.monotonic() - start
        save(directory / f"{index}-response.json", {**asdict(response), "api_seconds": elapsed})
        totals["api_seconds"] += elapsed
        totals["api_calls"] += 1
        totals["input_tokens"] += response.input_tokens or 0
        totals["output_tokens"] += response.output_tokens or 0
        step = validate_step(response.output_text, acquired, used)
        save(directory / f"{index}-checkpoint.json", step)
        if step["next_tool"] is None:
            return {"decision": step["decision"], "tool_trace": used, **totals, **score(step["decision"], case)}
        tool = step["next_tool"]
        used.append(tool)
        acquired.add(case["evidence"][tool]["evidence_id"])
        conversation.extend([{"role": "assistant", "content": response.output_text},
            {"role": "user", "content": json.dumps({"tool": tool, "observation": case["evidence"][tool],
                "remaining_tools": [name for name in TOOLS if name not in used]})}])
    raise ValueError("no final answer within budget")


def run(directory, execute=False):
    directory.mkdir(parents=True, exist_ok=False)
    catalog = cases()
    key, model = load_openai_configuration(ROOT) if execute else (None, "offline-only")
    source = Path(__file__).read_bytes()
    (directory / "runner-source.py").write_bytes(source)
    save(directory / "plan-private.json", {"at": now(), "model": model, "synthetic": True,
        "source_sha256": hashlib.sha256(source).hexdigest(), "cases": catalog, "prompt": PROMPT,
        "max_tools_per_case": 4, "max_api_calls_per_case": 5, "attempts_per_case": 1, "repair": False,
        "metrics": "cause match, required citations, tool count, API wall time and tokens; prose reviewed separately",
        "limitations": "three authored closed-catalog development cases, no live data, no generalization or causal recovery claim"})
    rows = []
    client = OpenAIResponsesClient(api_key=key, timeout_seconds=45, max_retries=0) if execute else None
    for case in catalog:
        fixed = baseline(case["evidence"])
        row = {"case_id": case["id"], "expected": case["expected"], "runbook": {**fixed, **score(fixed, case)}}
        if client:
            try:
                row["model"] = investigate(case, client, model, directory / case["id"])
            except Exception as exc:
                row["model"] = {"status": "FAILED", "error_type": type(exc).__name__,
                    "note": "first failure retained; inspect wire trace; no automatic retry"}
        rows.append(row)
        save(directory / f"{case['id']}-result.json", row)
        print(json.dumps({"case_id": case["id"], "model_status": row.get("model", {}).get("status", "RECORDED")}), flush=True)
    result = {"at": now(), "model": model, "synthetic": True, "first_attempt_only": True,
        "source_sha256": hashlib.sha256(source).hexdigest(), "rows": rows,
        "artifact_sha256": {str(p.relative_to(directory)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}}
    save(directory / "summary.json", result)
    return result


def run_assistance_pilot(directory, execute=False):
    """Two independent runs per authored case; follow-ups keep only past advice."""
    from ops_agent import runbook_assistant as assistant
    directory.mkdir(parents=True, exist_ok=False)
    fixture_path = ROOT / "ops_agent/fixtures/runbook_assistance_v1.json"
    suite = json.loads(fixture_path.read_text(encoding="utf-8"))
    key, model = load_openai_configuration(ROOT) if execute else (None, "offline-only")
    source_paths = [Path(__file__), ROOT / "ops_agent/runbook_assistant.py", fixture_path,
                    ROOT / "ops_agent/runbook_catalog.json", ROOT / "docs/RUNBOOK.md",
                    ROOT / "ops_agent/diagnosis_agent.py"]
    for path in source_paths:
        target = directory / "sources" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    save(directory / "plan-private.json", {"at": now(), "model": model, "provenance": "synthetic",
        "cases": suite["cases"], "independent_repeats": 2, "max_api_calls": 8, "automatic_retry": False,
        "acceptance": "expected applicability, real retrieved sources and current evidence references; prose review separate",
        "limitations": "development pilot, two repeats do not establish reliability or operator time savings",
        "source_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}})
    client = OpenAIResponsesClient(api_key=key, timeout_seconds=45, max_retries=0) if execute else None
    rows = []
    for case in suite["cases"]:
        for repeat in range(2 if execute else 1):
            previous = None
            for index, document in enumerate(case["steps"]):
                destination = directory / case["id"] / str(repeat) / str(index)
                row = {"case_id": case["id"], "repeat": repeat, "step": index}
                try:
                    start = time.monotonic()
                    result = assistant.run(document, case["question"], "synthetic", destination,
                        previous=previous, client=client, model=model)
                    row.update(status=result["status"], elapsed_seconds=time.monotonic()-start)
                    if execute:
                        actual = {a["runbook_id"]: a["applicability"] for a in result["advice"]["runbook_assessments"]}
                        expected = case["expected"][index]
                        row.update(applicability=actual, severity=result["advice"]["impact"]["severity"],
                            expected_applicability_matches=(all(actual.get(k) == v for k,v in expected.items())
                                and (bool(expected) or not actual)), usage=result["usage"],
                            advice=result["advice"], changes=result["changes"])
                        previous = result
                except Exception as exc:
                    row.update(status="FAILED", error_type=type(exc).__name__)
                rows.append(row)
                print(json.dumps({k:row[k] for k in ("case_id", "repeat", "step", "status")}), flush=True)
                if row["status"] == "FAILED":
                    # No fabricated previous record and no retries to hide the failure.
                    break
    comparisons = []
    if execute:
        for case in suite["cases"]:
            for index in range(len(case["steps"])):
                samples = [r for r in rows if r["case_id"] == case["id"] and r["step"] == index and r["status"] == "RECORDED"]
                comparisons.append({"case_id": case["id"], "step": index, "recorded_repeats":len(samples),
                    "same_applicability": len(samples) == 2 and samples[0]["applicability"] == samples[1]["applicability"],
                    "same_severity": len(samples) == 2 and samples[0]["severity"] == samples[1]["severity"]})
    result = {"at":now(), "model":model, "provenance":"synthetic", "rows":rows, "repeat_comparison":comparisons,
        "semantic_review":"not automatically certified", "artifact_sha256":{
            p.relative_to(directory).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}}
    save(directory / "summary.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--execute", action="store_true", help="Make bounded paid API calls using existing configuration")
    parser.add_argument("--mode", choices=["investigation", "runbook-assistance"], default="investigation")
    args = parser.parse_args()
    result = (run_assistance_pilot if args.mode == "runbook-assistance" else run)(args.output_dir, args.execute)
    raise SystemExit(1 if any(r.get("status") == "FAILED" or r.get("expected_applicability_matches") is False
        or r.get("model", {}).get("status") == "FAILED" for r in result["rows"]) else 0)
