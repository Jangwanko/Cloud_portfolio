"""Isolated, opt-in Argo CD migration-failure / rollback / forward-recovery lab.

Uses a namespace-local Helm fixture repository, not a Git push or production overlay.
The application image is pinned; only a synthetic additive Alembic revision changes.
No existing application, DB, topic, or namespace is modified. Created resources are
removed in finally, after preserving evidence. Run with --run and an explicit kind context.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import io
import ipaddress
import json
from pathlib import Path
import re
import secrets
import subprocess
import tarfile
import time


ROOT = Path(__file__).resolve().parents[1]
LABEL = 'portfolio.jangwanko.dev/release-lab'
RELEASE = 'portfolio.jangwanko.dev/lab-release'
APP_IMAGE = ('ghcr.io/jangwanko/cloud_portfolio@sha256:'
             'a2a834aba68848835216cffa99c00ecf52117484abec6af325cceca4c3200dd6')
IMAGE_SOURCE = '54ee42a2fb29'
BASE_SCHEMA = '0008_generic_event_envelope'
LAB_SCHEMA = '0009_release_lab'


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()


def validate_namespace(namespace):
    if not re.fullmatch(r'release-lab-[a-z0-9-]{6,35}', namespace):
        raise ValueError('Only a fresh release-lab-* namespace is allowed')


def metadata(name, namespace, wave=None):
    validate_namespace(namespace)
    result = {'name': name, 'namespace': namespace, 'labels': {LABEL: namespace}}
    if wave is not None:
        result['annotations'] = {'argocd.argoproj.io/sync-wave': str(wave)}
    return result


def env(values):
    return [{'name': key, 'value': str(value)} for key, value in values.items()]


def pod_spec(image, command, *, app_env=False, uid=10001, memory='256Mi', cpu='500m'):
    container = {'name': 'main', 'image': image, 'imagePullPolicy': 'IfNotPresent',
                 'command': command,
                 'securityContext': {'allowPrivilegeEscalation': False,
                                     'capabilities': {'drop': ['ALL']}},
                 'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'},
                               'limits': {'cpu': cpu, 'memory': memory}},
                 'volumeMounts': [{'name': 'tmp', 'mountPath': '/tmp'}]}
    if app_env:
        container['envFrom'] = [{'secretRef': {'name': 'messaging-env'}}]
    return {'automountServiceAccountToken': False,
            'securityContext': {'runAsNonRoot': True, 'runAsUser': uid, 'runAsGroup': uid,
                                'fsGroup': uid, 'seccompProfile': {'type': 'RuntimeDefault'}},
            'terminationGracePeriodSeconds': 15,
            'containers': [container], 'volumes': [{'name': 'tmp', 'emptyDir': {}}]}


def deployment(name, namespace, spec, wave=None, release=None):
    labels = {'app': name, LABEL: namespace}
    template_meta = {'labels': labels}
    if release:
        template_meta['annotations'] = {RELEASE: release}
    return {'apiVersion': 'apps/v1', 'kind': 'Deployment',
            'metadata': metadata(name, namespace, wave),
            'spec': {'replicas': 1, 'revisionHistoryLimit': 3,
                     'progressDeadlineSeconds': 180,
                     'selector': {'matchLabels': {'app': name}},
                     'strategy': {'type': 'RollingUpdate',
                                  'rollingUpdate': {'maxSurge': 1, 'maxUnavailable': 0}},
                     'template': {'metadata': template_meta, 'spec': spec}}}


def service(name, namespace, port):
    return {'apiVersion': 'v1', 'kind': 'Service', 'metadata': metadata(name, namespace),
            'spec': {'selector': {'app': name}, 'ports': [{'port': port, 'targetPort': port}]}}


def migration_source(fail):
    return ("from alembic import op\n"
            f"revision = '{LAB_SCHEMA}'\n"
            f"down_revision = '{BASE_SCHEMA}'\n"
            "branch_labels = None\ndepends_on = None\n\n"
            "def upgrade():\n"
            "    op.execute('CREATE TABLE release_lab_marker (id integer PRIMARY KEY)')\n"
            "    op.execute('INSERT INTO release_lab_marker VALUES (1)')\n" +
            ("    op.execute('SELECT 1 / 0')\n" if fail else '') +
            "\ndef downgrade():\n"
            "    raise RuntimeError('Lab never downgrades a committed migration')\n")


def mount_revision(spec, config_name):
    spec['volumes'].append({'name': 'lab-migration', 'configMap': {'name': config_name}})
    spec['containers'][0]['volumeMounts'].append({
        'name': 'lab-migration', 'mountPath': '/service/alembic/versions/0009_release_lab.py',
        'subPath': '0009_release_lab.py', 'readOnly': True})


def release_resources(namespace, image, version):
    if version not in {'0.1.0', '0.2.0', '0.3.0'}:
        raise ValueError('Unknown fixture release')
    resources = []
    config_name = 'lab-migration-' + version.replace('.', '-')
    if version != '0.1.0':
        resources.append({'apiVersion': 'v1', 'kind': 'ConfigMap',
                          'metadata': metadata(config_name, namespace, -3),
                          'data': {'0009_release_lab.py': migration_source(version == '0.2.0')}})
    spec = pod_spec(image, ['python', '-c',
                           'from portfolio.db import run_alembic_migrations; run_alembic_migrations()'],
                    app_env=True)
    spec['restartPolicy'] = 'Never'
    spec['containers'][0]['securityContext']['readOnlyRootFilesystem'] = True
    if version != '0.1.0':
        mount_revision(spec, config_name)
    job_meta = metadata('messaging-schema-migration', namespace, -2)
    job_meta['annotations']['argocd.argoproj.io/sync-options'] = 'Force=true,Replace=true'
    resources.append({'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': job_meta,
                      'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 120,
                               'template': {'metadata': {'labels': {LABEL: namespace},
                                                         'annotations': {RELEASE: version}},
                                            'spec': spec}}})
    for name, mode in [('worker', 'ingress'), ('notification-worker', 'notification')]:
        spec = pod_spec(image, ['python', '-m', 'worker.main'], app_env=True)
        spec['containers'][0]['env'] = env({'WORKER_MODE': mode})
        spec['containers'][0]['readinessProbe'] = {
            'tcpSocket': {'port': 9101}, 'periodSeconds': 2, 'failureThreshold': 3}
        resources.append(deployment(name, namespace, spec, -1, version))
    spec = pod_spec(image, ['python', '-m', 'uvicorn', 'portfolio.main:app', '--host', '0.0.0.0',
                           '--port', '8000', '--no-access-log'], app_env=True)
    spec['containers'][0]['env'] = env({'GENERIC_EVENTS_V2_ENABLED': 'true'})
    spec['containers'][0]['readinessProbe'] = {
        'httpGet': {'path': '/health/ready', 'port': 8000},
        'periodSeconds': 2, 'timeoutSeconds': 5, 'failureThreshold': 3}
    if version != '0.1.0':
        mount_revision(spec, config_name)
    resources.append(deployment('api', namespace, spec, 0, version))
    return resources


def chart_repository(namespace, image, url):
    binaries, entries, hashes = {}, [], {}
    for version in ('0.1.0', '0.2.0', '0.3.0'):
        chart = {'apiVersion': 'v2', 'name': 'release-lab', 'version': version,
                 'description': 'Isolated release failure fixture; synthetic additive migration'}
        files = {'Chart.yaml': json.dumps(chart),
                 'templates/resources.yaml': '\n---\n'.join(
                     json.dumps(item) for item in release_resources(namespace, image, version))}
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for name, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo('release-lab/' + name)
                info.size, info.mtime, info.mode = len(data), 0, 0o644
                archive.addfile(info, io.BytesIO(data))
        data = stream.getvalue()
        filename = f'release-lab-{version}.tgz'
        binaries[filename] = base64.b64encode(data).decode()
        hashes[version] = digest(data)
        entries.append({**chart, 'urls': [url + '/' + filename], 'digest': hashes[version]})
    index = {'apiVersion': 'v1', 'entries': {'release-lab': entries}}
    return {'apiVersion': 'v1', 'kind': 'ConfigMap',
            'metadata': metadata('chart-repository', namespace),
            'data': {'index.yaml': json.dumps(index)}, 'binaryData': binaries}, hashes


def validate_resource(resource, namespace):
    validate_namespace(namespace)
    meta, kind = resource['metadata'], resource['kind']
    if meta.get('labels', {}).get(LABEL) != namespace:
        raise ValueError('Missing experiment ownership label')
    if kind == 'Namespace':
        if meta['name'] != namespace:
            raise ValueError('Wrong namespace')
    elif kind in {'Application', 'AppProject'}:
        if meta['name'] != namespace or meta.get('namespace') != 'argocd':
            raise ValueError('Argo writes must target the exact experiment name')
        if kind == 'Application':
            if resource['spec']['destination']['namespace'] != namespace:
                raise ValueError('Application destination escapes the lab')
        elif resource['spec']['destinations'] != [
            {'server': 'https://kubernetes.default.svc', 'namespace': namespace}
        ]:
            raise ValueError('AppProject destination escapes the lab')
    elif kind not in {'Secret', 'ConfigMap', 'Deployment', 'Service', 'Pod', 'Job'}:
        raise ValueError('Unexpected lab resource kind')
    elif meta.get('namespace') != namespace:
        raise ValueError('Resource escapes the lab namespace')


def deployment_state(item):
    status = item.get('status', {})
    return {'uid': item['metadata']['uid'], 'generation': item['metadata']['generation'],
            'template_hash': digest(item['spec']['template']),
            'release': item['spec']['template']['metadata'].get('annotations', {}).get(RELEASE),
            'observed_generation': status.get('observedGeneration'),
            'available': status.get('availableReplicas', 0), 'ready': status.get('readyReplicas', 0),
            'updated': status.get('updatedReplicas', 0)}


def assert_blocked(before, after):
    for name in ('api', 'worker', 'notification-worker'):
        for key in ('uid', 'generation', 'template_hash', 'release'):
            if before[name][key] != after[name][key]:
                raise AssertionError(f'{name} changed despite failed migration: {key}')
        if after[name]['available'] < 1 or after[name]['ready'] < 1:
            raise AssertionError(f'{name} lost availability')


class Lab:
    def __init__(self, context, image, run_id):
        if not re.fullmatch(r'kind-[a-zA-Z0-9-]+', context):
            raise ValueError('An explicit local kind-* context is required')
        if not re.fullmatch(r'[a-zA-Z0-9./_-]+@sha256:[a-f0-9]{64}', image):
            raise ValueError('A digest-pinned application image is required')
        self.context, self.image = context, image
        self.namespace = 'release-lab-' + run_id.lower()
        validate_namespace(self.namespace)
        self.out = ROOT / 'results' / 'release-failure' / run_id
        self.out.mkdir(parents=True, exist_ok=False)
        self.tmp = ROOT / '.codex_tmp' / self.namespace
        self.tmp.mkdir(parents=True, exist_ok=False)
        self.created = {}
        self.private = []
        self.counter = 0
        self.report = {'schema_version': 'release.failure.lab.v1', 'started_at': utc(),
                       'namespace': self.namespace, 'context': context, 'image': image,
                       'result': 'INCOMPLETE', 'checks': {}, 'phases': {},
                       'limitations': [
                           'Isolated namespace on one existing kind node; shared host/control plane.',
                           'Single Kafka broker and PostgreSQL instance with emptyDir; no HA claim.',
                           'Local Helm fixture source and manual Argo sync; no Git push/CI publication test.',
                           'Same published application image; synthetic additive Alembic revision only.',
                           'Four low-volume canary batches; no continuous-availability or performance claim.',
                           'Rollback occurs while DB remains at 0008; no downgrade of a committed schema.',
                           'Namespace separation is not an enforced network security boundary.']}

    def log(self, message):
        print(f'[{utc()}] {message}', flush=True)

    def command(self, args, *, text=None, timeout=35):
        result = subprocess.run(args, input=text, text=True, encoding='utf-8',
                                errors='replace', capture_output=True, timeout=timeout, cwd=ROOT)
        if result.returncode:
            error = result.stderr + result.stdout
            for value in self.private:
                error = error.replace(value, '[REDACTED]')
            raise RuntimeError(f'{args[0]} {args[1]} failed: {error[:2400]}')
        return result.stdout

    def kubectl(self, *args, text=None, timeout=35):
        return self.command(['kubectl', '--context', self.context, '--request-timeout=15s',
                             *args], text=text, timeout=timeout)

    def get(self, kind, name=None, namespace=None, missing=False):
        args = ['get', kind]
        if name:
            args.append(name)
        if namespace:
            args += ['-n', namespace]
        if missing:
            args.append('--ignore-not-found')
        raw = self.kubectl(*args, '-o', 'json')
        return json.loads(raw) if raw.strip() else None

    def apply(self, resource):
        validate_resource(resource, self.namespace)
        self.kubectl('apply', '-f', '-', text=json.dumps(resource))

    def patch_app(self, patch):
        path = self.tmp / 'patch.json'
        path.write_text(json.dumps(patch), encoding='utf-8')
        self.kubectl('patch', 'application', self.namespace, '-n', 'argocd',
                     '--type=merge', '--patch-file', str(path))

    def save(self, name, value):
        path = self.out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(value, indent=2, sort_keys=True)
        for private in self.private:
            if private in text:
                raise RuntimeError('Refusing to write credential material into evidence')
        path.write_text(text + '\n', encoding='utf-8')

    def checkpoint(self, label):
        self.counter += 1
        app = self.get('application', self.namespace, 'argocd', missing=True)
        deployments = self.get('deployments', namespace=self.namespace)['items']
        state = {item['metadata']['name']: deployment_state(item) for item in deployments}
        pods = self.get('pods', namespace=self.namespace)['items']
        snapshot = {'captured_at': utc(), 'application': app, 'deployments': state,
                    'pods': [{'name': item['metadata']['name'], 'uid': item['metadata']['uid'],
                              'creation_timestamp': item['metadata']['creationTimestamp'],
                              'labels': item['metadata'].get('labels', {}),
                              'status': item.get('status', {})} for item in pods],
                    'jobs': self.get('jobs', namespace=self.namespace)['items']}
        self.save(f'raw/{self.counter:03d}-{label}.json', snapshot)
        return snapshot

    def wait(self, description, function, timeout=240):
        deadline, last_log = time.monotonic() + timeout, 0
        while time.monotonic() < deadline:
            result = function()
            if result:
                return result
            if time.monotonic() - last_log > 25:
                self.log('Waiting: ' + description)
                last_log = time.monotonic()
            time.sleep(2)
        raise TimeoutError(description)

    def control_snapshot(self):
        result = {}
        for kind in ('deployments', 'statefulsets', 'persistentvolumeclaims'):
            for item in self.get(kind, namespace='messaging-app')['items']:
                key = kind + '/' + item['metadata']['name']
                result[key] = {'uid': item['metadata']['uid'], 'spec_hash': digest(item['spec'])}
        app = self.get('application', 'messaging-portfolio-local-ha', 'argocd')
        result['application'] = {'uid': app['metadata']['uid'], 'spec_hash': digest(app['spec'])}
        return result

    def bootstrap(self):
        ns, image = self.namespace, self.image
        for kind, name, namespace in [('namespace', ns, None),
                                      ('application', ns, 'argocd'), ('appproject', ns, 'argocd')]:
            if self.get(kind, name, namespace, missing=True):
                raise RuntimeError('Refusing to reuse an existing lab resource')
        self.report['control_before'] = self.control_snapshot()
        self.report['source_commit'] = self.command(['git', 'rev-parse', 'HEAD']).strip()
        self.report['source_dirty'] = bool(self.command(['git', 'status', '--porcelain']).strip())
        self.report['image_source_commit'] = IMAGE_SOURCE
        diff = self.command(['git', 'diff', IMAGE_SOURCE, '--',
                             'portfolio', 'worker', 'alembic', 'Dockerfile', 'requirements.txt'])
        self.report['image_source_matches_checkout_code'] = not bool(diff)
        if self.image == APP_IMAGE and diff:
            raise RuntimeError('Pinned reference image source differs from checkout code')
        self.report['script_hashes'] = {
            name: digest((ROOT / 'scripts' / name).read_bytes())
            for name in ('release_failure_lab.py', 'release_failure_probe.py')}
        self.apply({'apiVersion': 'v1', 'kind': 'Namespace',
                    'metadata': {'name': ns, 'labels': {LABEL: ns}}})
        self.created['namespace'] = self.get('namespace', ns)['metadata']['uid']
        password, auth = secrets.token_hex(24), secrets.token_hex(32)
        self.private = [password, auth]
        values = {'APP_ENV': 'test', 'DB_HOST': 'postgres', 'DB_NAME': 'postgres',
                  'DB_USER': 'portfolio', 'DB_PASSWORD': password, 'DB_POOL_MAX_CONN': '3',
                  'AUTH_SECRET_KEY': auth, 'GENERIC_EVENTS_V2_ENABLED': 'false',
                  'DEMO_RESET_ENABLED': 'false', 'SCENARIO_LAB_ENABLED': 'false',
                  'KAFKA_BOOTSTRAP_SERVERS': 'kafka:9092', 'KAFKA_TOPIC_PARTITIONS': '2',
                  'KAFKA_TOPIC_REPLICATION_FACTOR': '1', 'KAFKA_MIN_INSYNC_REPLICAS': '1',
                  'POSTGRES_MIN_READY_STANDBYS': '0', 'POSTGRES_MIN_SYNC_STANDBYS': '0',
                  'K8S_NAMESPACE': ns, 'STARTUP_RETRIES': '3', 'STARTUP_RETRY_DELAY': '1'}
        self.apply({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': metadata('messaging-env', ns),
                    'type': 'Opaque', 'stringData': values})

        pod_cidrs = sorted({str(ipaddress.ip_network(cidr))
                            for node in self.get('nodes')['items']
                            for cidr in node['spec'].get('podCIDRs', [node['spec']['podCIDR']])})
        if not pod_cidrs:
            raise RuntimeError('Cannot determine cluster Pod CIDRs for the lab DB access policy')
        self.report['lab_postgres_allowed_pod_cidrs'] = pod_cidrs
        hba_command = ''.join(
            "printf '%s\\n' 'host postgres portfolio " + cidr +
            " scram-sha-256' >> /tmp/pgdata/pg_hba.conf; " for cidr in pod_cidrs)
        pg_command = ('/opt/bitnami/postgresql/bin/initdb -D /tmp/pgdata -U portfolio '
                      '--pwfile=/credentials/DB_PASSWORD --auth-local=trust --auth-host=scram-sha-256 '
                      '> /tmp/initdb.log; ' + hba_command +
                      'exec /opt/bitnami/postgresql/bin/postgres -D /tmp/pgdata '
                      '-c listen_addresses=* -c shared_buffers=32MB -c max_connections=40')
        pg = pod_spec('bitnamilegacy/postgresql-repmgr:17.6.0-debian-12-r2',
                      ['/opt/bitnami/scripts/postgresql-repmgr/entrypoint.sh',
                       'bash', '-ec', pg_command], uid=1001)
        pg['volumes'].append({'name': 'credentials', 'secret': {
            'secretName': 'messaging-env', 'items': [{'key': 'DB_PASSWORD', 'path': 'DB_PASSWORD'}]}})
        pg['containers'][0]['volumeMounts'].append({'name': 'credentials',
                                                   'mountPath': '/credentials', 'readOnly': True})
        pg['containers'][0]['readinessProbe'] = {'exec': {'command': [
            '/opt/bitnami/postgresql/bin/pg_isready', '-h', '127.0.0.1', '-U', 'portfolio']},
            'periodSeconds': 2}
        self.apply(deployment('postgres', ns, pg))
        self.apply(service('postgres', ns, 5432))

        kafka = pod_spec('apache/kafka:3.7.0', ['/etc/kafka/docker/run'], uid=1000,
                         memory='768Mi', cpu='1000m')
        kafka['containers'][0]['env'] = env({
            'CLUSTER_ID': 'MkU3OEVBNTcwNTJENDM2Qk', 'KAFKA_NODE_ID': '1',
            'KAFKA_PROCESS_ROLES': 'controller,broker',
            'KAFKA_CONTROLLER_QUORUM_VOTERS': '1@localhost:9093',
            'KAFKA_LISTENERS': 'PLAINTEXT://:9092,CONTROLLER://:9093',
            'KAFKA_ADVERTISED_LISTENERS': f'PLAINTEXT://kafka.{ns}.svc.cluster.local:9092',
            'KAFKA_LISTENER_SECURITY_PROTOCOL_MAP': 'PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT',
            'KAFKA_CONTROLLER_LISTENER_NAMES': 'CONTROLLER',
            'KAFKA_INTER_BROKER_LISTENER_NAME': 'PLAINTEXT',
            'KAFKA_AUTO_CREATE_TOPICS_ENABLE': 'false', 'KAFKA_NUM_PARTITIONS': '2',
            'KAFKA_DEFAULT_REPLICATION_FACTOR': '1', 'KAFKA_MIN_INSYNC_REPLICAS': '1',
            'KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR': '1',
            'KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR': '1',
            'KAFKA_TRANSACTION_STATE_LOG_MIN_ISR': '1', 'KAFKA_LOG_DIRS': '/tmp/kafka-logs',
            'KAFKA_HEAP_OPTS': '-Xms256m -Xmx384m', 'KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS': '0'})
        kafka['containers'][0]['readinessProbe'] = {
            'tcpSocket': {'port': 9092}, 'periodSeconds': 2, 'initialDelaySeconds': 10}
        self.apply(deployment('kafka', ns, kafka))
        self.apply(service('kafka', ns, 9092))
        self.apply(service('api', ns, 8000))

        self.apply({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': metadata('probe-code', ns),
                    'data': {'probe.py': (ROOT / 'scripts/release_failure_probe.py').read_text()}})
        probe = pod_spec(image, ['python', '-c', 'import time; time.sleep(86400)'], app_env=True)
        probe['volumes'].append({'name': 'probe-code', 'configMap': {'name': 'probe-code'}})
        probe['containers'][0]['volumeMounts'].append({'name': 'probe-code', 'mountPath': '/probe'})
        self.apply({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': metadata('probe', ns), 'spec': probe})

        url = f'http://chart-repository.{ns}.svc.cluster.local:8080'
        chart_config, hashes = chart_repository(ns, image, url)
        self.report['chart_sha256'] = hashes
        self.apply(chart_config)
        repo = pod_spec(image, ['python', '-m', 'http.server', '8080', '--directory', '/charts'],
                        memory='96Mi', cpu='200m')
        repo['volumes'].append({'name': 'charts', 'configMap': {'name': 'chart-repository'}})
        repo['containers'][0]['volumeMounts'].append({'name': 'charts', 'mountPath': '/charts'})
        repo['containers'][0]['readinessProbe'] = {
            'httpGet': {'path': '/index.yaml', 'port': 8080}, 'periodSeconds': 2}
        self.apply(deployment('chart-repository', ns, repo))
        self.apply(service('chart-repository', ns, 8080))
        def dependencies_ready():
            observed_ns = self.get('namespace', ns)
            if observed_ns['metadata'].get('deletionTimestamp'):
                raise RuntimeError('Experiment namespace is being removed')
            items = {item['metadata']['name']: item for item in
                     self.get('deployments', namespace=ns)['items']}
            return all(items.get(name, {}).get('status', {}).get('availableReplicas', 0) == 1
                       for name in ('postgres', 'kafka', 'chart-repository'))
        self.wait('isolated dependencies', dependencies_ready)
        self.kubectl('wait', '--for=condition=Ready', 'pod/probe', '-n', ns, '--timeout=120s', timeout=135)
        db_probe = ("import sys; sys.path.insert(0, '/probe'); import probe; "
                    "c=probe.database(); cur=c.cursor(); cur.execute('SELECT 1'); "
                    "assert cur.fetchone()[0] == 1; c.close(); print('Lab DB authentication verified')")
        self.kubectl('exec', '-n', ns, 'probe', '--', 'python', '-c', db_probe)
        topics_code = ("from kafka.admin import KafkaAdminClient, NewTopic; "
                       "a=KafkaAdminClient(bootstrap_servers='kafka:9092'); "
                       "a.create_topics([NewTopic(t, 2, 1, topic_configs={'min.insync.replicas':'1'}) "
                       "for t in ['message-ingress','message-ingress-dlq','message-notifications']]); "
                       "print('Created isolated topics'); a.close()")
        self.kubectl('exec', '-n', ns, 'probe', '--', 'python', '-c', topics_code, timeout=60)

        project = {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'AppProject',
                   'metadata': {'name': ns, 'namespace': 'argocd', 'labels': {LABEL: ns}},
                   'spec': {'sourceRepos': [url], 'destinations': [
                       {'server': 'https://kubernetes.default.svc', 'namespace': ns}],
                       'clusterResourceWhitelist': [], 'namespaceResourceWhitelist': [
                           {'group': 'apps', 'kind': 'Deployment'},
                           {'group': '', 'kind': 'ConfigMap'}, {'group': 'batch', 'kind': 'Job'}]}}
        self.apply(project)
        self.created['appproject'] = self.get('appproject', ns, 'argocd')['metadata']['uid']
        application = {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Application',
                       'metadata': {'name': ns, 'namespace': 'argocd', 'labels': {LABEL: ns}},
                       'spec': {'project': ns,
                                'source': {'repoURL': url, 'chart': 'release-lab',
                                           'targetRevision': '0.1.0'},
                                'destination': {'server': 'https://kubernetes.default.svc',
                                                'namespace': ns}}}
        self.apply(application)
        self.created['application'] = self.get('application', ns, 'argocd')['metadata']['uid']
        self.log('Isolated dependencies and scoped Argo Application created')

    def start_sync(self, version):
        app = self.get('application', self.namespace, 'argocd')
        if app.get('operation') or app.get('status', {}).get('operationState', {}).get('phase') in {
            'Running', 'Terminating'}:
            raise RuntimeError('An Argo operation is still active')
        self.patch_app({'spec': {'source': {'targetRevision': version}},
                        'operation': {'initiatedBy': {'username': 'release-failure-lab'},
                                      'sync': {'revision': version, 'prune': True,
                                               'syncStrategy': {'hook': {}}}}})
        self.log('Requested Argo sync ' + version)

    def successful_sync(self, version):
        self.start_sync(version)

        def check():
            snapshot = self.checkpoint('sync-' + version)
            status = (snapshot['application'] or {}).get('status', {})
            op = status.get('operationState', {})
            same = op.get('syncResult', {}).get('revision') == version
            if same and op.get('phase') in {'Failed', 'Error'}:
                raise RuntimeError('Unexpected sync failure: ' + op.get('message', ''))
            if (same and op.get('phase') == 'Succeeded' and
                    status.get('sync', {}).get('status') == 'Synced' and
                    status.get('health', {}).get('status') == 'Healthy' and all(
                snapshot['deployments'].get(name, {}).get('release') == version and
                snapshot['deployments'][name]['available'] >= 1 and
                snapshot['deployments'][name]['updated'] == 1 and
                snapshot['deployments'][name]['observed_generation'] ==
                snapshot['deployments'][name]['generation']
                for name in ('worker', 'notification-worker', 'api')
            )):
                return snapshot
            return None

        return self.wait('successful release ' + version, check)

    def probe(self, phase):
        raw = self.kubectl('exec', '-n', self.namespace, 'probe', '--',
                           'python', '/probe/probe.py', phase, timeout=120)
        result = json.loads(raw)
        self.save('canary-' + phase + '.json', result)
        self.report['phases'][phase] = result
        self.log(f"Canary {phase}: {result.get('persisted_total', '-')} persisted")
        return result

    def terminate_sync(self):
        app = self.get('application', self.namespace, 'argocd', missing=True)
        if not app:
            return
        if app.get('status', {}).get('operationState', {}).get('phase') == 'Running':
            self.patch_app({'status': {'operationState': {'phase': 'Terminating'}}})
        self.wait('lab sync termination', lambda: (
            (current := self.get('application', self.namespace, 'argocd')) and
            not current.get('operation') and
            current.get('status', {}).get('operationState', {}).get('phase') not in {'Running', 'Terminating'}
        ), timeout=90)

    def experiment(self):
        before = self.successful_sync('0.1.0')
        baseline = self.probe('baseline')
        assert baseline['schema'] == {'alembic_version': BASE_SCHEMA,
                                       'marker_exists': False, 'marker_rows': 0}
        self.report['checks']['baseline'] = True
        self.start_sync('0.2.0')

        def migration_failed():
            snapshot = self.checkpoint('failure')
            for job in snapshot['jobs']:
                if job['spec']['template']['metadata'].get('annotations', {}).get(RELEASE) == '0.2.0':
                    if any(item['type'] == 'Failed' and item['status'] == 'True'
                           for item in job.get('status', {}).get('conditions', [])):
                        return snapshot
            return None

        failed = self.wait('deliberate migration failure', migration_failed)
        assert_blocked(before['deployments'], failed['deployments'])
        self.report['checks']['worker_api_wave_blocked'] = True
        logs = self.kubectl('logs', '-n', self.namespace, 'job/messaging-schema-migration')
        for private in self.private:
            logs = logs.replace(private, '[REDACTED]')
        self.save('migration-failure-log.json', {'text': logs})
        assert 'division by zero' in logs, 'Migration did not fail at the intended SQL statement'
        during = self.probe('migration-failed')
        assert during['schema'] == baseline['schema'], 'Failed migration leaked schema changes'
        self.report['checks']['transactional_ddl_rollback'] = True
        self.report['checks']['existing_release_canary_during_failure'] = True
        self.report['failure_operation_phase'] = failed['application']['status'].get(
            'operationState', {}).get('phase')
        self.terminate_sync()
        rollback_started = time.monotonic()
        self.successful_sync('0.1.0')
        rolled_back = self.probe('rollback')
        assert rolled_back['schema'] == baseline['schema']
        self.report['rollback_to_verified_canary_seconds'] = round(time.monotonic() - rollback_started, 3)
        self.report['checks']['baseline_release_restored'] = True
        forward_started = time.monotonic()
        self.successful_sync('0.3.0')
        forward = self.probe('forward-recovery')
        assert forward['schema'] == {'alembic_version': LAB_SCHEMA,
                                      'marker_exists': True, 'marker_rows': 1}
        self.report['forward_to_verified_canary_seconds'] = round(time.monotonic() - forward_started, 3)
        self.report['checks']['forward_migration_and_canary'] = True
        self.report['checks']['all_40_events_preserved'] = forward['persisted_total'] == 40
        self.report['control_after'] = self.control_snapshot()
        self.report['checks']['control_workload_specs_unchanged'] = (
            self.report['control_before'] == self.report['control_after'])
        assert all(self.report['checks'].values()), 'One or more experiment checks failed'
        self.report['result'] = 'PASS'

    def cleanup(self):
        errors = []
        if 'application' in self.created:
            try:
                self.terminate_sync()
            except Exception as exc:
                errors.append(str(exc))
        for kind in ('application', 'appproject', 'namespace'):
            if kind not in self.created:
                continue
            namespace = None if kind == 'namespace' else 'argocd'
            try:
                current = self.get(kind, self.namespace, namespace, missing=True)
                if current:
                    if (current['metadata']['uid'] != self.created[kind] or
                            current['metadata'].get('labels', {}).get(LABEL) != self.namespace):
                        raise RuntimeError('Cleanup ownership mismatch: ' + kind)
                    args = ['delete', kind, self.namespace, '--wait=false']
                    if namespace:
                        args += ['-n', namespace]
                    self.kubectl(*args)
            except Exception as exc:
                errors.append(str(exc))
        if 'namespace' in self.created:
            try:
                self.wait('disposable namespace removal', lambda: not self.get(
                    'namespace', self.namespace, missing=True), timeout=180)
            except Exception as exc:
                errors.append(str(exc))
        patch = self.tmp / 'patch.json'
        if patch.exists():
            patch.unlink()
        self.tmp.rmdir()
        self.report['cleanup'] = {'complete': not errors, 'errors': errors}
        if errors and self.report['result'] == 'PASS':
            self.report['result'] = 'CLEANUP_REQUIRED'
        self.log('Cleanup: ' + ('complete' if not errors else 'requires attention'))

    def run(self):
        try:
            self.bootstrap()
            self.experiment()
        except Exception as exc:
            self.report['result'] = 'FAIL'
            self.report['error'] = str(exc)
            self.log(str(exc))
            if 'namespace' in self.created:
                try:
                    self.checkpoint('unexpected-failure')
                    for pod in self.get('pods', namespace=self.namespace)['items']:
                        name = pod['metadata']['name']
                        try:
                            logs = self.kubectl('logs', '-n', self.namespace, name, '--tail=100')
                            for private in self.private:
                                logs = logs.replace(private, '[REDACTED]')
                            self.save('diagnostics/' + name + '.json', {'text': logs})
                        except Exception:
                            pass
                except Exception:
                    pass
        finally:
            self.cleanup()
            self.report['finished_at'] = utc()
            self.save('summary.json', self.report)
            files = {str(path.relative_to(self.out)).replace('\\', '/'): digest(path.read_bytes())
                     for path in self.out.rglob('*') if path.is_file()}
            self.save('hashes.json', files)
            self.log('Result: ' + self.report['result'])
        return self.report['result'] == 'PASS'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True)
    parser.add_argument('--image', default=APP_IMAGE)
    parser.add_argument('--run', action='store_true', help='Create, exercise, and remove the isolated lab')
    args = parser.parse_args()
    if not args.run:
        print('No cluster writes. Add --run to execute the isolated lab.')
        return 0
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return 0 if Lab(args.context, args.image, run_id).run() else 1


if __name__ == '__main__':
    raise SystemExit(main())
