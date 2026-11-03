"""Freeze bounded evaluation cases, compare fixed observations/runbooks with optional Luna.

No cluster access or writes. --execute makes one call per selected snapshot, no retries.
The rubric is preserved for human review, never sent as an answer key to the model.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ops_agent.diagnosis_agent import OpenAIResponsesClient, load_openai_configuration
from ops_agent.grounded_assistant import SOURCE_HASHES, run
from ops_agent.evaluation_models import canonical_sha256
from ops_agent.runbook_assistant import read_json, save


def evaluate(directory, execute=False, case_ids=None, fixture_path=None, regression=False, repeats=1):
    fixture_path = fixture_path or ROOT / 'ops_agent/fixtures/grounded_assistance_v1.json'
    fixture = read_json(fixture_path)
    evaluation_class = ('Development regression using previously evaluated cases; not unseen evaluation.' if regression
                        else fixture.get('evaluation_class', 'synthetic development cases, not held-out or production evidence'))
    if case_ids:
        if not set(case_ids) <= {c['id'] for c in fixture['cases']}:
            raise ValueError('unknown case selection')
        fixture['cases'] = [c for c in fixture['cases'] if c['id'] in case_ids]
    if not 1 <= len(fixture['cases']) <= 8:
        raise ValueError('select 1-8 snapshots')
    if type(repeats) is not int or not 1 <= repeats <= 3 or len(fixture['cases']) * repeats > 16:
        raise ValueError('1-3 repeats and at most 16 calls per frozen evaluation')
    if repeats > 1:
        evaluation_class = 'Development exact-input repeats; frozen code-only history; not unseen or live sequence evaluation.'
    seen = set()
    for case in fixture['cases']:
        if case['id'] in seen or Path(case['id']).name != case['id'] or case['id'] in {'.', '..'}:
            raise ValueError('unique simple case IDs required')
        if case['previous_case'] and case['previous_case'] not in seen:
            raise ValueError('include the preceding case for follow-up evaluation')
        seen.add(case['id'])
    directory.mkdir(parents=True, exist_ok=False)
    save(directory / 'fixtures.json', fixture)
    frozen = {}
    paths = list(SOURCE_HASHES) + ['ops_agent/models.py', 'ops_agent/evaluation_models.py',
        'ops_agent/runbook_catalog.json', 'docs/RUNBOOK.md', 'scripts/evaluate_grounded_assistance.py']
    for name in paths:
        data = (ROOT / name).read_bytes()
        frozen[name] = hashlib.sha256(data).hexdigest()
        destination = directory / 'source' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as handle:
            handle.write(data)
    def assert_frozen():
        if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest for name, digest in frozen.items()):
            raise ValueError('evaluated implementation changed after freeze')
    save(directory / 'plan.json', {'at': datetime.now(timezone.utc).isoformat(),
        'max_api_calls': len(fixture['cases']) * repeats if execute else 0, 'automatic_retry': False,
        'repeats': repeats, 'history_mode': 'frozen_code_only' if repeats > 1 else 'sequential_model',
        'max_output_tokens_per_call': 6000, 'timeout_seconds_per_call': 45,
        'provenance': evaluation_class,
        'fixture_sha256': hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        'source_sha256': frozen,
        'acceptance_before_calls': ['fixed state matches fixture expectation',
            'model cannot alter code-owned facts', 'retain each first response/failure',
            'code policy and target-ID coverage are not model diagnosis accuracy',
            'manually verify tagged checks actually investigate read failures, observed blocking and worker unavailability when required',
            'manual review of useful additional information and false claims; no automatic semantic pass']})
    baseline, models, rows, comparisons = {}, {}, [], []
    # Every deterministic result and comparison source is frozen before the first call.
    for case in fixture['cases']:
        assert_frozen()
        identifier = case['id']
        baseline[identifier] = run(case['bundle'], case['question'], 'synthetic', directory / identifier / 'baseline',
                                   previous=baseline.get(case['previous_case']))
        if baseline[identifier]['observations']['request_state'] != case['expected_state']:
            raise ValueError('fixed-observation acceptance failed before API calls')
    client, model = None, None
    if execute:
        key, model = load_openai_configuration(ROOT)
        client = OpenAIResponsesClient(api_key=key, timeout_seconds=45, max_retries=0)
    for case in fixture['cases']:
        assert_frozen()
        identifier = case['id']
        base = baseline[identifier]
        row = {'case': identifier, 'baseline_state': base['observations']['request_state'],
            'baseline_runbooks': [b['id'] for b in base['input']['runbooks']],
            'rubric': case['review'], 'semantic_review': 'NOT_PERFORMED', 'model_status': 'NOT_REQUESTED'}
        requests, analyses = [], []
        for repetition in range(1, repeats + 1):
            assert_frozen()
            current = dict(row, repetition=repetition)
            if execute:
                target = directory / identifier / ('model' if repeats == 1 else f'repeat-{repetition}')
                previous = (baseline if repeats > 1 else models).get(case['previous_case'])
                result = run(case['bundle'], case['question'], 'synthetic', target,
                    previous=previous, client=client, model=model)
                models[identifier] = result
                request_hash = canonical_sha256(read_json(target / 'request.json'))
                requests.append(request_hash)
                analyses.append(result['model_analysis'])
                current.update(model_status=result['model_status'], usage=result['usage'],
                    request_sha256=request_hash,
                    fixed_history_equal=base['input']['observation_history'] == result['input']['observation_history'],
                    fixed_policy_equal=base['input']['investigation_policy'] == result['input']['investigation_policy'],
                    fixed_facts_equal=base['observations'] == result['observations'])
            rows.append(current)
            print(json.dumps({'case': identifier, 'repetition': repetition,
                'state': current['baseline_state'], 'model_status': current['model_status']}), flush=True)
        if execute and repeats > 1:
            if len(set(requests)) != 1:
                raise ValueError('repeat requests differ; exact-input comparison invalid')
            valid = all(a is not None for a in analyses)
            comparisons.append({'case': identifier, 'identical_request': True,
                'all_contracts_accepted': valid,
                'analysis_identical': len({canonical_sha256(a) for a in analyses}) == 1 if valid else None,
                'runbook_decisions_identical': len({canonical_sha256(sorted([
                    {'id': b['runbook_id'], 'applicability': b['applicability'], 'time_scope': b['time_scope'],
                     'conditions': sorted([(c['id'], c['status']) for c in b['conditions']])}
                    for b in a['runbook_assessments']], key=lambda b: b['id'])) for a in analyses}) == 1 if valid else None,
                'selected_gaps_identical': len({canonical_sha256(sorted(a['unknowns'])) for a in analyses}) == 1 if valid else None,
                'semantic_consistency': 'MANUAL_REVIEW_REQUIRED'})
    assert_frozen()
    summary = {'status': 'MANUAL_SEMANTIC_REVIEW_REQUIRED' if execute else 'BASELINE_PREPARED',
        'evaluation_class': evaluation_class,
        'model': model, 'calls_attempted': len(rows) if execute else 0, 'cases': rows, 'comparisons': comparisons,
        'boundary': 'Typed contract/fact isolation checks do not establish truthful prose or operator time savings.'}
    save(directory / 'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--case', action='append', dest='case_ids', help='Select development case; include its predecessor')
    parser.add_argument('--fixtures', type=Path, help='Separate frozen evaluation fixture; up to eight snapshots')
    parser.add_argument('--regression', action='store_true', help='Explicitly label reused evaluation cases as development regression')
    parser.add_argument('--repeat', type=int, default=1, help='2-3 exact-input repeats using frozen code-only history; max 16 total calls')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    result = evaluate(args.output_dir, args.execute, args.case_ids, args.fixtures, args.regression, args.repeat)
    return int(any(c['model_status'] in {'FAILED', 'WITHHELD'} or c.get('fixed_facts_equal') is False
                   for c in result['cases']))


if __name__ == '__main__':
    sys.exit(main())
