import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from ops_agent.diagnosis_agent import ModelResponse
from ops_agent.grounded_assistant import run, validate_investigation, verify_record, render
from ops_agent.grounded_assistant import output_schema
from ops_agent.diagnosis_agent import _parse_model_response
from ops_agent.investigation_policy import build_policy
from ops_agent.observation_facts import build_facts
from ops_agent.runbook_assistant import prepare, read_json

ROOT = Path(__file__).resolve().parents[2]


def cases():
    return read_json(ROOT / 'ops_agent/fixtures/grounded_assistance_v1.json')['cases']


def context(index=0):
    case = cases()[index]
    return prepare(case['bundle'], case['question'], 'synthetic')


def answer(ctx, facts):
    result = {'based_on_request_state': facts['request_state'],
        'acceptance_basis': facts['contracts']['accepted_202'],
        'timing_basis': facts['contracts']['status_poll_timing'],
        'lag_basis': facts['contracts']['lag_time_basis'],
        'hypotheses': [], 'unknowns': ['gap:causal-link'], 'updates': [],
        'next_checks': [{'text': 'Inspect the blocking session transaction metadata.',
            'evidence_ids': [ctx['evidence'][0]['evidence_id']], 'runbook_id': None, 'target_ids': []}],
        'runbook_assessments': [{'runbook_id': b['id'], 'applicability': 'NEEDS_MORE_EVIDENCE', 'time_scope': 'CURRENT',
            'rationale': 'Procedure requires more evidence.', 'conditions': [
                {'id': c['id'], 'status': 'UNKNOWN', 'evidence_ids': []} for c in b['conditions']]}
            for b in ctx['runbooks']]}
    policy = build_policy(ctx, facts)
    for fixed in policy['current_runbook_constraints']:
        a = next(a for a in result['runbook_assessments'] if a['runbook_id'] == fixed['runbook_id'])
        a.update(applicability=fixed['applicability'], time_scope=fixed['time_scope'])
        a['conditions'] = [{'id': fixed['condition_id'], 'status': fixed['status'], 'evidence_ids': fixed['evidence_ids']}]
    if policy['required_checks']:
        result['next_checks'] = [{'text': t['purpose'], 'evidence_ids': t['evidence_ids'],
                                 'runbook_id': None, 'target_ids': [t['id']]} for t in policy['required_checks']]
    return result


def fake_client(mutate=lambda a: None):
    def create(payload):
        value = json.loads(payload['input'][1]['content'])
        reply = answer(value['context'], value['code_owned_observations'])
        mutate(reply)
        return ModelResponse('fake', [], json.dumps(reply), 1, 1, 2)
    return SimpleNamespace(create=create)


def test_wire_schema_requires_fields_and_limits_ids_to_context(tmp_path):
    case = cases()[0]
    result = run(case['bundle'], case['question'], 'synthetic', tmp_path / 'model',
        client=fake_client(), model='fake')
    request = read_json(tmp_path / 'model/request.json')
    format_ = request['text']['format']
    assert format_['strict'] and format_['type'] == 'json_schema'
    schema = format_['schema']

    def check(node):
        if isinstance(node, dict):
            assert 'default' not in node
            if node.get('type') == 'object':
                assert set(node['required']) == set(node['properties'])
                assert node['additionalProperties'] is False
            for child in node.values(): check(child)
        if isinstance(node, list):
            for child in node: check(child)
    check(schema)
    defs = schema['$defs']
    assert 'runbook_id' in defs['TargetedCheck']['required']
    assert None in defs['TargetedCheck']['properties']['runbook_id']['enum']
    assert defs['Update']['properties']['history_ids']['maxItems'] == 0
    assert defs['Claim']['properties']['evidence_ids']['items']['enum'] == sorted(
        e['evidence_id'] for e in result['input']['evidence'] if e['usable'])
    assert schema['properties']['unknowns']['items']['enum'] == sorted(
        g['id'] for g in result['input']['unknown_catalog'])


