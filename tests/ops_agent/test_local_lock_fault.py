import pytest
from pathlib import Path
from scripts import run_local_lock_fault as runner
from scripts.run_local_lock_fault import lock_sql, persisted, sql_command


def test_relative_artifact_path_keeps_hash_and_does_not_break_capture(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner,'ROOT',tmp_path)
    path=Path('observation.json')
    path.write_text('{}',encoding='utf-8')
    row=runner.supplemental('requests',{},'2026-11-01T00:00:00+00:00',
        '2026-11-01T00:00:01Z',path)
    assert row['raw_ref']=='observation.json' and len(row['raw_sha256'])==64
    assert row['freshness']['age_seconds']==1


def test_lock_is_single_row_transaction_with_server_deadlines():
    text=lock_sql(42,'ops_session_123456abcdef')
    assert 'WHERE room_id=42 FOR UPDATE' in text
    assert "statement_timeout='55s'" in text and "lock_timeout='2s'" in text
    assert "idle_in_transaction_session_timeout='60s'" in text
    assert text.endswith('ROLLBACK;\n') and 'pg_sleep(50)' in text
    assert 'ALTER ' not in text and 'DELETE ' not in text
    for args in [(42,'ops_session_123456abcdef',51),('42; DROP TABLE messages','ops_session_123456abcdef',50),
                 (42,"bad'; SELECT 1;",50)]:
        with pytest.raises(ValueError): lock_sql(*args)


def test_database_target_is_fixed_local_cluster():
    command=sql_command('messaging-postgresql-ha-postgresql-0')
    assert command[command.index('--context')+1]=='kind-messaging-ha'
    assert command[command.index('-n')+1]=='messaging-app'
    with pytest.raises(ValueError): sql_command('other-primary')


def test_recovery_requires_exact_unique_accepted_and_event_ids():
    rows=[{'request_id':'a','http_status':200,'body':{'status':'persisted','event_id':1}},
          {'request_id':'b','http_status':200,'body':{'status':'persisted','event_id':2}}]
    assert persisted(rows,['a','b'])
    assert not persisted(rows,['a','b','c'])
    assert not persisted(rows,['a','a'])
    assert not persisted([rows[0],rows[0]],['a','b'])
    rows[1]['body']['event_id']=1
    assert not persisted(rows,['a','b'])
    rows[1]['body']['event_id']=2
    for status in (404,503,401):
        rows[1]['http_status']=status
        assert not persisted(rows,['a','b'])
