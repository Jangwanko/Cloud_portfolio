import copy
import hashlib
import json

import pytest

from ops_agent.diagnosis_agent import ModelResponse
from scripts import evaluate_grounded_assistance as runner


def fixture(tmp_path, count=1):
    document = runner.read_json(runner.ROOT / 'ops_agent/fixtures/grounded_assistance_v1.json')
    document['cases'] = document['cases'][:count]
    path = tmp_path / 'fixtures.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    return path, document


@pytest.mark.parametrize('mode', ['too_many', 'parent_path', 'duplicate', 'missing_predecessor'])
def test_invalid_evaluation_cannot_spend_api_budget(tmp_path, monkeypatch, mode):
    path, document = fixture(tmp_path, 2)
    if mode == 'too_many': document['cases'] *= 5
    if mode == 'parent_path': document['cases'][0]['id'] = '../escaped'
    if mode == 'duplicate': document['cases'][1]['id'] = document['cases'][0]['id']
    if mode == 'missing_predecessor': document['cases'] = document['cases'][1:]
    path.write_text(json.dumps(document), encoding='utf-8')
    monkeypatch.setattr(runner, 'load_openai_configuration', lambda _: pytest.fail('unexpected API setup'))
    with pytest.raises(ValueError):
        runner.evaluate(tmp_path / 'run', execute=True, fixture_path=path)
    assert not (tmp_path / 'run').exists()


def test_rubric_is_not_sent_and_failed_responses_are_never_retried(tmp_path, monkeypatch):
    path, document = fixture(tmp_path, 2)
    for case in document['cases']:
        case['review'] = ['PRIVATE_EXPECTATION_MARKER_NEVER_SEND']
    path.write_text(json.dumps(document), encoding='utf-8')
    calls = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['max_retries'] == 0
        def create(self, request):
            calls.append(copy.deepcopy(request))
            assert 'PRIVATE_EXPECTATION_MARKER_NEVER_SEND' not in json.dumps(request)
            return ModelResponse('fake', [], '{invalid', 1, 1, 2)
    monkeypatch.setattr(runner, 'load_openai_configuration', lambda _: ('not-a-real-key', 'fake'))
    monkeypatch.setattr(runner, 'OpenAIResponsesClient', Client)
    directory = tmp_path / 'run'
    result = runner.evaluate(directory, execute=True, fixture_path=path)
    assert len(calls) == result['calls_attempted'] == 2
    assert all(c['model_status'] == 'WITHHELD' and c['fixed_facts_equal'] for c in result['cases'])
    for case in document['cases']:
        assert (directory / case['id'] / 'model/response.json').exists()
        assert (directory / case['id'] / 'model/failure.json').exists()
    plan = runner.read_json(directory / 'plan.json')
    for name, digest in plan['source_sha256'].items():
        assert hashlib.sha256((directory / 'source' / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('repeats', [0, 4, True])
def test_invalid_repeat_budget_cannot_call_api(tmp_path, monkeypatch, repeats):
    path, _ = fixture(tmp_path)
    monkeypatch.setattr(runner, 'load_openai_configuration', lambda _: pytest.fail('unexpected API setup'))
    with pytest.raises(ValueError):
        runner.evaluate(tmp_path / 'run', execute=True, fixture_path=path, repeats=repeats)
    assert not (tmp_path / 'run').exists()


def test_exact_repeats_freeze_previous_input_and_preserve_every_failure(tmp_path, monkeypatch):
    path, document = fixture(tmp_path, 2)
    calls = []
    class Client:
        def __init__(self, **kwargs): assert kwargs['max_retries'] == 0
        def create(self, request):
            calls.append(copy.deepcopy(request))
            return ModelResponse('fake', [], '{invalid', 1, 1, 2)
    monkeypatch.setattr(runner, 'load_openai_configuration', lambda _: ('fake-key', 'fake'))
    monkeypatch.setattr(runner, 'OpenAIResponsesClient', Client)
    result = runner.evaluate(tmp_path / 'run', execute=True, fixture_path=path, repeats=2)
    assert result['calls_attempted'] == len(calls) == 4
    assert calls[0] == calls[1] and calls[2] == calls[3]
    followup = json.loads(calls[2]['input'][1]['content'])['context']
    assert followup['previous']['model_status'] == 'NOT_REQUESTED'
    assert len(followup['observation_history']['entries']) == 1
    assert all(c['fixed_history_equal'] and c['fixed_facts_equal'] for c in result['cases'])
    assert all(c['analysis_identical'] is None for c in result['comparisons'])
    for case in document['cases']:
        for repeat in (1, 2):
            assert (tmp_path / 'run' / case['id'] / f'repeat-{repeat}/response.json').exists()