def test_wire_schema_empty_choices_and_historical_citation_scope(tmp_path):
    first, later = cases()[:2]
    previous = run(first['bundle'], first['question'], 'synthetic', tmp_path / 'first')
    result = run(later['bundle'], later['question'], 'synthetic', tmp_path / 'later', previous=previous)
    ctx = copy.deepcopy(result['input'])
    ctx['runbooks'] = []
    ctx['unknown_catalog'] = []
    for item in ctx['evidence']: item['usable'] = False
    schema = output_schema(ctx, result['observations'])
    assert schema['properties']['hypotheses']['maxItems'] == 0
    assert schema['properties']['runbook_assessments']['maxItems'] == 0
    assert schema['properties']['unknowns']['maxItems'] == 0
    assert schema['$defs']['TargetedCheck']['properties']['runbook_id']['enum'] == [None]
    historical = ctx['observation_history']['entries'][0]['history_id']
    assert historical in schema['$defs']['Update']['properties']['history_ids']['items']['enum']
    assert historical not in schema['$defs']['Update']['properties']['evidence_ids']['items']['enum']
    assert '"enum": []' not in json.dumps(schema)


@pytest.mark.parametrize('mode', ['incomplete', 'refusal', 'failed', 'missing_status'])
def test_noncompleted_or_refused_valid_json_is_withheld_without_retry(tmp_path, mode):
    calls = []
    def create(request):
        calls.append(request)
        value = json.loads(request['input'][1]['content'])
        raw = {'id': 'fake', 'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps(answer(value['context'], value['code_owned_observations']))}]}]}
        if mode == 'incomplete':
            raw.update(status='incomplete', incomplete_details={'reason': 'max_output_tokens'})
        if mode == 'failed': raw['status'] = 'failed'
        if mode == 'missing_status': raw.pop('status')
        if mode == 'refusal': raw['output'][0]['content'].append({'type': 'refusal', 'refusal': 'declined'})
        return _parse_model_response(raw)
    case = cases()[0]
    result = run(case['bundle'], case['question'], 'synthetic', tmp_path / 'model',
        client=SimpleNamespace(create=create), model='fake')
    assert len(calls) == 1 and result['model_status'] == 'WITHHELD'
    assert result['model_analysis'] is None
    assert read_json(tmp_path / 'model/response.json')['output']
    verify_record(result)


@pytest.mark.parametrize('index', range(4))
def test_frozen_synthetic_states_and_no_global_health_claim(index):
    fact = build_facts(context(index))
    assert fact['request_state'] == cases()[index]['expected_state']
    assert 'severity' not in fact and 'recovered' not in fact


def test_latest_lag_history_and_poll_bound_are_distinct():
    fact = build_facts(context(1))
    assert fact['facts']['lag']['value']['latest_total'] == 0
    assert fact['facts']['lag']['value']['window_peak_total'] == 12
    assert fact['facts']['requests']['value']['accepted_to_status_observed_seconds'] == {'req-1': 80}
    assert fact['contracts']['status_poll_timing'] == 'OBSERVATION_BOUND_NOT_EXACT_COMMIT_LATENCY'
    assert fact['facts']['database_waits']['value']['blocking_edges'] == []
    assert build_facts(context())['facts']['database_waits']['value']['blocking_edges'] == [
        {'waiter_pid': 101, 'blocker_pid': 202}]


def test_db_server_clock_skew_does_not_override_collector_freshness():
    ctx = context()
    db = ctx['evidence'][3]
    db['metric']['value']['captured_at'] = '2026-11-02T00:00:22+00:00'
    facts = build_facts(ctx)
    assert facts['facts']['database_waits']['status'] == 'KNOWN'
    assert facts['facts']['database_waits']['value']['server_captured_at'] != db['source_timestamp']
    db['usable'] = False
    assert build_facts(ctx)['facts']['database_waits']['status'] == 'UNKNOWN'


@pytest.mark.parametrize('mode', ['stale', 'partial', 'duplicate', 'missing_poll', 'mismatched_id',
                                  'future_poll', 'old_poll', 'duplicate_event', 'missing_event'])
def test_bad_request_evidence_cannot_establish_persistence(mode):
    ctx = context(1)
    item = ctx['evidence'][0]
    value = item['metric']['value']
    if mode == 'stale': item['usable'] = False
    if mode == 'partial': item['coverage']['complete'] = False
    if mode == 'duplicate': value['accepted'] *= 2
    if mode == 'missing_poll': value['status_polls'] = []
    if mode == 'mismatched_id': value['status_polls'][0]['body']['request_id'] = 'wrong'
    if mode == 'future_poll': value['status_polls'][0]['at'] = '2026-11-02T00:01:21+00:00'
    if mode == 'old_poll': value['status_polls'][0]['at'] = '2026-11-02T00:00:01+00:00'
    if mode == 'missing_event': value['status_polls'][0]['body'].pop('event_id')
    if mode == 'duplicate_event':
        value['accepted'].append({**value['accepted'][0], 'request_id': 'req-2'})
        row = copy.deepcopy(value['status_polls'][0])
        row['request_id'] = row['body']['request_id'] = 'req-2'
        value['status_polls'].append(row)
    fact = build_facts(ctx)
    assert fact['request_state'] == 'UNKNOWN'


@pytest.mark.parametrize('http_status', [401, 403, 500, 503, 0])
def test_read_error_is_unknown_not_pending(http_status):
    ctx = context()
    ctx['evidence'][0]['metric']['value']['status_polls'][0]['http_status'] = http_status
    assert build_facts(ctx)['request_state'] == 'UNKNOWN'


@pytest.mark.parametrize('mode', ['negative', 'nan', 'unsorted', 'partial', 'duplicate', 'stale', 'missing', 'future', 'old'])
def test_invalid_lag_never_becomes_zero(mode):
    ctx = context(1)
    item = ctx['evidence'][1]
    if mode == 'negative': item['metric']['value'][0]['value'] = '-1'
    if mode == 'nan': item['metric']['value'][0]['value'] = 'NaN'
    if mode == 'unsorted': item['metric']['value'].reverse()
    if mode == 'partial': item['coverage']['expected_count'] = 2
    if mode == 'duplicate': ctx['evidence'].append(copy.deepcopy(item))
    if mode == 'stale': item['usable'] = False
    if mode == 'missing': ctx['evidence'].remove(item)
    if mode == 'future': item['metric']['value'][-1]['timestamp'] += 1
    if mode == 'old':
        for s in item['metric']['value']: s['timestamp'] -= 60
    assert build_facts(ctx)['facts']['lag']['status'] == 'UNKNOWN'


@pytest.mark.parametrize('field,value', [
    ('based_on_request_state', 'ACCEPTED_REQUESTS_PENDING'),
    ('acceptance_basis', 'APPEND_UNCONFIRMED'),
    ('timing_basis', 'EXACT_COMMIT_LATENCY'),
    ('lag_basis', 'HISTORICAL_MAX_IS_CURRENT'),
    ('impact', {'severity': 'DEGRADED'}),
    ('observations', {'request_state': 'NORMAL'}),
])
def test_model_cannot_overwrite_facts_or_use_old_contract(field, value, tmp_path):
    case = cases()[1]
    baseline = run(case['bundle'], case['question'], 'synthetic', tmp_path / 'baseline')
    result = run(case['bundle'], case['question'], 'synthetic', tmp_path / 'model',
        client=fake_client(lambda a: a.update({field: value})), model='fake')
    assert result['model_status'] == 'WITHHELD' and result['model_analysis'] is None
    assert result['observations'] == baseline['observations']
    assert (tmp_path / 'model/response.json').exists()
    verify_record(result)


def test_transport_failure_preserves_facts_and_followup(tmp_path):
    def fail(_): raise TimeoutError('private transport details')
    first, later = cases()[:2]
    previous = run(first['bundle'], first['question'], 'synthetic', tmp_path / 'failed',
        client=SimpleNamespace(create=fail), model='fake')
    assert previous['model_status'] == 'FAILED'
    assert 'private transport details' not in (tmp_path / 'failed/failure.json').read_text()
    result = run(later['bundle'], later['question'], 'synthetic', tmp_path / 'later', previous=previous)
    assert result['changes']['previous_request_state'] == 'ACCEPTED_REQUESTS_PENDING'
    assert result['changes']['current_request_state'] == 'SAMPLED_REQUESTS_PERSISTED'
    assert '관측한 요청의 상태: SAMPLED_REQUESTS_PERSISTED' in render(result)


def test_free_text_stays_unreviewed_even_when_contract_fields_pass(tmp_path):
    case = cases()[0]
    def mutate(reply): reply['next_checks'][0]['text'] = 'Deliberately false prose: 202 proves DB commit.'
    record = run(case['bundle'], case['question'], 'synthetic', tmp_path / 'run',
        client=fake_client(mutate), model='fake')
    assert record['model_status'] == 'UNREVIEWED'
    assert '검토가 필요한 모델 해석' in render(record)
    assert record['observations']['request_state'] == 'ACCEPTED_REQUESTS_PENDING'
    # Explicit limit: typed checks are not a natural-language truth verifier.


def test_followup_rejects_old_foreign_and_tampered_records(tmp_path):
    first, later = cases()[:2]
    previous = run(first['bundle'], first['question'], 'synthetic', tmp_path / 'first')
    with pytest.raises(ValueError, match='advance'):
        run(first['bundle'], first['question'], 'synthetic', tmp_path / 'old', previous=previous)
    foreign = copy.deepcopy(later['bundle'])
    foreign['incident_id'] = 'another'
    with pytest.raises(ValueError, match='different'):
        run(foreign, later['question'], 'synthetic', tmp_path / 'foreign', previous=previous)
    previous['observations']['request_state'] = 'SAMPLED_REQUESTS_PERSISTED'
    with pytest.raises(ValueError, match='tampered'):
        run(later['bundle'], later['question'], 'synthetic', tmp_path / 'tampered', previous=previous)


def test_unseen_hypothesis_reference_is_rejected():
    ctx = context()
    facts = build_facts(ctx)
    reply = answer(ctx, facts)
    reply['hypotheses'] = [{'text': 'Invented evidence', 'evidence_ids': ['unseen']}]
    with pytest.raises(ValueError): validate_investigation(json.dumps(reply), ctx, facts)


def test_past_lag_can_support_historical_investigation_but_not_current_delay():
    ctx = context(1)
    facts = build_facts(ctx)
    reply = answer(ctx, facts)
    assessment = next(a for a in reply['runbook_assessments'] if a['runbook_id'] == 'RB-WORKER-LAG')
    assessment['applicability'] = 'APPLICABLE'
    assessment['conditions'][0].update(status='MET', evidence_ids=[ctx['evidence'][1]['evidence_id']])
    with pytest.raises(ValueError, match='historical lag'):
        validate_investigation(json.dumps(reply), ctx, facts)
    assessment['time_scope'] = 'HISTORICAL'
    assert validate_investigation(json.dumps(reply), ctx, facts)['runbook_assessments']


def test_cli_defaults_to_fixed_facts_without_api(tmp_path):
    source = tmp_path / 'bundle.json'
    source.write_text(json.dumps(cases()[1]['bundle']), encoding='utf-8')
    result = subprocess.run([sys.executable, 'scripts/assist_incident.py', '--bundle', str(source),
        '--question', 'investigate', '--provenance', 'synthetic', '--output-dir', str(tmp_path / 'run')],
        cwd=ROOT, capture_output=True, encoding='utf-8', check=True)
    value = json.loads(result.stdout)
    assert value['observations']['request_state'] == 'SAMPLED_REQUESTS_PERSISTED'
    assert value['model_status'] == 'NOT_REQUESTED'
    assert not (tmp_path / 'run/request.json').exists()
