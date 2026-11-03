"""Model-independent local experiment records and a small lexical case lookup.

This sidecar reuses grounded records; it does not forge a calibrated backlog
IncidentRecord activation/closure for a small controlled fault.
"""
from pathlib import Path
import re

from ops_agent.evaluation_models import canonical_sha256
from ops_agent.grounded_assistant import verify_record, visible_analysis
from ops_agent.runbook_assistant import ROOT, read_json, save


def artifact(path, root=ROOT):
    path = path.resolve()
    relative = path.relative_to(root.resolve()).as_posix()
    import hashlib
    return {'path': relative, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def verify_experience(record, root=ROOT):
    body = {k: v for k, v in record.items() if k != 'record_sha256'}
    if record.get('version') != 'ops.incident-experience.v1' or canonical_sha256(body) != record.get('record_sha256'):
        raise ValueError('invalid experience digest')
    for item in record['artifacts']:
        path = (root / item['path']).resolve()
        if not path.is_relative_to(root.resolve()) or artifact(path, root) != item:
            raise ValueError('missing, foreign or modified experience artifact')


def assemble(directory, scenario, review, root=ROOT):
    if scenario not in {'lock', 'worker'}:
        raise ValueError('unknown local scenario')
    plan = read_json(directory / 'experiment-plan-private.json')
    approval = read_json(directory / 'approval.json')
    configuration = read_json(directory / 'configuration-verification.json')
    if configuration.get('verified') is not True or any(configuration.get('fields', {}).get(k) is not True for k in
            ('worker_fields_sha256', 'pg_fields_sha256', 'pg_pods', 'argocd_automated')):
        raise ValueError('Kubernetes/Argo configuration restoration not verified')
    records, references = [], []
    for phase in ('baseline', 'during', 'recovery'):
        path = directory / 'investigation' / phase / 'record.json'
        record = read_json(path)
        verify_record(record)
        if record['input']['provenance'] != 'recorded' or record['input']['scope']['context'] != 'kind-messaging-ha':
            raise ValueError('experience requires recorded local observations')
        if record['input']['incident_id'] != plan['incident_id']:
            raise ValueError('incident identity mismatch')
        if records and record['input']['observed_at'] <= records[-1]['input']['observed_at']:
            raise ValueError('incident phases must advance observation time')
        if records and record['changes']['previous_record_sha256'] != records[-1]['record_sha256']:
            raise ValueError('incident phase record chain mismatch')
        records.append(record)
        references.append(artifact(path, root))
        if scenario == 'lock':
            relative = {'baseline': 'baseline.json', 'during': '02-investigation/bundle-model.json',
                        'recovery': '03-recovery/bundle-model.json'}[phase]
            bundle_path = directory / 'drill' / relative
        else:
            bundle_path = directory / 'snapshots' / phase / 'bundle-model.json'
        references.append(artifact(bundle_path, root))
        # Preserve and verify raw collection material, not only a model projection.
        for evidence in read_json(bundle_path)['evidence']:
            raw = evidence.get('raw_ref')
            if raw and not str(raw).startswith('synthetic:'):
                raw_path = Path(raw)
                if not raw_path.is_absolute(): raw_path = root / raw_path
                reference = artifact(raw_path, root)
                if evidence.get('raw_sha256') and evidence['raw_sha256'] != reference['sha256']:
                    raise ValueError('raw evidence hash mismatch')
                if reference not in references: references.append(reference)
        for name in ('input.json', 'observations.json', 'request.json', 'response.json', 'failure.json'):
            source = path.parent / name
            if source.exists(): references.append(artifact(source, root))
    final = records[-1]['observations']
    if final['request_state'] != 'SAMPLED_REQUESTS_PERSISTED':
        raise ValueError('exact sampled persistence not verified')
    if scenario == 'lock':
        outcome = directory / 'drill/observation-summary.json'
        result = read_json(outcome)
        injection = read_json(directory / 'drill/injection-private.json')
        waits = records[1]['observations']['facts']['database_waits']
        if (result['status'] != 'COMPLETE' or not result['all_accepted_verified']
                or result['remaining_owned_sessions'] != 0 or not result['pg_uids_and_restarts_unchanged']
                or waits['status'] != 'KNOWN' or not any(e['blocker_pid'] == injection['pid'] for e in waits['value']['blocking_edges'])):
            raise ValueError('lock observation/recovery contract not verified')
        actions = ['Owned lock released by bounded server transaction ROLLBACK; no PG rollout.']
        sources = ['drill/plan-private.json', 'drill/injection-private.json', 'drill/release.json']
        cause = 'Controlled FOR UPDATE on a newly owned stream sequence row; observed blocker PID matches injection.'
    else:
        outcome = directory / 'drill/recovery.json'
        result = read_json(outcome)
        restored = read_json(directory / 'drill/restored.json')
        observed = records[1]['input']['investigation_policy']['worker_observation']
        # Kubernetes may omit readyReplicas entirely during an outage. Keep that
        # aggregate UNKNOWN and use the explicitly observed non-ready containers.
        unavailable = observed['status'] == 'KNOWN' and observed['value']['ready_replicas'] == 0
        if not unavailable:
            evidence = read_json(directory / 'drill/evidence.json')
            pods = evidence['get_worker_status']['result']['pods']
            unavailable = bool(pods) and all(p.get('containers') and all(c.get('ready') is False
                for c in p['containers']) for p in pods)
            references.append(artifact(directory / 'drill/evidence.json', root))
        if (not result.get('verified') or restored.get('fields_verified') is not True or not unavailable):
            raise ValueError('worker observation/recovery contract not verified')
        actions = ['Restored original Worker command/args/container configuration and strategy.',
                   'Restored and verified original local Argo automated sync policy.']
        sources = ['drill/plan-private.json', 'drill/rollback.json', 'drill/restored.json', 'drill/summary.json']
        cause = 'Controlled Worker startup module regression; see observed startup logs and original rollback configuration.'
    references.extend(artifact(directory / p, root) for p in sources)
    references.extend(artifact(p, root) for p in (outcome, directory / 'approval.json', directory / 'experiment-plan-private.json',
        directory / 'configuration-before.json', directory / 'configuration-after.json', directory / 'configuration-verification.json'))
    record = {'version': 'ops.incident-experience.v1', 'incident_id': plan['incident_id'],
        'scenario': scenario, 'provenance': 'recorded controlled local experiment',
        'context': 'kind-messaging-ha', 'namespace': 'messaging-app',
        'status': 'RECOVERY_VERIFIED_REVIEW_PENDING',
        'known_injection': cause, 'cause_boundary': 'Experiment ground truth, kept out of all model inputs; not an LLM success claim.',
        'operator_approval': approval['operator_approval'], 'actions': actions,
        'recovery': {'sampled_request_state': final['request_state'], 'verified_by': artifact(outcome, root)},
        'timeline': [{'phase': phase, 'observed_at': r['input']['observed_at'],
                      'observations': r['observations'], 'record_sha256': r['record_sha256']}
                     for phase, r in zip(('baseline', 'during', 'recovery'), records)],
        'model_assessments': [{'phase': phase, 'model': r['model'], 'status': r['model_status'],
                              'analysis': visible_analysis(r['model_analysis'])}
                             for phase, r in zip(('baseline', 'during', 'recovery'), records)],
        'review': review, 'artifacts': references,
        'knowledge_boundary': 'Recovery verified by code, semantic judgments require review. Search result is a past controlled case, not a current cause or approved runbook.'}
    record['record_sha256'] = canonical_sha256(record)
    verify_experience(record, root)
    return record


def archive(directory, scenario, review, root=ROOT):
    record = assemble(directory, scenario, review, root)
    save(directory / 'incident-record.json', record)
    save(directory / 'runbook-draft.json', {'status': 'DRAFT_REQUIRES_OPERATOR_REVIEW',
        'source_incident': record['incident_id'], 'incident_record_sha256': record['record_sha256'],
        'observed_cause': record['known_injection'], 'actions_observed': record['actions'],
        'recovery_evidence': record['recovery'], 'limitations': record['review'],
        'published_to_catalog': False})
    return record


def search(root, query, exclude_incident=None, limit=3):
    if not query.strip() or not 1 <= limit <= 10:
        raise ValueError('nonempty query and 1-10 results required')
    tokens = set(re.findall(r'[\w-]+', query.casefold()))
    found = []
    for path in sorted((root / 'results/ops-agent/shadow').glob('*/incident-record.json')):
        record = read_json(path)
        verify_experience(record, root)
        if record['incident_id'] == exclude_incident:
            continue
        score = sum(t in (record['scenario'] + ' ' + record['known_injection']).casefold() for t in tokens)
        if score:
            found.append({'score': score, 'incident_id': record['incident_id'], 'status': record['status'],
                'record': path.relative_to(root).as_posix(), 'known_injection': record['known_injection'],
                'recovery': record['recovery'], 'semantic_review': record['review'],
                'warning': record['knowledge_boundary']})
    return sorted(found, key=lambda r: (-r['score'], r['incident_id']))[:limit]
