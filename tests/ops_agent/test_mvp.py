import copy
import json
from pathlib import Path

import pytest

from ops_agent.grounded_assistant import public_result, render, run
from ops_agent.incident_experience import archive, search, verify_experience
from ops_agent.runbook_assistant import read_json, save
from scripts import ops_agent_mvp as mvp
from tests.ops_agent.test_grounded_assistant import cases, fake_client


def test_default_outputs_hide_known_false_history_but_archive_preserves_it(tmp_path):
    marker = 'KNOWN_FALSE_PAST_PERSISTENCE_AND_ZERO_LAG'
    case = cases()[0]
    def change(reply):
        reply['updates'] = [{'text': marker, 'evidence_ids': [reply['next_checks'][0]['evidence_ids'][0]], 'history_ids': []}]
    record = run(case['bundle'], case['question'], 'synthetic', tmp_path / 'first', client=fake_client(change), model='fake')
    assert marker in (tmp_path / 'first/response.json').read_text(encoding='utf-8')
    assert marker in (tmp_path / 'first/record.json').read_text(encoding='utf-8')
    assert marker not in json.dumps(public_result(record))
    assert marker not in render(record)
    following = cases()[1]
    later = run(following['bundle'], following['question'], 'synthetic', tmp_path / 'later', previous=record)
    assert marker not in json.dumps(later['input']['previous'])
    request = read_json(tmp_path / 'first/request.json')
    assert request['text']['format']['schema']['properties']['updates']['maxItems'] == 0


@pytest.mark.parametrize('mode', ['foreign_book', 'foreign_evidence', 'approval_false'])
def test_recovery_proposals_are_cited_operator_only_suggestions(tmp_path, mode):
    case = cases()[0]
    def change(reply):
        proposal = {'text': 'Operator may inspect and release an identified owned blocker after approval.',
            'evidence_ids': reply['next_checks'][0]['evidence_ids'], 'runbook_id': None,
            'approval_required': True, 'verification': 'Reobserve accepted request persistence and blocking graph.'}
        if mode == 'foreign_book': proposal['runbook_id'] = 'INVENTED'
        if mode == 'foreign_evidence': proposal['evidence_ids'] = ['INVENTED']
        if mode == 'approval_false': proposal['approval_required'] = False
        reply['recovery_proposals'] = [proposal]
    record = run(case['bundle'], case['question'], 'synthetic', tmp_path / 'run', client=fake_client(change), model='fake')
    assert record['model_status'] == 'WITHHELD'


def test_preflight_block_cannot_reach_fault_runner(tmp_path, monkeypatch):
    target = mvp.ROOT / 'results/ops-agent/shadow/unit-no-mutation'
    monkeypatch.setattr(mvp, 'preflight', lambda _: {'status': 'BLOCKED', 'blockers': ['PG_NOT_READY']})
    monkeypatch.setattr(mvp, 'save', lambda *_: None)
    monkeypatch.setattr(mvp.lock, 'run', lambda *_a, **_k: pytest.fail('unexpected injection'))
    monkeypatch.setattr(mvp.worker, 'run', lambda *_a, **_k: pytest.fail('unexpected injection'))
    assert mvp.execute(target, 'lock', 'test approval') == 1
    with pytest.raises(ValueError, match='approval'):
        mvp.execute(target, 'lock', '')
    with pytest.raises(ValueError, match='scenarios'):
        mvp.execute(target, 'unreviewed', 'test approval')


def test_preflight_rejects_remote_endpoint_before_any_service_observation(tmp_path, monkeypatch):
    calls = []
    def kube(*args, **kwargs):
        calls.append(args)
        assert args[:2] == ('config', 'view')
        return {'clusters': [{'cluster': {'server': 'https://remote.example.test'}}]}
    monkeypatch.setattr(mvp.worker, 'kube', kube)
    monkeypatch.setattr(mvp, 'collect_bundle', lambda **_: pytest.fail('unexpected collection'))
    result = mvp.preflight(tmp_path / 'preflight')
    assert result['status'] == 'BLOCKED' and len(calls) == 1


