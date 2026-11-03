"""Local MVP glue: reuse collectors, fault drills and grounded investigation.

preflight is read-only. execute requires explicit operator approval via --execute.
Only the two fixed local drills are supported; diagnosis runs AFTER safe rollback.
"""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ops_agent.calibration import summarize_bundle
from ops_agent.controller import collect_bundle
from ops_agent.diagnosis_agent import OpenAIResponsesClient, load_openai_configuration
from ops_agent.grounded_assistant import run as investigate
from ops_agent.evaluation_models import canonical_sha256
from ops_agent.models import EvidenceBundle
from ops_agent.policies import load_policy
from ops_agent.runbook_assistant import read_json, save
from scripts import run_local_lock_fault as lock
from scripts import run_worker_fault_diagnosis as worker
from scripts.run_local_burst import LocalClient, now


def preflight(directory):
    directory.mkdir(parents=True, exist_ok=False)
    checks, blockers = {}, []
    try:
        config = worker.kube('config', 'view', '--minify', '-o', 'json')
        server = config['clusters'][0]['cluster']['server']
        if urlparse(server).hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('cluster endpoint is not loopback')
        checks['cluster_endpoint'] = server
    except Exception as exc:
        result = {'at': now(), 'context': worker.CONTEXT, 'namespace': worker.NAMESPACE,
            'status': 'BLOCKED', 'checks': {'cluster_endpoint': {'error_type': type(exc).__name__}},
            'blockers': ['LOCAL_CLUSTER_IDENTITY_UNVERIFIED'], 'fault_injected': False, 'model_calls': 0}
        save(directory / 'preflight.json', result)
        return result
    for label, call in {
        'health': lambda: LocalClient().request('GET', '/health/ready'),
        'worker': lambda: worker.kube('get', 'deployment', 'worker', '-o', 'json'),
        'postgres': lambda: worker.kube('get', 'statefulset', 'messaging-postgresql-ha-postgresql', '-o', 'json'),
        'pods': lambda: worker.kube('get', 'pods', '-o', 'json'),
        'argocd': lambda: worker.kube('get', 'application', worker.APP, '-o', 'json', namespace='argocd'),
    }.items():
        try:
            value = call()
            if label == 'health':
                checks[label] = {'http_status': value[0], 'status': value[1].get('status')}
                if value[0] != 200 or value[1].get('status') != 'ready': blockers.append('API_NOT_READY')
            elif label == 'argocd':
                checks[label] = {'revision': value['spec']['source']['targetRevision'],
                    'automated': value['spec'].get('syncPolicy', {}).get('automated')}
            elif label == 'pods':
                checks[label] = [{'name': p['metadata']['name'], 'uid': p['metadata']['uid'],
                    'ready': bool(p.get('status', {}).get('containerStatuses')) and all(c['ready'] for c in p['status']['containerStatuses']),
                    'restarts': [c['restartCount'] for c in p.get('status', {}).get('containerStatuses', [])]}
                    for p in value['items'] if p['metadata']['name'].startswith(('kafka-', lock.PG_PREFIX, 'worker-', 'api-'))]
            else:
                checks[label] = {'desired': value['spec'].get('replicas', 0),
                    'ready': value.get('status', {}).get('readyReplicas', 0)}
                if checks[label]['desired'] <= 0 or checks[label]['ready'] != checks[label]['desired']:
                    blockers.append(label.upper() + '_NOT_READY')
                if label == 'postgres':
                    checks['postgres_partition'] = value['spec'].get('updateStrategy', {}).get('rollingUpdate', {}).get('partition')
        except Exception as exc:
            checks[label] = {'error_type': type(exc).__name__}
            blockers.append(label.upper() + '_UNAVAILABLE')
    try:
        bundle = collect_bundle(policy=load_policy('local-ha'), incident_id='mvp-preflight',
            artifact_root=directory / 'raw', cluster_context=worker.CONTEXT)
        save(directory / 'bundle.json', bundle.model_dump(mode='json'))
        state = summarize_bundle(bundle)
        checks['lag'] = state['kafka']
        if state['kafka']['total_lag'] != 0 or state['kafka']['anomalies']: blockers.append('LAG_NOT_CLEAN')
    except Exception as exc:
        checks['lag'] = {'error_type': type(exc).__name__}
        blockers.append('OBSERVATION_UNAVAILABLE')
    result = {'at': now(), 'context': worker.CONTEXT, 'namespace': worker.NAMESPACE,
        'status': 'READY_FOR_APPROVAL' if not blockers else 'BLOCKED', 'checks': checks,
        'blockers': blockers, 'fault_injected': False, 'model_calls': 0}
    save(directory / 'preflight.json', result)
    return result


