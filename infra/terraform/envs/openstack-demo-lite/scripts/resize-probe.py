"""Persist a probe before resize, verify the same authenticated read after resize."""
import json
import pathlib
import secrets
import sys
import time
import urllib.request
import urllib.error

mode, base, filename = sys.argv[1:]
path = pathlib.Path(filename)
token = None
def call(method, route, body=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + route, method=method, headers=h,
        data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, json.load(r)

if mode == "prepare":
    suffix = secrets.token_hex(8)
    credentials = {"username": "resize_" + suffix, "password": secrets.token_urlsafe(24) + "Aa1!"}
    _, user = call("POST", "/v1/users", credentials)
    _, login = call("POST", "/v1/auth/login", credentials)
    token = login["access_token"]
    _, stream = call("POST", "/v1/streams", {"name": "resize-" + suffix, "member_ids": [user["id"]]})
    route = f'/v2/streams/{stream["id"]}/events'
    event = {"event_type": "portfolio.resize.probe", "payload": {"marker": suffix},
             "metadata": {"scenario": "cpu-resize-preservation"}}
    code, accepted = call("POST", route, event, {"X-Idempotency-Key": suffix})
    assert code == 202
    evidence = {"credentials": credentials, "stream_id": stream["id"],
                "request_id": accepted["request_id"], "event": event}
    path.write_text(json.dumps(evidence))
elif mode == "verify":
    evidence = json.loads(path.read_text())
    _, login = call("POST", "/v1/auth/login", evidence["credentials"])
    token = login["access_token"]
    route = f'/v2/streams/{evidence["stream_id"]}/events'
else:
    raise SystemExit("mode must be prepare or verify")

for attempt in range(90):
    try:
        _, state = call("GET", "/v2/event-requests/" + evidence["request_id"])
        if state["status"] == "persisted":
            break
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    time.sleep(1)
else:
    raise RuntimeError("Persistence timeout")
_, result = call("GET", route)
rows = [x for x in result["items"] if x.get("request_id") == evidence["request_id"]]
assert len(rows) == 1
for key, value in evidence["event"].items():
    assert rows[0][key] == value, key
print(json.dumps({"mode": mode, "result": "PASS", "request_id": evidence["request_id"],
                  "event_id": rows[0].get("id"), "matching_events": 1}))