def experience_fixture(root):
    directory = root / 'results/ops-agent/shadow/test-case'
    fixture = read_json(mvp.ROOT / 'ops_agent/fixtures/grounded_novel_v1.json')['cases'][3:]
    previous = None
    for phase, case in zip(('baseline', 'during', 'recovery'), fixture):
        document = copy.deepcopy(case['bundle'])
        if phase == 'during':
            next(e for e in document['evidence'] if e['metric']['name'] == 'kubernetes_worker_deployment_observation')['metric']['value']['ready_replicas'] = 0
        if phase == 'recovery':
            value = next(e for e in document['evidence'] if e['metric']['name'] == 'event_acceptance_and_persistence')['metric']['value']
            for index, row in enumerate(value['status_polls']):
                row.update(http_status=200, body={'request_id': row['request_id'], 'status': 'persisted', 'event_id': str(index + 1)})
        previous = run(document, 'Review currently observed evidence.', 'recorded', directory / 'investigation' / phase, previous=previous)
        save(directory / 'snapshots' / phase / 'bundle-model.json', document)
    save(directory / 'experiment-plan-private.json', {'incident_id': previous['input']['incident_id']})
    save(directory / 'approval.json', {'operator_approval': 'unit fixture, no real injection'})
    save(directory / 'configuration-verification.json', {'verified': True, 'fields': {k: True for k in (
        'worker_fields_sha256', 'pg_fields_sha256', 'pg_pods', 'argocd_automated')}})
    for phase in ('before', 'after'): save(directory / f'configuration-{phase}.json', {'unit': True})
    for name, value in {
        'recovery.json': {'verified': True}, 'restored.json': {'fields_verified': True},
        'plan-private.json': {'test': True}, 'rollback.json': {'test': True}, 'summary.json': {'test': True},
    }.items(): save(directory / 'drill' / name, value)
    return directory


def test_case_storage_reuse_is_model_independent_and_draft_only(tmp_path):
    directory = experience_fixture(tmp_path)
    record = archive(directory, 'worker', {'cause_correctness': 'PENDING', 'incorrect_hypotheses': 'PENDING'}, root=tmp_path)
    assert record['status'] == 'RECOVERY_VERIFIED_REVIEW_PENDING'
    assert read_json(directory / 'runbook-draft.json')['published_to_catalog'] is False
    result = search(tmp_path, 'Worker startup')
    assert result[0]['incident_id'] == record['incident_id']
    assert result[0]['semantic_review']['cause_correctness'] == 'PENDING'
    assert not search(tmp_path, 'Worker', exclude_incident=record['incident_id'])
    # Reuse does not require a model client or an API call.
    source = directory / 'drill/recovery.json'
    source.write_text('{"verified":false}', encoding='utf-8')
    with pytest.raises(ValueError, match='artifact'):
        verify_experience(record, tmp_path)


def test_restoration_and_model_failure_cannot_be_promoted_to_recovery(tmp_path):
    directory = experience_fixture(tmp_path)
    (directory / 'drill/restored.json').write_text('{"fields_verified":false}', encoding='utf-8')
    with pytest.raises(ValueError, match='recovery'):
        archive(directory, 'worker', {'cause_correctness': 'PENDING'}, root=tmp_path)


def test_omitted_deployment_ready_count_uses_explicit_pod_evidence_without_zero_imputation(tmp_path):
    directory = experience_fixture(tmp_path)
    from ops_agent.evaluation_models import canonical_sha256
    from ops_agent.investigation_policy import build_policy
    path = directory / 'investigation/during/record.json'
    record = read_json(path)
    item = next(e for e in record['input']['evidence'] if e['metric']['name'] == 'kubernetes_worker_deployment_observation')
    item['metric']['value']['ready_replicas'] = None
    record['input']['investigation_policy'] = build_policy(record['input'], record['observations'])
    assert record['input']['investigation_policy']['worker_observation']['status'] == 'UNKNOWN'
    record['record_sha256'] = canonical_sha256({k: v for k, v in record.items() if k != 'record_sha256'})
    path.write_text(json.dumps(record), encoding='utf-8')
    following = directory / 'investigation/recovery/record.json'
    recovery = read_json(following)
    recovery['input']['previous']['record_sha256'] = record['record_sha256']
    recovery['input']['observation_history']['entries'][-1]['source_record_sha256'] = record['record_sha256']
    recovery['changes']['previous_record_sha256'] = record['record_sha256']
    recovery['record_sha256'] = canonical_sha256({k: v for k, v in recovery.items() if k != 'record_sha256'})
    following.write_text(json.dumps(recovery), encoding='utf-8')
    save(directory / 'drill/evidence.json', {'get_worker_status': {'result': {
        'pods': [{'containers': [{'ready': False}]}]}}})
    record = archive(directory, 'worker', {'cause_correctness': 'PENDING'}, root=tmp_path)
    assert record['status'] == 'RECOVERY_VERIFIED_REVIEW_PENDING'
