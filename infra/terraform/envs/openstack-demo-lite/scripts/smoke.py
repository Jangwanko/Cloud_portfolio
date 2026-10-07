"""Non-destructive v2 persistence smoke; creates one unique account and event."""
import json
import secrets
import time
import urllib.error
import urllib.request

token = None
def call(method, path, body=None, extra=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    headers.update(extra or {})
    req = urllib.request.Request(
        "http://127.0.0.1:8000" + path,
        data=None if body is None else json.dumps(body).encode(),
        headers=headers, method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.status, json.load(response)

_, ready = call("GET", "/health/ready")
assert ready["status"] == "ready", ready
suffix = secrets.token_hex(8)
credentials = {"username": "tf_" + suffix,
               "password": secrets.token_urlsafe(24) + "Aa1!"}
_, user = call("POST", "/v1/users", credentials)
_, login = call("POST", "/v1/auth/login", credentials)
token = login["access_token"]
_, stream = call("POST", "/v1/streams",
                 {"name": "terraform-" + suffix, "member_ids": [user["id"]]})
path = f'/v2/streams/{stream["id"]}/events'
event = {"event_type": "portfolio.smoke.probe",
         "payload": {"message": "terraform OpenStack"},
         "metadata": {"scenario": "terraform-demo-lite", "run": suffix}}
code, accepted = call("POST", path, event, {"X-Idempotency-Key": suffix})
assert code == 202 and accepted["schema_version"] == 2, accepted
request_id = accepted["request_id"]
deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    try:
        _, state = call("GET", f"/v2/event-requests/{request_id}")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    else:
        if state.get("status") == "persisted":
            break
    time.sleep(1)
else:
    raise RuntimeError("Persistence timed out")
_, events = call("GET", path)
matches = [e for e in events["items"] if e.get("request_id") == request_id]
assert len(matches) == 1, matches
for field in ("event_type", "payload", "metadata"):
    assert matches[0][field] == event[field], field
print(json.dumps({"result": "PASS", "request_id": request_id,
                  "accepted": 202, "status": "persisted", "matches": 1}))
