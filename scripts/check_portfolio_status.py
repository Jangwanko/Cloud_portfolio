"""Read-only profile-aware checks. Failures never suppress independent checks."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

try:
    from .validation_record import atomic_json, new_record, utc_now
except ImportError:
    from validation_record import atomic_json, new_record, utc_now

PROFILES = {
    'local-ha': {'brokers': 3, 'outbox': True, 'application': 'messaging-portfolio-local-ha'},
    'demo-lite': {'brokers': 1, 'outbox': False, 'application': 'messaging-portfolio-demo-lite'},
}


def http_json(url):
    try:
        with urlopen(url, timeout=10) as response:
            return json.load(response)
    except HTTPError as exc:
        # Readiness uses 503 to return structured not-ready evidence.
        if exc.code == 503:
            return json.load(exc)
        raise


class CheckFailed(Exception):
    def __init__(self, evidence):
        self.evidence = evidence


def require(condition, evidence):
    if not condition:
        raise CheckFailed(evidence)
    return evidence


class Checker:
    def __init__(self, args, *, get_json=http_json, command=subprocess.check_output):
        self.args, self.get_json, self.command = args, get_json, command
        self.profile = PROFILES[args.profile]
        self.path, self.report = new_record('portfolio-status', args.output_dir)
        self.report.update(profile=args.profile, context=args.context,
                           public_only=args.public_only, checks=[])

    def check(self, name, action=None):
        item = {'name': name, 'observed_at': utc_now()}
        if action is None:
            item['status'] = 'skipped'
        else:
            try:
                item.update(status='passed', evidence=action())
            except CheckFailed as exc:
                item.update(status='failed', evidence=exc.evidence)
            except Exception as exc:
                # Do not dump arbitrary HTTP bodies, manifests, stderr or secrets.
                item.update(status='unknown', error_type=type(exc).__name__)
        self.report['checks'].append(item)
        atomic_json(self.path, self.report)
        print(f'{name}: {item["status"]}', flush=True)

    def kube(self, resource, name=None, namespace=None):
        cmd = ['kubectl', '--context', self.args.context, '--request-timeout=10s',
               '-n', namespace or self.args.namespace, 'get', resource]
        if name:
            cmd.append(name)
        return json.loads(self.command(cmd + ['-o', 'json'], text=True, timeout=15,
                                       stderr=subprocess.DEVNULL))

    def workload(self, kind, name):
        obj = self.kube(kind, name)
        desired = obj.get('spec', {}).get('replicas', 1)
        state = obj.get('status', {})
        ready = state.get('availableReplicas' if kind == 'deployment' else 'readyReplicas', 0)
        evidence = {'desired': desired, 'ready': ready}
        return require(ready >= desired and state.get('observedGeneration', 0) >= obj['metadata'].get('generation', 0), evidence)

    def argo(self):
        obj = self.kube('application', self.args.argo_application or self.profile['application'], self.args.argo_namespace)
        state = obj.get('status', {})
        evidence = {'target_revision': obj['spec'].get('source', {}).get('targetRevision'),
                    'revision': state.get('sync', {}).get('revision'),
                    'sync': state.get('sync', {}).get('status'), 'health': state.get('health', {}).get('status')}
        return require(evidence['sync'] == 'Synced' and evidence['health'] == 'Healthy', evidence)

    def pods(self):
        result = []
        for pod in self.kube('pods')['items']:
            if not pod['metadata']['name'].startswith(('api-', 'worker-', 'outbox-publisher-', 'notification-worker-')):
                continue
            result.append({'name': pod['metadata']['name'], 'containers': [
                {key: c.get(key) for key in ('name', 'image', 'imageID', 'ready', 'restartCount')}
                for c in pod.get('status', {}).get('containerStatuses', [])]})
        return require(bool(result), result)

    def prom(self, query):
        body = self.get_json(self.args.prometheus_url.rstrip('/') + '/api/v1/query?' + urlencode({'query': query}))
        require(body.get('status') == 'success', {'query': query, 'api_status': body.get('status')})
        values = [float(row['value'][1]) for row in body['data']['result']]
        if not values:
            raise LookupError('No series; not zero')
        return values

    def ui(self):
        with urlopen(self.args.base_url.rstrip('/') + '/demo/order-dashboard.html', timeout=10) as response:
            html = response.read().decode('utf-8')
        version = re.search(r'const DEMO_UI_VERSION\s*=\s*"([^"]+)"', html)
        return require(version is not None, {'ui_version': version[1] if version else None})

    def run(self):
        base = self.args.base_url.rstrip('/')
        def readiness():
            raw = self.get_json(base + '/health/ready')
            evidence = {key: raw.get(key) for key in ('app_version', 'status', 'reason', 'kafka', 'postgres')}
            return require(raw.get('status') == 'ready', evidence)
        self.check('API readiness', readiness)
        self.check('Public UI', self.ui)
        self.check('Operations summary', lambda: self.get_json(base + '/ops/summary'))
        if not self.args.public_only:
            self.check('Namespace', lambda: {'name': self.kube('namespace', self.args.namespace)['metadata']['name']})
            self.check('Argo CD GitOps', None if self.args.skip_argocd else self.argo)
            names = ['api', 'worker', 'notification-worker', 'dlq-replayer', 'kafka-exporter',
                     'prometheus', 'grafana', 'kube-state-metrics', 'messaging-postgresql-ha-pgpool']
            if self.profile['outbox']:
                names.append('outbox-publisher')
            for name in names:
                self.check('deployment/' + name, lambda name=name: self.workload('deployment', name))
            for name in ['kafka', 'messaging-postgresql-ha-postgresql']:
                self.check('statefulset/' + name, lambda name=name: self.workload('statefulset', name))
            self.check('Runtime images', self.pods)
            def scaling():
                obj = self.kube('scaledobject', 'worker-keda')
                conditions = obj.get('status', {}).get('conditions', [])
                return require(any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in conditions),
                               {'ready': any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in conditions)})
            self.check('worker-keda', scaling)
            for name in ['api-hpa', 'worker-keda-hpa']:
                def hpa(name=name):
                    state = self.kube('hpa', name).get('status', {})
                    evidence = {key: state.get(key) for key in ('currentReplicas', 'desiredReplicas')}
                    return require(all(v is not None for v in evidence.values()), evidence)
                self.check(name, hpa)
            if self.args.profile == 'local-ha':
                def backup():
                    phase = self.kube('pvc', 'postgres-backups').get('status', {}).get('phase')
                    return require(phase in ('Bound', 'Pending'), {'phase': phase,
                        'note': 'Pending can mean WaitForFirstConsumer; not a backup success'})
                self.check('postgres-backups', backup)
        else:
            self.check('Cluster inspection', None)
        if not self.args.skip_prometheus:
            jobs = ['api', 'worker', 'notification-worker', 'dlq-replayer', 'kafka-exporter', 'kube-state-metrics']
            if self.profile['outbox']:
                jobs.append('outbox-publisher')
            for job in jobs:
                def up(job=job):
                    values = self.prom(f'up{{job="{job}"}}')
                    return require(all(v >= 1 for v in values), {'up': values})
                self.check('scrape/' + job, up)
            def brokers():
                values = self.prom('kafka_brokers')
                return require(min(values) >= self.profile['brokers'], {'observed': values, 'minimum': self.profile['brokers']})
            self.check('Kafka brokers', brokers)
            for group in ['message-worker', 'notification-worker']:
                self.check(group + ' lag', lambda group=group: {'lag': self.prom(
                    'sum(clamp_min(kafka_consumergroup_lag{consumergroup="' + group + '"}, 0))'),
                    'note': 'Observation only; lag is workload-dependent'})
        else:
            self.check('Prometheus inspection', None)
        states = {c['status'] for c in self.report['checks']}
        self.report.update(status='failed' if 'failed' in states else 'incomplete' if 'unknown' in states else 'passed',
                           finished_at=utc_now(), scope='public endpoints only' if self.args.public_only else 'cluster and endpoints')
        atomic_json(self.path, self.report)
        print(f'{self.report["status"]}; evidence: {self.path}', flush=True)
        return 1 if 'failed' in states else 2 if 'unknown' in states else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', choices=PROFILES, default='local-ha')
    p.add_argument('--context')
    p.add_argument('--public-only', action='store_true')
    p.add_argument('--base-url', default='http://localhost')
    p.add_argument('--prometheus-url', default='http://localhost/prometheus')
    p.add_argument('--namespace', default='messaging-app')
    p.add_argument('--argo-namespace', default='argocd')
    p.add_argument('--argo-application')
    p.add_argument('--skip-argocd', action='store_true')
    p.add_argument('--skip-prometheus', action='store_true')
    p.add_argument('--output-dir', type=Path)
    args = p.parse_args()
    if not args.public_only and not args.context:
        p.error('--context is required for cluster inspection; use --public-only for endpoints only')
    return Checker(args).run()


if __name__ == '__main__':
    sys.exit(main())
