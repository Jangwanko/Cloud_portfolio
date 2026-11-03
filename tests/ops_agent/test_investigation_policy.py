import copy
import json

import pytest

from ops_agent.evaluation_models import canonical_sha256
from ops_agent.grounded_assistant import run, verify_record, validate_investigation
from ops_agent.investigation_policy import build_policy
from ops_agent.observation_facts import build_facts
from ops_agent.runbook_assistant import prepare
from tests.ops_agent.test_grounded_assistant import answer
from tests.ops_agent.test_observation_history import cases, client


def context(index):
    case = cases()[index]
    return prepare(case['bundle'], case['question'], 'synthetic')


def test_current_blocking_warrants_investigation_without_request_causality():
    ctx = context(3)
    policy = build_policy(ctx, build_facts(ctx))
    fixed = policy['current_runbook_constraints'][0]
    assert (fixed['status'], fixed['applicability'], fixed['time_scope']) == ('MET', 'APPLICABLE', 'CURRENT')
    assert fixed['evidence_ids'] == [ctx['evidence'][3]['evidence_id']]
    assert [t['id'] for t in policy['required_checks']] == ['check:db-blocking']


def test_absent_waits_and_healthy_readiness_cannot_exclude_db_investigation():
    ctx = context(4)
    policy = build_policy(ctx, build_facts(ctx))
    fixed = policy['current_runbook_constraints'][0]
    assert (fixed['status'], fixed['applicability'], fixed['time_scope']) == ('UNKNOWN', 'NEEDS_MORE_EVIDENCE', 'CURRENT')
    assert policy['required_checks'] == []
    # This conservative limitation is explicit: no global DB health inference.
    assert fixed['evidence_ids'] == []


@pytest.mark.parametrize('change', ['stale', 'partial', 'duplicate', 'negative', 'boolean', 'missing', 'coverage'])
def test_bad_worker_observation_cannot_create_zero_ready_obligation(change):
    ctx = context(5)
    item = ctx['evidence'][4]
    if change == 'stale': item['usable'] = False
    if change == 'partial': item['coverage']['complete'] = False
    if change == 'duplicate': ctx['evidence'].append(copy.deepcopy(item))
    if change == 'negative': item['metric']['value']['ready_replicas'] = -1
    if change == 'boolean': item['metric']['value']['ready_replicas'] = False
    if change == 'missing': item['metric']['value'].pop('ready_replicas')
    if change == 'coverage': item['coverage'].update(expected_count=2, observed_count=1)
    policy = build_policy(ctx, build_facts(ctx))
    assert policy['worker_observation']['status'] == 'UNKNOWN'
    assert policy['required_checks'] == []


def test_scale_to_zero_is_not_a_failed_worker_obligation():
    ctx = context(5)
    ctx['evidence'][4]['metric']['value']['desired_replicas'] = 0
    assert build_policy(ctx, build_facts(ctx))['required_checks'] == []


@pytest.mark.parametrize('value,expected', [(False, 'MET'), (True, 'UNKNOWN'), ('false', 'UNKNOWN'), (None, 'UNKNOWN')])
def test_only_explicit_primary_failure_is_positive_db_signal(value, expected):
    ctx = context(4)
    item = ctx['evidence'][5]
    item['metric']['value']['http_status'] = 503
    item['metric']['value']['body']['postgres']['primary_reachable'] = value
    assert build_policy(ctx, build_facts(ctx))['current_runbook_constraints'][0]['status'] == expected


@pytest.mark.parametrize('status', [401, 403, 503, 0])
def test_read_failure_scope_preserves_append_and_requests(status):
    ctx = context(2)
    ctx['evidence'][0]['metric']['value']['status_polls'][2]['http_status'] = status
    facts = build_facts(ctx)
    target = build_policy(ctx, facts)['required_checks'][0]
    assert target['id'] == 'check:status-read' and target['request_ids'] == ['qc-3']
    assert target['status_http'] == {'qc-3': status}
    assert facts['facts']['requests']['value']['per_request']['qc-3']['append'] == 'ACKNOWLEDGED'


@pytest.mark.parametrize('index', [2, 3, 5])
@pytest.mark.parametrize('mode', ['omit', 'invent', 'wrong_evidence'])
def test_omitted_or_misattributed_target_is_withheld_and_not_repaired(tmp_path, index, mode):
    case = cases()[index]
    def change(reply, context):
        check = reply['next_checks'][0]
        if mode == 'omit': check['target_ids'] = []
        if mode == 'invent': check['target_ids'] = ['check:invented']
        if mode == 'wrong_evidence': check['evidence_ids'] = [context['evidence'][5]['evidence_id']]
    result = run(case['bundle'], case['question'], 'synthetic', tmp_path / 'run', client=client(change), model='fake')
    assert result['model_status'] == 'WITHHELD' and result['model_analysis'] is None
    assert (tmp_path / 'run/response.json').exists()
    verify_record(result)


@pytest.mark.parametrize('index', [3, 4])
def test_previous_pg_decision_flip_is_rejected(index):
    ctx = context(index)
    facts = build_facts(ctx)
    result = answer(ctx, facts)
    a = next(a for a in result['runbook_assessments'] if a['runbook_id'] == 'RB-PG-HEALTH')
    if index == 3:
        a['applicability'] = 'NEEDS_MORE_EVIDENCE'
        a['conditions'][0]['status'] = 'UNKNOWN'
    else:
        a['time_scope'] = 'HISTORICAL'
    with pytest.raises(ValueError, match='code-owned investigation'):
        validate_investigation(json.dumps(result), ctx, facts)


def test_target_tags_do_not_prove_explanation_quality():
    ctx = context(2)
    facts = build_facts(ctx)
    result = answer(ctx, facts)
    result['next_checks'][0]['text'] = 'Unrelated prose that does not investigate the tagged read failure.'
    assert validate_investigation(json.dumps(result), ctx, facts)


def test_rehashed_policy_tampering_and_old_protocol_rejected(tmp_path):
    case = cases()[2]
    record = run(case['bundle'], case['question'], 'synthetic', tmp_path / 'run')
    old = copy.deepcopy(record)
    old['version'] = 'ops.grounded-assistance.v3'
    record['input']['investigation_policy']['required_checks'] = []
    for bad in (old, record):
        bad['record_sha256'] = canonical_sha256({k: v for k, v in bad.items() if k != 'record_sha256'})
        with pytest.raises(ValueError): verify_record(bad)
