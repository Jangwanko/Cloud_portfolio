import argparse
import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


PASSWORD = "Password123!"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def http_json(method, url, token=None, payload=None, timeout=10, host_header=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if host_header:
        headers["Host"] = host_header
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {method} {url}: {body}") from exc


def run_command(args, check=True, capture=True):
    kwargs = {
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    result = subprocess.run(args, **kwargs)
    if check and result.returncode != 0:
        raise RuntimeError(
            "Command failed: {0}\nstdout:\n{1}\nstderr:\n{2}".format(
                " ".join(args),
                result.stdout or "",
                result.stderr or "",
            )
        )
    return result


def kubectl(namespace, *args, check=True):
    return run_command(["kubectl", "-n", namespace, *args], check=check)


def wait_ready(base_url, timeout_sec, host_header=None):
    deadline = time.monotonic() + timeout_sec
    last_error = None
    while time.monotonic() < deadline:
        try:
            health = http_json("GET", f"{base_url}/health/ready", timeout=5, host_header=host_header)
            if health.get("status") == "ready":
                return health
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for API ready. last_error={last_error}")


def resolve_workload(namespace, name):
    for kind in ("deployment", "statefulset"):
        result = kubectl(namespace, "get", kind, name, "--ignore-not-found", "-o", "name", check=False)
        value = (result.stdout or "").strip()
        if value:
            return value
    raise RuntimeError(f"Workload not found: {name}")


def get_workload_replicas(namespace, workload_ref):
    result = kubectl(namespace, "get", workload_ref, "-o", "jsonpath={.spec.replicas}")
    value = (result.stdout or "").strip()
    return int(value) if value else 1


def scale_workload(namespace, workload_ref, replicas):
    kubectl(namespace, "scale", workload_ref, f"--replicas={replicas}")


def wait_rollout(namespace, workload_ref, timeout_sec):
    kubectl(namespace, "rollout", "status", workload_ref, f"--timeout={timeout_sec}s")


def wait_db_query_ready(namespace, api_deployment, timeout_sec):
    code = (
        "from portfolio.db import get_conn;"
        "ctx=get_conn();"
        "conn=ctx.__enter__();"
        "cur=conn.cursor();"
        "cur.execute('SELECT 1');"
        "cur.fetchone();"
        "conn.close()"
    )
    deadline = time.monotonic() + timeout_sec
    successes = 0
    while time.monotonic() < deadline:
        result = kubectl(
            namespace,
            "exec",
            f"deploy/{api_deployment}",
            "--",
            "python",
            "-c",
            code,
            check=False,
        )
        if result.returncode == 0:
            successes += 1
            if successes >= 3:
                return
            time.sleep(2)
            continue
        successes = 0
        time.sleep(3)
    raise RuntimeError("Timed out waiting for DB query readiness")


def run_migrations(namespace, api_deployment):
    code = "from portfolio.db import run_alembic_migrations; run_alembic_migrations()"
    kubectl(namespace, "exec", f"deploy/{api_deployment}", "--", "python", "-c", code)


def query_persisted_events(namespace, api_deployment, stream_ids):
    code = r"""
import json
import os
from portfolio.db import get_conn, get_cursor

stream_ids = [int(value) for value in os.environ["STREAM_IDS"].split(",") if value]
placeholders = ",".join(["%s"] * len(stream_ids))
sql = (
    "SELECT id, request_id, room_id, room_seq, user_id, event_type, payload, body, created_at "
    "FROM messages "
    f"WHERE room_id IN ({placeholders}) "
    "ORDER BY room_id ASC, room_seq ASC"
)
with get_conn() as conn:
    with get_cursor(conn) as cur:
        cur.execute(sql, stream_ids)
        rows = cur.fetchall()

items = []
for row in rows:
    created_at = row["created_at"]
    items.append({
        "id": row["id"],
        "request_id": row["request_id"],
        "stream_id": row["room_id"],
        "stream_seq": row["room_seq"],
        "user_id": row["user_id"],
        "event_type": row["event_type"],
        "payload": row["payload"],
        "body": (row["payload"] or {}).get("message", row["body"]),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
    })
print(json.dumps(items))
"""
    env_args = ["env", f"STREAM_IDS={','.join(str(value) for value in stream_ids)}"]
    result = kubectl(
        namespace,
        "exec",
        f"deploy/{api_deployment}",
        "--",
        *env_args,
        "python",
        "-c",
        code,
    )
    return json.loads(result.stdout or "[]")


def list_dlq(base_url, token, limit=200, host_header=None):
    data = http_json("GET", f"{base_url}/v1/dlq/ingress?limit={limit}", token=token, host_header=host_header)
    return data.get("items", [])


def create_user(base_url, prefix, host_header=None):
    safe_prefix = "".join(ch for ch in prefix if ch.isalnum())[:10]
    username = f"{safe_prefix}{uuid4().hex[:12]}"
    user = http_json(
        "POST",
        f"{base_url}/v1/users",
        payload={"username": username, "password": PASSWORD},
        host_header=host_header,
    )
    login = http_json(
        "POST",
        f"{base_url}/v1/auth/login",
        payload={"username": username, "password": PASSWORD},
        host_header=host_header,
    )
    return user, login["access_token"]


def create_stream(base_url, token, name, member_ids, host_header=None):
    return http_json(
        "POST",
        f"{base_url}/v1/streams",
        token=token,
        payload={"name": name, "member_ids": member_ids},
        host_header=host_header,
    )


def send_event(base_url, token, stream_id, body, delay, host_header=None):
    if delay > 0:
        time.sleep(delay)
    accepted_at = time.monotonic()
    response = http_json(
        "POST",
        f"{base_url}/v2/streams/{stream_id}/events",
        token=token,
        payload={
            "event_type": "portfolio.ordering-failure.probe",
            "payload": {"message": body, "sequence_label": body},
            "metadata": {"scenario": "ordering-failure-injection"},
        },
        host_header=host_header,
    )
    if response.get("status") != "accepted":
        raise RuntimeError(f"Unexpected accept status body={body} response={response}")
    return {
        "stream_id": stream_id,
        "body": body,
        "request_id": response["request_id"],
        "accepted_at_monotonic": accepted_at,
    }


def send_stream_events(base_url, token, stream_id, prefix, count, per_event_delay, start_event, host_header=None):
    accepted = []
    start_event.wait()
    for index in range(1, count + 1):
        body = f"{prefix}{index:03d}"
        accepted.append(send_event(base_url, token, stream_id, body, per_event_delay, host_header=host_header))
    return accepted


def inject_db_outage(args, start_event, outage_info):
    start_event.wait()
    time.sleep(args.outage_start_sec)
    workload_ref = resolve_workload(args.namespace, args.db_failure_target)
    original_replicas = get_workload_replicas(args.namespace, workload_ref)
    outage_info["target"] = workload_ref
    outage_info["original_replicas"] = original_replicas
    outage_info["down_started_at"] = now_iso()
    outage_info["down_started_monotonic"] = time.monotonic()
    scale_workload(args.namespace, workload_ref, 0)
    time.sleep(args.outage_duration_sec)
    outage_info["restore_started_at"] = now_iso()
    outage_info["restore_started_monotonic"] = time.monotonic()
    scale_workload(args.namespace, workload_ref, original_replicas)
    wait_rollout(args.namespace, workload_ref, args.recovery_timeout_sec)
    wait_db_query_ready(args.namespace, args.api_deployment, args.recovery_timeout_sec)
    run_migrations(args.namespace, args.api_deployment)
    wait_ready(args.base_url, args.recovery_timeout_sec, host_header=args.host_header)
    outage_info["db_ready_at"] = now_iso()
    outage_info["db_ready_monotonic"] = time.monotonic()


def build_expected(prefixes, count):
    expected = {}
    for stream_id, prefix in prefixes.items():
        expected[stream_id] = [f"{prefix}{index:03d}" for index in range(1, count + 1)]
    return expected


def analyze_result(name, stream_specs, accepted, persisted, dlq_items, started_at, completed_at, outage_info):
    stream_ids = [spec["stream_id"] for spec in stream_specs]
    prefix_by_stream = {spec["stream_id"]: spec["prefix"] for spec in stream_specs}
    expected_by_stream = build_expected(prefix_by_stream, stream_specs[0]["count"])
    expected_total = sum(len(items) for items in expected_by_stream.values())

    accepted_request_ids = {item["request_id"] for item in accepted}
    persisted_request_ids = {item["request_id"] for item in persisted if item["request_id"] in accepted_request_ids}
    missing_request_ids = sorted(accepted_request_ids - persisted_request_ids)

    duplicate_request_ids = []
    request_seen = {}
    for row in persisted:
        request_id = row["request_id"]
        if request_id not in accepted_request_ids:
            continue
        request_seen[request_id] = request_seen.get(request_id, 0) + 1
    for request_id, count in request_seen.items():
        if count > 1:
            duplicate_request_ids.append(request_id)

    mixed_payload = []
    stream_results = []
    ordering_pass = True
    sequence_gap_count = 0
    duplicate_body_count = 0

    for spec in stream_specs:
        stream_id = spec["stream_id"]
        prefix = spec["prefix"]
        expected_bodies = expected_by_stream[stream_id]
        rows = [row for row in persisted if int(row["stream_id"]) == int(stream_id)]
        rows.sort(key=lambda row: int(row["stream_seq"]))
        bodies = [row["body"] for row in rows if row["request_id"] in accepted_request_ids]
        seqs = [int(row["stream_seq"]) for row in rows if row["request_id"] in accepted_request_ids]
        mixed = [body for body in bodies if not str(body).startswith(prefix)]
        mixed_payload.extend([{"stream_id": stream_id, "body": body} for body in mixed])
        duplicate_bodies = len(bodies) - len(set(bodies))
        duplicate_body_count += max(0, duplicate_bodies)
        expected_seqs = list(range(1, len(expected_bodies) + 1))
        stream_ordering_pass = bodies == expected_bodies and seqs == expected_seqs
        if not stream_ordering_pass:
            ordering_pass = False
        if seqs != expected_seqs:
            sequence_gap_count += 1

        head = [f"{seq}:{body}" for seq, body in list(zip(seqs, bodies))[:5]]
        tail = [f"{seq}:{body}" for seq, body in list(zip(seqs, bodies))[-5:]]
        first_mismatch = None
        for index, expected_body in enumerate(expected_bodies):
            actual_body = bodies[index] if index < len(bodies) else None
            actual_seq = seqs[index] if index < len(seqs) else None
            if actual_body != expected_body or actual_seq != index + 1:
                first_mismatch = {
                    "index": index + 1,
                    "expected_seq": index + 1,
                    "actual_seq": actual_seq,
                    "expected_body": expected_body,
                    "actual_body": actual_body,
                }
                break

        stream_results.append(
            {
                "name": spec["name"],
                "stream_id": stream_id,
                "prefix": prefix,
                "result": "PASS" if stream_ordering_pass and not mixed else "FAIL",
                "expected_count": len(expected_bodies),
                "persisted_count": len(bodies),
                "sequence_min": min(seqs) if seqs else None,
                "sequence_max": max(seqs) if seqs else None,
                "head": head,
                "tail": tail,
                "first_mismatch": first_mismatch,
            }
        )

    scenario_dlq = [
        item for item in dlq_items
        if item.get("request_id") in accepted_request_ids
    ]
    dlq_count = len(scenario_dlq)
    duplicate_total = len(duplicate_request_ids) + duplicate_body_count
    no_loss = len(missing_request_ids) == 0 and len(persisted_request_ids) == len(accepted_request_ids)
    no_duplicate = duplicate_total == 0
    no_mixed = len(mixed_payload) == 0
    dlq_empty = dlq_count == 0
    persisted_count = len(persisted_request_ids)

    checks = {
        "ordering": "PASS" if ordering_pass else "FAIL",
        "no_loss": "PASS" if no_loss else "FAIL",
        "no_duplicate": "PASS" if no_duplicate else "FAIL",
        "no_mixed_payload": "PASS" if no_mixed else "FAIL",
        "dlq_empty": "PASS" if dlq_empty else "FAIL",
    }
    passed = all(value == "PASS" for value in checks.values())

    total_duration = completed_at - started_at
    db_outage_seconds = None
    recovery_to_completion = None
    if outage_info.get("down_started_monotonic") and outage_info.get("db_ready_monotonic"):
        db_outage_seconds = outage_info["db_ready_monotonic"] - outage_info["down_started_monotonic"]
        recovery_to_completion = max(0, completed_at - outage_info["db_ready_monotonic"])

    return {
        "result": "PASS" if passed else "FAIL",
        "scenario": name,
        "checks": checks,
        "counts": {
            "expected": expected_total,
            "accepted": len(accepted_request_ids),
            "persisted": persisted_count,
            "missing": len(missing_request_ids),
            "duplicate": duplicate_total,
            "mixed_payload": len(mixed_payload),
            "dlq": dlq_count,
        },
        "timing": {
            "total_duration_seconds": round(total_duration, 3),
            "db_outage_seconds": None if db_outage_seconds is None else round(db_outage_seconds, 3),
            "recovery_to_completion_seconds": None if recovery_to_completion is None else round(recovery_to_completion, 3),
        },
        "outage": {
            key: value for key, value in outage_info.items()
            if not key.endswith("_monotonic")
        },
        "streams": stream_results,
        "failure_details": {
            "missing_request_ids": missing_request_ids[:20],
            "duplicate_request_ids": duplicate_request_ids[:20],
            "mixed_payload": mixed_payload[:20],
            "dlq_request_ids": [item.get("request_id") for item in scenario_dlq[:20]],
        },
    }


def print_result(result):
    line = "=" * 60
    print(line)
    print(f"ORDERING / FAILURE INJECTION TEST: {result['result']}")
    print(line)
    print(f"Scenario: {result['scenario']}")
    print()
    print("Checks:")
    for name, value in result["checks"].items():
        print(f"- {name}: {value}")
    print()
    print("Counts:")
    for name, value in result["counts"].items():
        print(f"- {name}: {value}")
    print()
    print("Timing:")
    for name, value in result["timing"].items():
        print(f"- {name}: {value}")
    if result.get("outage"):
        print()
        print("Outage:")
        for name, value in result["outage"].items():
            print(f"- {name}: {value}")
    print()
    print("PostgreSQL evidence:")
    for stream in result["streams"]:
        seq_range = (
            f"{stream['sequence_min']}..{stream['sequence_max']}"
            if stream["sequence_min"] is not None
            else "none"
        )
        body_range = f"{stream['prefix']}001..{stream['prefix']}{stream['expected_count']:03d}"
        print(
            "- Stream {0} id={1} result={2} count={3} seq={4} body={5}".format(
                stream["name"],
                stream["stream_id"],
                stream["result"],
                stream["persisted_count"],
                seq_range,
                body_range,
            )
        )
        print(f"  head: {', '.join(stream['head'])}")
        print(f"  tail: {', '.join(stream['tail'])}")
        if stream["first_mismatch"]:
            print(f"  first_mismatch: {stream['first_mismatch']}")
    if result["result"] != "PASS":
        print()
        print("Failure details:")
        for name, value in result["failure_details"].items():
            print(f"- {name}: {value}")


def poll_until_persisted(args, stream_specs, accepted):
    expected_total = len({item["request_id"] for item in accepted})
    stream_ids = [spec["stream_id"] for spec in stream_specs]
    deadline = time.monotonic() + args.persist_timeout_sec
    last_rows = []
    last_error = None
    while time.monotonic() < deadline:
        try:
            rows = query_persisted_events(args.namespace, args.api_deployment, stream_ids)
            accepted_ids = {item["request_id"] for item in accepted}
            matched = [row for row in rows if row["request_id"] in accepted_ids]
            last_rows = rows
            if len({row["request_id"] for row in matched}) >= expected_total:
                return rows
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(1)
    if last_error is not None:
        raise RuntimeError(f"Timed out polling persisted events. last_db_query_error={last_error}") from last_error
    return last_rows


def run_scenario(args, name, stream_count, inject_failure):
    wait_ready(args.base_url, args.ready_timeout_sec, host_header=args.host_header)
    suffix = f"{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
    owner, token = create_user(args.base_url, f"order_owner_{suffix}", host_header=args.host_header)
    member, _ = create_user(args.base_url, f"order_member_{suffix}", host_header=args.host_header)

    prefixes = ["A", "B", "C"][:stream_count]
    stream_specs = []
    for prefix in prefixes:
        stream = create_stream(
            args.base_url,
            token,
            f"ordering-{name}-{prefix}-{suffix}",
            [owner["id"], member["id"]],
            host_header=args.host_header,
        )
        stream_specs.append(
            {
                "name": prefix,
                "prefix": prefix,
                "stream_id": int(stream["id"]),
                "count": args.event_count,
            }
        )

    start_event = threading.Event()
    outage_info = {}
    outage_thread = None
    if inject_failure:
        outage_thread = threading.Thread(
            target=inject_db_outage,
            args=(args, start_event, outage_info),
            daemon=True,
        )
        outage_thread.start()

    started_at = time.monotonic()
    accepted = []
    with ThreadPoolExecutor(max_workers=stream_count) as executor:
        futures = [
            executor.submit(
                send_stream_events,
                args.base_url,
                token,
                spec["stream_id"],
                spec["prefix"],
                spec["count"],
                args.per_event_delay_sec,
                start_event,
                args.host_header,
            )
            for spec in stream_specs
        ]
        start_event.set()
        for future in as_completed(futures):
            accepted.extend(future.result())

    if outage_thread:
        outage_thread.join(timeout=args.recovery_timeout_sec + args.outage_duration_sec + 30)
        if outage_thread.is_alive():
            raise RuntimeError("DB outage thread did not finish in time")

    persisted = poll_until_persisted(args, stream_specs, accepted)
    completed_at = time.monotonic()
    dlq_items = list_dlq(args.base_url, token, host_header=args.host_header)
    return analyze_result(name, stream_specs, accepted, persisted, dlq_items, started_at, completed_at, outage_info)


def save_results(output_dir, results):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    latest = output_path / "latest.json"
    latest.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return latest


def parse_args():
    parser = argparse.ArgumentParser(description="Ordering and DB failure injection validation")
    parser.add_argument("--base-url", default="http://127.0.0.1")
    parser.add_argument("--host-header", default="localhost")
    parser.add_argument("--namespace", default="messaging-app")
    parser.add_argument("--api-deployment", default="api")
    parser.add_argument("--db-failure-target", default="messaging-postgresql-ha-pgpool")
    parser.add_argument("--event-count", type=int, default=100)
    parser.add_argument("--per-event-delay-sec", type=float, default=0.03)
    parser.add_argument("--outage-start-sec", type=float, default=0.5)
    parser.add_argument("--outage-duration-sec", type=float, default=4.0)
    parser.add_argument("--ready-timeout-sec", type=int, default=180)
    parser.add_argument("--recovery-timeout-sec", type=int, default=180)
    parser.add_argument("--persist-timeout-sec", type=int, default=240)
    parser.add_argument("--output-dir", default="results/ordering-failure")
    parser.add_argument(
        "--scenario",
        choices=[
            "all",
            "single_no_failure",
            "multi_no_failure",
            "single_db_failure",
            "multi_db_failure",
        ],
        default="all",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    scenario_plan = [
        ("single_no_failure", 1, False),
        ("multi_no_failure", 3, False),
        ("single_db_failure", 1, True),
        ("multi_db_failure", 3, True),
    ]
    if args.scenario != "all":
        scenario_plan = [item for item in scenario_plan if item[0] == args.scenario]

    all_results = {
        "started_at": now_iso(),
        "base_url": args.base_url,
        "namespace": args.namespace,
        "event_count_per_stream": args.event_count,
        "db_failure_target": args.db_failure_target,
        "results": [],
    }

    any_failed = False
    try:
        for name, stream_count, inject_failure in scenario_plan:
            result = run_scenario(args, name, stream_count, inject_failure)
            all_results["results"].append(result)
            print_result(result)
            print()
            if result["result"] != "PASS":
                any_failed = True
        all_results["completed_at"] = now_iso()
        all_results["result"] = "FAIL" if any_failed else "PASS"
    finally:
        path = save_results(args.output_dir, all_results)
        print(f"Result JSON: {path}")

    if any_failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
