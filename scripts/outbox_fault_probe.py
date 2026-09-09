"""Isolated-lab-only crash injection. Never imported by application entrypoints."""
import json
import os
from pathlib import Path
import sys
import time
from contextlib import contextmanager

sys.path.insert(0, '/probe')
sys.path.insert(0, '/service')
from probe import http, database
from worker import main as core, outbox

STATE = Path('/tmp/outbox-fault-state.json')


def seed(count):
    if STATE.exists():
        state = json.loads(STATE.read_text())
    else:
        credentials = {'username': 'outbox-lab', 'password': os.environ['DB_PASSWORD']}
        assert http('POST', '/v1/users', credentials)[0] == 200
        code, login = http('POST', '/v1/auth/login', credentials)
        assert code == 200
        code, stream = http('POST', '/v1/streams', {'name': 'Outbox crash lab'}, login['access_token'])
        assert code == 200
        state = {'token': login['access_token'], 'stream_id': stream['id'], 'events': []}
    for _ in range(count):
        payload = {'event_type': 'lab.outbox', 'payload': {'sequence': len(state['events'])+1},
                   'metadata': {'experiment': 'transactional-outbox'}}
        code, accepted = http('POST', f"/v2/streams/{state['stream_id']}/events", payload, state['token'])
        assert code == 202
        state['events'].append({'request_id': accepted['request_id'], **payload})
        STATE.write_text(json.dumps(state))
    return {'accepted_total': len(state['events'])}


def snapshot():
    with database() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM messages'); messages = cur.fetchone()[0]
        cur.execute('SELECT count(*), count(*) FILTER (WHERE published_at IS NULL), coalesce(sum(attempts),0) FROM notification_outbox')
        total, pending, attempts = cur.fetchone()
        cur.execute('SELECT count(*) FROM notification_attempts'); notifications = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM request_statuses WHERE status_json->>'status'='persisted'")
        persisted_statuses = cur.fetchone()[0]
    return {'messages': messages, 'outbox_total': total, 'pending': pending,
            'publish_attempts': attempts, 'notifications': notifications,
            'persisted_statuses': persisted_statuses}


def crash_core(after_commit):
    if after_commit:
        original = core.get_conn
        @contextmanager
        def patched():
            with original() as conn:
                class Proxy:
                    def __getattr__(self, key):
                        return getattr(conn, key)
                    def commit(self):
                        conn.commit()
                        os._exit(71)
                yield Proxy()
        core.get_conn = patched
    else:
        original = core.enqueue_notification
        def patched(cur, payload):
            original(cur, payload)
            os._exit(73)
        core.enqueue_notification = patched
    core.run_kafka_worker_loop()


def crash_relay():
    original = outbox.publish_notification_job
    def patched(key, payload):
        original(key, payload)
        os._exit(72)
    outbox.publish_notification_job = patched
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        outbox.relay_once()
        time.sleep(0.2)
    raise RuntimeError('Expected an ACK-boundary process exit')


def fail_send():
    def fail(*args):
        raise RuntimeError('Lab injected send failure')
    outbox.publish_notification_job = fail
    assert outbox.relay_once()
    return snapshot()


def verify():
    state = json.loads(STATE.read_text())
    with database() as conn, conn.cursor() as cur:
        cur.execute('SELECT request_id,room_seq,schema_version,event_type,payload,metadata FROM messages ORDER BY room_seq')
        rows = cur.fetchall()
        assert len(rows) == len(state['events'])
        for sequence, (row, event) in enumerate(zip(rows,state['events']),1):
            assert row == (event['request_id'], sequence, 2, event['event_type'], event['payload'], event['metadata'])
        cur.execute('SELECT count(*) FROM notification_attempts n JOIN notification_outbox o USING(message_id) WHERE n.payload=o.payload')
        assert cur.fetchone()[0] == len(rows)
    from kafka import KafkaConsumer, TopicPartition
    from portfolio.config import settings
    consumer = KafkaConsumer(bootstrap_servers=settings.kafka_bootstrap_servers.split(','),
                             enable_auto_commit=False, group_id=None)
    try:
        partitions = [TopicPartition(settings.kafka_notification_topic, p)
                      for p in consumer.partitions_for_topic(settings.kafka_notification_topic)]
        consumer.assign(partitions); consumer.seek_to_beginning(*partitions)
        ends = consumer.end_offsets(partitions)
        events = []
        deadline = time.monotonic()+30
        while any(consumer.position(p) < ends[p] for p in partitions):
            if time.monotonic() > deadline:
                raise TimeoutError('Notification Kafka audit')
            for records in consumer.poll(timeout_ms=500).values():
                events.extend(json.loads(r.value) for r in records)
    finally:
        consumer.close()
    counts = {}
    for event in events:
        key = str(event['event_id']); counts[key] = counts.get(key,0)+1
    assert len(counts)==len(rows)
    assert sorted(counts.values()) == [1]*(len(rows)-1)+[2]
    return {**snapshot(), 'event_data_and_ordering': True,
            'kafka_notifications': len(events), 'kafka_delivery_counts': counts,
            'database_notification_deduplication': True}


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'commit-crash':
        crash_core(True)
    elif action == 'rollback-crash':
        crash_core(False)
    elif action == 'ack-crash':
        crash_relay()
    else:
        result = seed(int(sys.argv[2])) if action=='seed' else {
            'snapshot': snapshot, 'fail-send': fail_send, 'verify': verify}[action]()
        print(json.dumps(result, default=str))