def capture_worker(directory, incident):
    def capture(phase, accepted, polls, logs):
        target = directory / 'snapshots' / phase
        at = now()
        request_value = {'accepted': accepted, 'status_polls': polls}
        save(target / 'requests.json', request_value)
        if logs is not None: save(target / 'worker-logs.json', logs)
        bundle = collect_bundle(policy=load_policy('local-ha'), incident_id=incident,
            artifact_root=target / 'raw', cluster_context=worker.CONTEXT)
        document = bundle.model_dump(mode='json')
        save(target / 'bundle-full.json', document)
        selected = [e for e in document['evidence'] if e['metric']['name'] in lock.METRICS]
        selected.append(lock.supplemental('event_acceptance_and_persistence', request_value,
            at, document['collection']['completed_at'], target / 'requests.json'))
        if logs is not None:
            selected.append(lock.supplemental('worker_runtime_logs', logs, at,
                document['collection']['completed_at'], target / 'worker-logs.json'))
        projected = {**document, 'evidence': selected}
        EvidenceBundle.model_validate(projected)
        save(target / 'bundle-model.json', projected)
    return capture


def configuration_snapshot():
    deployment = worker.kube('get', 'deployment', 'worker', '-o', 'json')
    pg = worker.kube('get', 'statefulset', 'messaging-postgresql-ha-postgresql', '-o', 'json')
    app = worker.kube('get', 'application', worker.APP, '-o', 'json', namespace='argocd')
    pods = worker.kube('get', 'pods', '-l', 'app.kubernetes.io/component=postgresql', '-o', 'json')['items']
    return {'at': now(), 'worker_fields_sha256': canonical_sha256({
        'strategy': deployment['spec']['strategy'], 'containers': deployment['spec']['template']['spec']['containers']}),
        'pg_fields_sha256': canonical_sha256({'updateStrategy': pg['spec']['updateStrategy'],
                                             'containers': pg['spec']['template']['spec']['containers']}),
        'pg_pods': sorted([(p['metadata']['name'], p['metadata']['uid'], c['restartCount'])
                          for p in pods for c in p.get('status', {}).get('containerStatuses', [])]),
        'argocd_automated': app['spec'].get('syncPolicy', {}).get('automated')}


