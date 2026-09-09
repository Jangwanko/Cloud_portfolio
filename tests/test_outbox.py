from contextlib import contextmanager

import pytest

from worker import outbox


@pytest.fixture
def relay(monkeypatch):
    events = []
    class Cursor:
        row = {'message_id': 1, 'room_id': 2, 'payload': {'event_id': 1}, 'attempts': 100}
        def execute(self, query, params=None):
            events.append(('sql', query, params))
        def fetchone(self):
            return self.row
    class Conn:
        def commit(self):
            events.append(('commit',))
    cursor, conn = Cursor(), Conn()
    @contextmanager
    def connection():
        try:
            yield conn
        finally:
            events.append(('release',))
    @contextmanager
    def curs(_conn):
        yield cursor
    monkeypatch.setattr(outbox, 'get_conn', connection)
    monkeypatch.setattr(outbox, 'get_cursor', curs)
    return cursor, conn, events


def test_send_failure_leaves_pending_with_capped_backoff(monkeypatch, relay):
    _, _, events = relay
    def fail(*args):
        raise RuntimeError('credential-bearing broker detail must not be stored')
    monkeypatch.setattr(outbox, 'publish_notification_job', fail)
    assert outbox.relay_once()
    updates = [e for e in events if e[0]=='sql' and e[1].strip().startswith('UPDATE')]
    assert len(updates)==1
    assert 'published_at=' not in updates[0][1]
    assert updates[0][2] == ('RuntimeError',60,1)
    assert ('commit',) in events


def test_completion_is_after_ack_and_commit_failure_propagates(monkeypatch, relay):
    _, conn, events = relay
    monkeypatch.setattr(outbox, 'publish_notification_job', lambda *_: events.append(('ack',)))
    def commit_failure():
        raise RuntimeError('lost DB connection after ACK')
    conn.commit = commit_failure
    with pytest.raises(RuntimeError,match='after ACK'):
        outbox.relay_once()
    ack = events.index(('ack',))
    completion = next(i for i,e in enumerate(events) if e[0]=='sql' and 'published_at=' in e[1])
    assert ack < completion
    assert events[-1]==('release',)


def test_empty_or_locked_queue_does_not_publish(monkeypatch, relay):
    cur, _, events = relay
    cur.row = None
    monkeypatch.setattr(outbox, 'publish_notification_job',lambda *_: pytest.fail('Unexpected send'))
    assert outbox.relay_once() is False
    assert ('commit',) not in events


def test_enqueue_failure_prevents_core_commit(monkeypatch):
    from worker import main as core
    class Conn:
        def commit(self):
            pytest.fail('Commit after failed outbox insert')
    @contextmanager
    def connection():
        yield Conn()
    @contextmanager
    def cursor(_conn):
        yield object()
    monkeypatch.setattr(core,'get_conn',connection)
    monkeypatch.setattr(core,'get_cursor',cursor)
    monkeypatch.setattr(core,'_persist_message_with_cursor',lambda *a:{'id':1,'room_id':2})
    monkeypatch.setattr(core,'persisted_status_payload',lambda *a:{})
    monkeypatch.setattr(core,'upsert_request_status',lambda *a:None)
    monkeypatch.setattr(core,'notification_attempt_payload',lambda *a:{})
    def fail(*args):
        raise RuntimeError('outbox unavailable')
    monkeypatch.setattr(core,'enqueue_notification',fail)
    with pytest.raises(RuntimeError,match='outbox unavailable'):
        core.persist_ingress_job({'request_id':'r1'})
