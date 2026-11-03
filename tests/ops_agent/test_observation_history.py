import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ops_agent.diagnosis_agent import ModelResponse
from ops_agent.evaluation_models import canonical_sha256
from ops_agent.grounded_assistant import public_result, run, validate_investigation, verify_record
from ops_agent.observation_facts import build_facts
from ops_agent.observation_history import MAX_ENTRIES, unknown_catalog
from ops_agent.runbook_assistant import prepare, read_json
from tests.ops_agent.test_grounded_assistant import answer

ROOT = Path(__file__).resolve().parents[2]


def cases():
    return read_json(ROOT / 'ops_agent/fixtures/grounded_novel_v1.json')['cases']


def reply(context, facts):
    return answer(context, facts)


def client(transform=lambda result, context: None):
    def create(payload):
        data = json.loads(payload['input'][1]['content'])
        answer = reply(data['context'], data['code_owned_observations'])
        transform(answer, data['context'])
        return ModelResponse('fake', [], json.dumps(answer), 1, 1, 2)
    return SimpleNamespace(create=create)


def test_mixed_requests_separate_acknowledgement_persistence_and_read_visibility(tmp_path):
    case = cases()[2]
    record = run(case['bundle'], case['question'], 'synthetic', tmp_path / 'run')
    state = record['observations']['facts']['requests']['value']['per_request']
    assert {r['append'] for r in state.values()} == {'ACKNOWLEDGED'}
    assert state['qc-1']['persistence'] == 'CONFIRMED'
    assert state['qc-2']['persistence'] == 'NOT_YET_OBSERVED'
    assert state['qc-3']['persistence'] == 'UNKNOWN'
    assert state['qc-3']['status_read'] == 'UNAVAILABLE'
    gaps = record['input']['unknown_catalog']
    assert not any(g['kind'] == 'REQUEST_APPEND' for g in gaps)
    assert {g['kind'] for g in gaps if g['request_id'] == 'qc-3'} == {
        'REQUEST_PERSISTENCE', 'REQUEST_READ_FAILURE_REASON'}
    assert not any(g['kind'] == 'REQUEST_PERSISTENCE' and g['request_id'] in {'qc-1', 'qc-2'} for g in gaps)


@pytest.mark.parametrize('unknown', [
    'qc-3의 403 원인과 Kafka append 여부를 알 수 없다.',
    'gap:append:qc-3', 'gap:persistence:unknown-request', {'kind': 'REQUEST_APPEND', 'request_id': 'qc-3'},
])
def test_previous_failure_and_invented_unknowns_are_withheld_with_facts_preserved(tmp_path, unknown):
    case = cases()[2]
    def change(answer, context): answer['unknowns'] = [unknown]
    result = run(case['bundle'], case['question'], 'synthetic', tmp_path / 'run', client=client(change), model='fake')
    assert result['model_status'] == 'WITHHELD'
    assert result['model_analysis'] is None
    assert len(result['observations']['facts']['requests']['value']['accepted_ids']) == 3
    assert (tmp_path / 'run/response.json').exists()
    verify_record(result)


def test_only_selected_scoped_gaps_are_rendered_from_code(tmp_path):
    case = cases()[2]
    def change(answer, context):
        answer['unknowns'] = [g['id'] for g in context['unknown_catalog'] if g['request_id'] == 'qc-3']
    record = run(case['bundle'], case['question'], 'synthetic', tmp_path / 'run', client=client(change), model='fake')
    result = public_result(record)
    assert record['model_status'] == 'UNREVIEWED'
    assert len(result['selected_unknowns']) == 2
    assert all(g['request_id'] == 'qc-3' for g in result['selected_unknowns'])
    assert 'append 수락은 확인됐지만' in result['selected_unknowns'][0]['text']


def chain(tmp_path, middle_client=None):
    result = None
    records = []
    for i, case in enumerate(cases()[3:]):
        result = run(case['bundle'], case['question'], 'synthetic', tmp_path / str(i), previous=result,
                     client=middle_client if i == 1 else None, model='fake' if i == 1 else None)
        records.append(result)
    return records


