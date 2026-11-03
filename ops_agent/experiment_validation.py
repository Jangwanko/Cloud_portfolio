"""Pure acceptance checks for experimental runners; no runtime or model I/O."""
from datetime import datetime
import json


def require_stalled_requests(expected_ids, rows):
    expected = list(expected_ids)
    actual = [r.get("request_id") for r in rows]
    if not expected or len(set(expected)) != len(expected) or len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError("stall evidence request coverage mismatch")
    for row in rows:
        body = row.get("body", {})
        code = row.get("http_status")
        if code == 404 and body.get("detail") == "Request not found":
            continue
        if code == 200 and body.get("status") == "queued" and not body.get("event_id"):
            continue
        raise ValueError("stall not proven: query failed, invalid state, or already persisted")


def validate_diagnosis(text, acquired_ids, investigated):
    if not investigated or not isinstance(text, str) or not text.strip():
        raise ValueError("diagnosis requires investigation and nonempty output")
    value = json.loads(text)
    required = {"root_cause", "evidence_ids", "alternatives", "recommended_action", "verification_steps", "uncertainty"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("diagnosis fields mismatch")
    for key in ("root_cause", "recommended_action", "uncertainty"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError("empty diagnosis field")
    refs = value["evidence_ids"]
    if not isinstance(refs, list) or not refs or any(not isinstance(r, str) for r in refs):
        raise ValueError("diagnosis requires evidence IDs")
    if len(set(refs)) != len(refs) or not set(refs) <= set(acquired_ids):
        raise ValueError("diagnosis cites unavailable or duplicate evidence")
    if not isinstance(value["alternatives"], list) or not value["alternatives"]:
        raise ValueError("diagnosis requires alternative assessment")
    if not isinstance(value["verification_steps"], list) or not value["verification_steps"] or any(
            not isinstance(s, str) or not s.strip() for s in value["verification_steps"]):
        raise ValueError("diagnosis requires verification steps")
    return value


class RecoveryWindow:
    """Require exact pod coverage, stable identities and bounded observation gaps."""
    def __init__(self, expected_names, primary_name, primary_uid, primary_restarts, seconds=180, max_gap=21):
        self.expected = set(expected_names)
        if not self.expected or primary_name not in self.expected:
            raise ValueError("invalid recovery scope")
        self.primary = (primary_name, primary_uid, primary_restarts)
        self.seconds, self.max_gap = seconds, max_gap
        self.started = self.last = self.identity = None

    def observe(self, pods, http_status, health, captured_at):
        timestamp = datetime.fromisoformat(captured_at)
        if timestamp.tzinfo is None:
            raise ValueError("timezone required")
        names = [p.get("name") for p in pods]
        healthy = len(names) == len(self.expected) and set(names) == self.expected
        identity = []
        for pod in pods:
            containers = pod.get("containers", [])
            healthy = healthy and bool(pod.get("uid")) and bool(containers) and all(c.get("ready") is True for c in containers)
            for c in containers:
                healthy = healthy and type(c.get("restartCount")) is int
                identity.append((pod.get("name"), pod.get("uid"), c.get("name"), c.get("restartCount")))
        identity = sorted(identity, key=repr)
        primary = next((p for p in pods if p.get("name") == self.primary[0]), {})
        primary_counts = [c.get("restartCount") for c in primary.get("containers", [])]
        healthy = healthy and primary.get("uid") == self.primary[1] and primary_counts == [self.primary[2]]
        healthy = healthy and http_status == 200 and health.get("status") == "ready"
        gap = (timestamp - self.last).total_seconds() if self.last else None
        continuous = gap is None or 0 < gap <= self.max_gap
        if not healthy:
            self.started = None
        elif not continuous or self.identity != identity or self.started is None:
            self.started = timestamp
        elapsed = (timestamp - self.started).total_seconds() if self.started else 0
        self.last, self.identity = timestamp, identity
        return {"healthy": bool(healthy), "gap_seconds": gap, "cadence_valid": continuous,
                "sampled_stable_seconds": elapsed, "complete": bool(healthy and elapsed >= self.seconds)}
