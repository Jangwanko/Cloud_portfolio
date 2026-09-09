"""In-cluster canary for release_failure_lab.py; credentials never leave the Pod."""

import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import psycopg2
from psycopg2.extras import RealDictCursor


STATE = Path('/tmp/release-probe.json')
BASE = 'http://api:8000'


def http(method, path, body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                      headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def database():
    return psycopg2.connect(host=os.environ['DB_HOST'], dbname=os.environ['DB_NAME'],
                            user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'],
                            connect_timeout=5)


def schema():
    with database() as conn, conn.cursor() as cur:
        cur.execute('SELECT version_num FROM alembic_version')
        version = cur.fetchone()[0]
        cur.execute("SELECT to_regclass('public.release_lab_marker')")
        marker = cur.fetchone()[0]
        count = 0
        if marker:
            cur.execute('SELECT count(*) FROM release_lab_marker')
            count = cur.fetchone()[0]
    return {'alembic_version': version, 'marker_exists': bool(marker), 'marker_rows': count}


def inspect_rows(state):
    with database() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('SELECT request_id, room_seq, schema_version, event_type, payload, metadata '
                    'FROM messages WHERE room_id=%s ORDER BY room_seq', (state['stream_id'],))
        rows = [dict(row) for row in cur.fetchall()]
        cur.execute('SELECT count(*) AS count FROM notification_attempts WHERE room_id=%s',
                    (state['stream_id'],))
        notifications = cur.fetchone()['count']
    return rows, notifications


def canary(phase, count=10):
    started = time.monotonic()
    if not STATE.exists():
        credentials = {'username': 'release-lab-probe', 'password': os.environ['DB_PASSWORD']}
        status, _ = http('POST', '/v1/users', credentials)
        assert status == 200, f'user creation HTTP {status}'
        status, login = http('POST', '/v1/auth/login', credentials)
        assert status == 200, f'login HTTP {status}'
        token = login['access_token']
        status, stream = http('POST', '/v1/streams', {'name': 'Release failure canary'}, token)
        assert status == 200, f'stream creation HTTP {status}'
        state = {'token': token, 'stream_id': stream['id'], 'events': []}
    else:
        state = json.loads(STATE.read_text())
    new_events = []
    for _ in range(count):
        sequence = len(state['events']) + 1
        event = {'event_type': 'lab.release_checked',
                 'payload': {'sequence': sequence, 'phase': phase, 'nested': {'verified': True}},
                 'metadata': {'experiment': 'release-failure', 'phase': phase}}
        status, accepted = http('POST', f"/v2/streams/{state['stream_id']}/events", event,
                                state['token'])
        assert status == 202, f'event intake HTTP {status}'
        event['request_id'] = accepted['request_id']
        state['events'].append(event)
        new_events.append(event)
        STATE.write_text(json.dumps(state))

    deadline = time.monotonic() + 90
    expected = state['events']
    while True:
        rows, notifications = inspect_rows(state)
        if len(rows) == len(expected) and notifications == len(expected):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Persistence timeout: expected={len(expected)}, '
                               f'rows={len(rows)}, notifications={notifications}')
        time.sleep(0.5)
    request_ids = [row['request_id'] for row in rows]
    checks = {
        'accepted_equals_persisted': len(expected) == len(rows),
        'missing_zero': set(request_ids) == {event['request_id'] for event in expected},
        'duplicate_zero': len(request_ids) == len(set(request_ids)),
        'ordering': [row['room_seq'] for row in rows] == list(range(1, len(rows) + 1)),
        'structured_envelopes_match': all(
            row['schema_version'] == 2 and
            all(row[key] == event[key] for key in ('request_id', 'event_type', 'payload', 'metadata'))
            for row, event in zip(rows, expected, strict=True)),
        'notifications_match': notifications == len(expected),
    }
    for event in new_events:
        status, response = http('GET', '/v2/event-requests/' + event['request_id'],
                                token=state['token'])
        assert status == 200 and response['status'] == 'persisted', 'request status mismatch'
    assert all(checks.values()), checks
    return {'phase': phase, 'accepted_this_phase': len(new_events),
            'accepted_total': len(expected), 'persisted_total': len(rows),
            'notification_attempts_total': notifications, 'checks': checks,
            'schema': schema(), 'elapsed_seconds': round(time.monotonic() - started, 3),
            'events': rows}


if __name__ == '__main__':
    result = schema() if sys.argv[1] == 'schema' else canary(sys.argv[1])
    print(json.dumps(result, sort_keys=True))