def execute(directory, scenario, approval):
    if not approval or not approval.strip():
        raise ValueError('explicit operator approval reference required')
    if scenario not in {'lock', 'worker'}:
        raise ValueError('only the two reviewed local scenarios are allowed')
    directory = directory.resolve()
    if not directory.is_relative_to(ROOT / 'results/ops-agent/shadow'):
        raise ValueError('experiment artifacts must stay under local shadow directory')
    # Nothing mutates the service until all preflight gates pass.
    check = preflight(directory / 'preflight')
    save(directory / 'approval.json', {'at': now(), 'operator_approval': approval, 'scenario': scenario,
        'context': worker.CONTEXT, 'max_model_calls': 3, 'max_output_tokens_per_call': 6000,
        'timeout_seconds_per_call': 45, 'retry': False})
    if check['status'] != 'READY_FOR_APPROVAL':
        save(directory / 'execution.json', {'status': 'BLOCKED', 'blockers': check['blockers'], 'fault_injected': False})
        return 1
    key, model = load_openai_configuration(ROOT)
    if model != 'gpt-5.6-luna':
        raise ValueError('this fixed evaluation requires the approved gpt-5.6-luna model')
    incident = 'local-observation-' + secrets.token_hex(6)
    before = configuration_snapshot()
    save(directory / 'configuration-before.json', before)
    save(directory / 'experiment-plan-private.json', {'at': now(), 'scenario': scenario,
        'incident_id': incident, 'model': model, 'model_calls': 3, 'retry': False,
        'acceptance': ['baseline ready', 'fault witnessed', 'rollback verified', 'exact accepted IDs persist',
                       'first model outputs retained', 'semantic review remains separate'],
        'source_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in (
            'scripts/ops_agent_mvp.py', 'scripts/run_local_lock_fault.py', 'scripts/run_worker_fault_diagnosis.py',
            'ops_agent/grounded_assistant.py', 'ops_agent/runbook_catalog.json')}})
    try:
        if scenario == 'lock':
            code = lock.run(directory / 'drill', live=False, incident_id=incident)
            paths = [directory / 'drill/baseline.json', directory / 'drill/02-investigation/bundle-model.json',
                     directory / 'drill/03-recovery/bundle-model.json']
        else:
            code = worker.run(directory / 'drill', live=False, capture=capture_worker(directory, incident))
            paths = [directory / 'snapshots' / p / 'bundle-model.json' for p in ('baseline', 'during', 'recovery')]
    except Exception as exc:
        save(directory / 'execution.json', {'status': 'DRILL_FAILED', 'error_type': type(exc).__name__,
            'rollback': 'Inspect original drill restoration/release artifacts; no recovery claim.'})
        return 1
    save(directory / 'execution.json', {'status': 'CAPTURED' if code == 0 else 'DRILL_FAILED', 'exit_code': code})
    if code != 0:
        return code
    after = configuration_snapshot()
    save(directory / 'configuration-after.json', after)
    fields = ('worker_fields_sha256', 'pg_fields_sha256', 'pg_pods', 'argocd_automated')
    verified = all(before[k] == after[k] for k in fields)
    save(directory / 'configuration-verification.json', {'verified': verified,
        'fields': {k: before[k] == after[k] for k in fields}, 'at': now()})
    if not verified:
        return 1
    client = OpenAIResponsesClient(api_key=key, timeout_seconds=45, max_retries=0)
    previous, results = None, []
    for phase, path in zip(('baseline', 'during', 'recovery'), paths):
        document = read_json(path)
        document['evidence'] = [e for e in document['evidence'] if e['metric']['name'] in
                                lock.METRICS | {'event_acceptance_and_persistence', 'postgres_blocking_activity', 'worker_runtime_logs'}]
        previous = investigate(document,
            '현재 증거의 원인 후보와 반대 근거, 런북 적용 조건, 후보를 구분할 다음 확인과 운영자가 검토할 복구 제안을 설명해줘.',
            'recorded', directory / 'investigation' / phase, previous=previous,
            client=client if phase != 'recovery' else None, model=model if phase != 'recovery' else None)
        results.append({'phase': phase, 'path': (directory / 'investigation' / phase / 'record.json').relative_to(ROOT).as_posix(),
                        'model_status': previous['model_status']})
    save(directory / 'investigation-summary.json', {'model': model, 'calls': 2, 'max_calls_approved': 3, 'retry': False,
        'recovery_shared_with_model': False,
        'results': results, 'semantic_review': 'PENDING', 'recovery_proposal_usefulness': 'PENDING'})
    return int(any(r['model_status'] != ('NOT_REQUESTED' if r['phase'] == 'recovery' else 'UNREVIEWED') for r in results))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preflight', 'execute', 'archive', 'search'))
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--scenario', choices=('lock', 'worker'))
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--approval', help='Reference to the operator approval for the reviewed plan')
    parser.add_argument('--review', type=Path, help='Explicit review JSON; unresolved fields remain PENDING')
    parser.add_argument('--query')
    parser.add_argument('--exclude-incident')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    if args.action == 'search':
        if not args.query: parser.error('search requires --query')
        from ops_agent.incident_experience import search
        print(json.dumps(search(ROOT, args.query, args.exclude_incident), ensure_ascii=False, indent=2))
        return 0
    if not args.output_dir: parser.error('--output-dir required')
    if args.action == 'archive':
        if not args.review or not args.scenario: parser.error('archive requires --review and --scenario')
        from ops_agent.incident_experience import archive
        result = archive(args.output_dir, args.scenario, read_json(args.review))
        print(json.dumps({'incident_id': result['incident_id'], 'status': result['status']}))
        return 0
    if args.action == 'preflight':
        print(json.dumps(preflight(args.output_dir), ensure_ascii=False, indent=2))
        return 0
    if not args.execute or not args.approval or not args.scenario:
        parser.error('execute requires --execute --approval and --scenario after operator review')
    return execute(args.output_dir, args.scenario, args.approval)


if __name__ == '__main__':
    sys.exit(main())
