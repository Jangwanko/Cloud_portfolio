"""Deterministic, snapshot-scoped facts; no model or service-health diagnosis."""
from datetime import datetime
from decimal import Decimal, DecimalException
import math

VERSION = "ops.observation-facts.v2"
CONTRACTS = {
    "accepted_202": "KAFKA_APPEND_ACKNOWLEDGED_NOT_DB_PERSISTENCE",
    "status_poll_timing": "OBSERVATION_BOUND_NOT_EXACT_COMMIT_LATENCY",
    "lag_time_basis": "LATEST_SAMPLE_SEPARATE_FROM_HISTORY",
}


def _time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp requires timezone")
    return parsed


def _fresh(item):
    coverage = item["coverage"]
    return (item["usable"] and not coverage["missing_items"]
            and not coverage["extra_items"])


def _requests(item):
    value = item["metric"]["value"]
    accepted, polls = value["accepted"], value["status_polls"]
    if not accepted or item["coverage"]["complete"] is not True:
        raise ValueError("complete nonempty request inventory required")
    receipts = {r["request_id"]: r for r in accepted}
    states = {r["request_id"]: r for r in polls}
    if (len(receipts) != len(accepted) or len(states) != len(polls)
            or set(receipts) != set(states)
            or any(not isinstance(k, str) or not k for k in receipts)):
        raise ValueError("request identity coverage mismatch")
    persisted, pending, unknown, bounds, per_request = [], [], [], {}, {}
    event_ids = set()
    for identifier, receipt in receipts.items():
        if receipt["http_status"] != 202:
            raise ValueError("receipt is not an accepted event")
        row = states[identifier]
        start, end = _time(receipt["at"]), _time(row["at"])
        age = (_time(item["source_timestamp"]) - end).total_seconds()
        if start > end or not 0 <= age <= item["freshness"]["max_age_seconds"]:
            raise ValueError("invalid or stale request poll timestamp")
        body = row.get("body", {})
        if body.get("request_id", identifier) != identifier:
            raise ValueError("poll body request identity mismatch")
        state = {"append": "ACKNOWLEDGED", "persistence": "UNKNOWN", "status_read": "UNAVAILABLE",
                 "status_http": row["http_status"], "accepted_at": receipt["at"], "status_observed_at": row["at"]}
        if row["http_status"] == 200 and body.get("status") == "persisted":
            event = body.get("event_id")
            if not event or event in event_ids:
                raise ValueError("missing or duplicate persisted event identity")
            event_ids.add(event)
            persisted.append(identifier)
            state.update(persistence="CONFIRMED", status_read="READABLE")
            bounds[identifier] = round((end - start).total_seconds(), 6)
        elif row["http_status"] == 404 or (
                row["http_status"] == 200 and body.get("status") == "queued"):
            pending.append(identifier)
            state.update(persistence="NOT_YET_OBSERVED", status_read="READABLE")
        else:
            # 401/503/timeouts do not prove either persistence or a stalled write.
            unknown.append(identifier)
        per_request[identifier] = state
    return {"accepted_ids": sorted(receipts), "persisted_ids": sorted(persisted),
            "not_yet_persisted_ids": sorted(pending), "unobservable_ids": sorted(unknown),
            "accepted_to_status_observed_seconds": bounds,
            "per_request": dict(sorted(per_request.items())),
            "scope": "only listed accepted request IDs; not all service traffic"}