def test_three_step_history_preserves_observed_blocker_independent_of_failed_model(tmp_path):
    def fail(_): raise TimeoutError()
    first, middle, last = chain(tmp_path, SimpleNamespace(create=fail))
    assert middle['model_status'] == 'FAILED'
    history = last['input']['observation_history']
    assert len(history['entries']) == 2 and history['dropped_entries'] == 0
    assert history['entries'][0]['observations']['facts']['database_waits']['value']['blocking_edges'] == [
        {'waiter_pid': 4811, 'blocker_pid': 5922}]
    assert history['entries'][1]['observations']['facts']['database_waits']['value']['blocking_edges'] == []
    assert last['observations']['facts']['database_waits']['value']['blocking_edges'] == []
    assert history['entries'][0]['source_record_sha256'] == first['record_sha256']
    assert history['entries'][1]['source_record_sha256'] == middle['record_sha256']
    assert all('model_analysis' not in entry for entry in history['entries'])
    # Deep copies: later records do not mutate earlier authoritative facts.
    verify_record(first); verify_record(middle); verify_record(last)


@pytest.mark.parametrize('mutation', ['digest', 'foreign', 'duplicate', 'future', 'tail', 'catalog'])
def test_history_and_gap_tampering_rejected_even_with_rehashed_outer_record(tmp_path, mutation):
    record = chain(tmp_path)[-1]
    entry = record['input']['observation_history']['entries'][0]
    if mutation == 'digest': entry['observations']['request_state'] = 'invented'
    if mutation == 'foreign': entry['identity']['incident_id'] = 'foreign'
    if mutation == 'duplicate': record['input']['observation_history']['entries'].insert(0, copy.deepcopy(entry))
    if mutation == 'future':
        entry['observations']['observed_at'] = record['input']['observed_at']
        entry['observations_sha256'] = canonical_sha256(entry['observations'])
        entry['history_id'] = 'history:' + entry['observations_sha256']
    if mutation == 'tail': record['input']['previous']['record_sha256'] = '0' * 64
    if mutation == 'catalog': record['input']['unknown_catalog'].append({'id': 'gap:append:qc-3'})
    record['record_sha256'] = canonical_sha256({k: v for k, v in record.items() if k != 'record_sha256'})
    with pytest.raises(ValueError): verify_record(record)


def test_historical_refs_allowed_in_updates_not_current_hypotheses(tmp_path):
    record = chain(tmp_path)[-1]
    ctx, facts = record['input'], record['observations']
    history = ctx['observation_history']['entries'][0]
    answer = reply(ctx, facts)
    answer['updates'] = [{'text': 'Previous blocking was observed; current graph is empty.',
        'evidence_ids': [ctx['evidence'][3]['evidence_id']], 'history_ids': [history['history_id']]}]
    assert validate_investigation(json.dumps(answer), ctx, facts)['updates']
    answer['hypotheses'] = [{'text': 'Wrong current use.', 'evidence_ids': [history['history_id']]}]
    with pytest.raises(ValueError): validate_investigation(json.dumps(answer), ctx, facts)
    answer['hypotheses'] = []
    answer['updates'][0]['history_ids'] = ['history:invented']
    with pytest.raises(ValueError): validate_investigation(json.dumps(answer), ctx, facts)


def test_history_eviction_is_bounded_and_disclosed(tmp_path):
    case = cases()[0]
    result = None
    for i in range(MAX_ENTRIES + 3):
        doc = copy.deepcopy(case['bundle'])
        def shift(stamp): return (datetime.fromisoformat(stamp) + timedelta(minutes=i)).isoformat()
        doc['bundle_id'] = 'retention-' + str(i)
        doc['collection'] = {**doc['collection'], 'started_at': shift(doc['collection']['started_at']),
                             'completed_at': shift(doc['collection']['completed_at'])}
        for item in doc['evidence']:
            item['source_timestamp'] = shift(item['source_timestamp'])
            item['collected_at'] = shift(item['collected_at'])
            if item['metric']['name'] == 'event_acceptance_and_persistence':
                for poll in item['metric']['value']['status_polls']: poll['at'] = shift(poll['at'])
        result = run(doc, case['question'], 'synthetic', tmp_path / str(i), previous=result)
    verify_record(result)
    history = result['input']['observation_history']
    assert len(history['entries']) == MAX_ENTRIES and history['dropped_entries'] == 2
    assert any(g['kind'] == 'HISTORY_RETENTION' for g in result['input']['unknown_catalog'])
    assert history == public_result(result)['observation_history']


def test_old_protocol_is_not_silently_migrated(tmp_path):
    case = cases()[0]
    old = run(case['bundle'], case['question'], 'synthetic', tmp_path / 'first')
    old['version'] = 'ops.grounded-assistance.v2'
    old['record_sha256'] = canonical_sha256({k: v for k, v in old.items() if k != 'record_sha256'})
    with pytest.raises(ValueError): verify_record(old)
