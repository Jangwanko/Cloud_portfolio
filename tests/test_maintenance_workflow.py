import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import check_docs, ci_scope, run_validation
from scripts.check_portfolio_status import Checker


@pytest.mark.parametrize('paths,expected', [
    (['README.md', 'docs/DEMO_LITE.md'], True),
    (['docs/harness/runtime.json'], True),
    (['README.md', 'portfolio/api.py'], False),
    (['.github/workflows/ci.yml'], False),
    (['requirements.txt'], False),
    (['docs/example.py'], False),
    (['tests/test_portfolio_readiness.py'], False),
    (['demo/verified-incident-replay.json'], False),
    (['docs/../portfolio/api.py'], False),
    (['docs/unknown.json'], False),
    ([], False),
])
def test_ci_scope_fails_closed(paths, expected):
    assert ci_scope.docs_only(paths) is expected


def test_missing_history_uses_full_ci():
    def unavailable(*args, **kwargs):
        raise subprocess.CalledProcessError(1, 'git')
    assert ci_scope.changed_paths('a'*40, 'b'*40, run=unavailable) is None
    assert ci_scope.changed_paths('0'*40, 'b'*40) is None


def test_rename_includes_deleted_source_path():
    def fake(cmd, **kwargs):
        assert '--no-renames' in cmd
        return b'portfolio/api.py\0docs/api.md\0'
    paths = ci_scope.changed_paths('a'*40, 'b'*40, run=fake)
    assert not ci_scope.docs_only(paths)


def args(tmp_path, profile='local-ha'):
    return argparse.Namespace(profile=profile, context='explicit-test-context', public_only=False,
        namespace='messaging-app', argo_namespace='argocd', argo_application=None,
        skip_argocd=False, skip_prometheus=False, base_url='http://unused',
        prometheus_url='http://unused/prometheus', output_dir=tmp_path)


def test_status_collects_independent_failures_and_does_not_dump_stderr(tmp_path):
    calls = []
    def unavailable(cmd, **kwargs):
        calls.append(cmd)
        assert cmd[1:3] == ['--context', 'explicit-test-context']
        raise subprocess.CalledProcessError(1, cmd, stderr='SECRET must never be in report')
    def http(url):
        if url.endswith('/health/ready'):
            return {'status': 'not_ready', 'reason': ['schema_not_ready']}
        if '/api/v1/query' in url:
            return {'status': 'success', 'data': {'result': []}}
        return {'worker': {'available': 0}}
    checker = Checker(args(tmp_path), get_json=http, command=unavailable)
    checker.ui = lambda: {'ui_version': 'test'}
    assert checker.run() == 1
    report = json.loads(checker.path.read_text(encoding='utf-8'))
    checks = {c['name']: c for c in report['checks']}
    assert checks['API readiness']['status'] == 'failed'
    assert checks['Operations summary']['status'] == 'passed'
    assert checks['Runtime images']['status'] == 'unknown'
    assert checks['Kafka brokers']['status'] == 'unknown'
    assert calls and 'SECRET' not in checker.path.read_text(encoding='utf-8')
    assert report['finished_at']


def test_demo_profile_uses_one_broker_and_no_outbox(tmp_path):
    options = args(tmp_path, 'demo-lite')
    options.public_only = True
    def http(url):
        if '/api/v1/query' in url:
            return {'status': 'success', 'data': {'result': [{'value': [1, '1']}]}}
        return {'status': 'ready'}
    checker = Checker(options, get_json=http)
    checker.ui = lambda: {'ui_version': '2.5.0'}
    assert checker.run() == 0
    checks = {c['name']: c for c in checker.report['checks']}
    assert checks['Kafka brokers']['evidence']['minimum'] == 1
    assert checks['Cluster inspection']['status'] == 'skipped'
    assert 'scrape/outbox-publisher' not in checks
    assert checker.report['scope'] == 'public endpoints only'