def _lag(items, observed_at):
    if not items or any(not _fresh(e) for e in items):
        raise ValueError("missing or unusable lag series")
    partitions = [e["labels"]["partition"] for e in items]
    if len(partitions) != len(set(partitions)):
        raise ValueError("duplicate partition or multiple consumer groups")
    scope = {(e["labels"].get("topic"), e["labels"].get("consumergroup")) for e in items}
    if len(scope) != 1 or any(not all(s) for s in scope):
        raise ValueError("one explicit topic and consumer group required")
    grids, series = [], []
    for item in items:
        cov = item["coverage"]
        if (cov["complete"] is not True or set(cov["expected_items"]) != set(partitions)
                or set(cov["observed_items"]) != set(partitions)
                or cov["expected_count"] != len(partitions) or cov["observed_count"] != len(partitions)):
            raise ValueError("incomplete partition coverage")
        grid = [Decimal(str(s["timestamp"])) for s in item["metric"]["value"]]
        values = [Decimal(str(s["value"])) for s in item["metric"]["value"]]
        if (not grid or any(not x.is_finite() for x in grid + values)
                or any(v < 0 for v in values)
                or any(b <= a for a, b in zip(grid, grid[1:]))):
            raise ValueError("invalid lag values or sample order")
        age = Decimal(str(_time(observed_at).timestamp())) - grid[-1]
        if not 0 <= age <= Decimal(str(item["freshness"]["max_age_seconds"])):
            raise ValueError("latest lag sample is stale or in the future")
        grids.append(grid)
        series.append(values)
    if any(grid != grids[0] for grid in grids):
        raise ValueError("partition sample grids differ")
    totals = [sum(values) for values in zip(*series)]
    if any(not math.isfinite(float(total)) for total in totals):
        raise ValueError("lag exceeds finite numeric range")
    return {"latest_total": float(totals[-1]), "window_peak_total": float(max(totals)),
            "latest_sample_timestamp": float(grids[0][-1]), "partitions": sorted(partitions),
            "topic": next(iter(scope))[0], "consumer_group": next(iter(scope))[1]}


def build_facts(context):
    """Unknown shapes and incomplete observations fail closed, without losing raw input."""
    evidence = context["evidence"]
    facts = {}

    def derive(name, metric, transform, multiple=False):
        items = [e for e in evidence if e["metric"]["name"] == metric]
        entry = {"status": "UNKNOWN", "value": None,
                 "evidence_ids": [e["evidence_id"] for e in items]}
        try:
            if not items or any(not _fresh(e) for e in items) or (not multiple and len(items) != 1):
                raise ValueError("missing, duplicate or unusable observation")
            entry.update(status="KNOWN", value=transform(items if multiple else items[0]))
        except (KeyError, TypeError, ValueError, DecimalException, OverflowError, AttributeError):
            entry["reason"] = "missing, stale, incomplete or unsupported observation"
        facts[name] = entry

    derive("requests", "event_acceptance_and_persistence", _requests)
    derive("lag", "kafka_consumergroup_lag", lambda items: _lag(items, context["observed_at"]), True)

    def waits(item):
        rows = item["metric"]["value"]["waiting"]
        # captured_at is the DB server clock; freshness is collector-clock based.
        # Comparing the two without a measured clock offset rejects valid reads.
        server_at = item["metric"]["value"].get("captured_at")
        if server_at is not None:
            _time(server_at)
        if not isinstance(rows, list) or item["coverage"]["complete"] is not True:
            raise ValueError("complete wait inventory required")
        edges = []
        for row in rows:
            if type(row["pid"]) is not int or not isinstance(row["blocking_pids"], list):
                raise ValueError("invalid wait graph")
            for blocker in row["blocking_pids"]:
                if type(blocker) is not int or blocker <= 0 or row["pid"] <= 0:
                    raise ValueError("invalid process identity")
                edges.append({"waiter_pid": row["pid"], "blocker_pid": blocker})
        return {"waiting_sessions": len(rows), "blocking_edges": edges,
                "server_captured_at": server_at, "collector_observed_at": item["source_timestamp"],
                "time_basis": "collector freshness; server clock offset unmeasured",
                "scope": "observed database sessions only; does not identify business root cause"}

    derive("database_waits", "postgres_blocking_activity", waits)
    requests = facts["requests"]
    state = "UNKNOWN"
    if requests["status"] == "KNOWN":
        value = requests["value"]
        if value["unobservable_ids"]:
            state = "UNKNOWN"
        elif value["not_yet_persisted_ids"]:
            state = "ACCEPTED_REQUESTS_PENDING"
        else:
            state = "SAMPLED_REQUESTS_PERSISTED"
    return {"version": VERSION, "observed_at": context["observed_at"],
            "bundle_sha256": context["bundle_sha256"], "contracts": dict(CONTRACTS),
            "request_state": state, "facts": facts,
            "boundary": "saved snapshot; no global severity, incident closure or sustained recovery judgment"}