def test_all_scrape_targets_must_be_up(tmp_path):
    options = args(tmp_path, 'demo-lite')
    options.public_only = True
    def http(url):
        if '/api/v1/query' in url:
            return {'status': 'success', 'data': {'result': [
                {'value': [1, '1']}, {'value': [1, '0']} ]}}
        return {'status': 'ready'}
    checker = Checker(options, get_json=http)
    checker.ui = lambda: {'ui_version': 'test'}
    assert checker.run() == 1
    checks = {c['name']: c for c in checker.report['checks']}
    assert checks['scrape/api']['status'] == 'failed'
    assert checks['scrape/api']['evidence']['up'] == [1.0, 0.0]


def test_validation_failure_keeps_exit_code_and_log(tmp_path):
    assert run_validation.run([sys.executable, '-c', 'print("gate-output"); raise SystemExit(7)'], 'failure', tmp_path) == 7
    path = next(tmp_path.glob('*/summary.json'))
    report = json.loads(path.read_text(encoding='utf-8'))
    assert report['status'] == 'failed' and report['exit_code'] == 7
    assert report['started_at'] and report['finished_at'] and report['source']['commit']
    assert 'gate-output' in (path.parent / 'output.log').read_text()


def test_validation_started_record_exists_before_command(monkeypatch, tmp_path):
    def spawn(*args, **kwargs):
        report = json.loads(next(tmp_path.glob('*/summary.json')).read_text(encoding='utf-8'))
        assert report['status'] == 'running'
        assert 'finished_at' not in report
        raise FileNotFoundError('tool unavailable')
    from scripts import validation_record
    monkeypatch.setattr(validation_record, 'source_identity', lambda: {'commit': 'test'})
    monkeypatch.setattr(run_validation.subprocess, 'Popen', spawn)
    assert run_validation.run(['missing-tool'], 'missing', tmp_path) == 127


def test_document_checker_catches_missing_anchors_and_conflict(tmp_path):
    path = tmp_path / 'README.md'
    path.write_text('# Intro\n\n[ok](#intro)\n[bad](#absent)\n<<<<<<< HEAD\n', encoding='utf-8')
    errors = check_docs.check_file(path, tmp_path)
    assert 'merge conflict marker' in errors
    assert 'missing anchor: #absent' in errors
    assert not any('#intro' in error for error in errors)


def test_hash_computed_before_connection_checkout(monkeypatch):
    from portfolio import api
    from portfolio.schemas import UserCreate
    events = []
    class Cursor:
        def execute(self, sql, values):
            assert values == ('tester', 'computed-hash')
        def fetchone(self):
            return {'id': 1, 'username': 'tester'}
    class Connection:
        def commit(self):
            events.append('commit')
    @contextmanager
    def connection():
        events.append('checkout')
        assert events == ['hash', 'checkout']
        yield Connection()
    @contextmanager
    def cursor(conn):
        yield Cursor()
    def hash_password(password):
        assert not events
        events.append('hash')
        return 'computed-hash'
    monkeypatch.setattr(api, 'hash_password', hash_password)
    monkeypatch.setattr(api, 'get_conn', connection)
    monkeypatch.setattr(api, 'get_cursor', cursor)
    assert api.create_user(UserCreate(username='tester', password='Password123!'))['id'] == 1
    assert events == ['hash', 'checkout', 'commit']


def test_routine_checks_have_no_reset_invocation():
    root = Path(__file__).resolve().parents[1]
    for name in ['smoke_test.ps1', 'test_api_contracts.ps1']:
        assert 'reset_k8s_state.ps1' not in (root / 'scripts' / name).read_text()
    runner = (root / 'scripts/run_recommended_tests.ps1').read_text()
    routine = runner.split('if (-not $RunIsolatedExperiments) {', 1)[1].split('# Existing failure scripts', 1)[0]
    assert 'Reset-State' not in routine and 'test_db_down' not in routine
    assert 'return' in routine
