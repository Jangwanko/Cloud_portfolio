"""Prepare a synthetic real-schema dump, or restore it into a new isolated Pod.

prepare uses the existing release lab to seed generic v2 events. restore never
connects to an existing database. External storage transfer is a separate step.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

from object_storage_backup import fingerprint
from release_failure_lab import APP_IMAGE, LABEL, Lab, metadata, pod_spec, validate_namespace


PG_IMAGE = ('docker.io/bitnamilegacy/postgresql-repmgr@sha256:'
            'f12387ec882ba42383bbeccfe56d1e9b7671bfbb10795426de4acf2ae800f314')


def kubectl(context, *args, data=None, timeout=180):
    if not context.startswith('kind-'):
        raise ValueError('Explicit kind context required')
    result = subprocess.run(['kubectl', '--context', context, '--request-timeout=30s', *args],
                            input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        # SQL and dump contents must not be included in diagnostic output.
        raise RuntimeError(f'kubectl {args[0]} failed (exit {result.returncode})')
    return result.stdout


def sql(context, namespace, pod, statement):
    return kubectl(context, 'exec', '-i', '-n', namespace, pod, '--',
                   'psql', '-h', '/tmp', '-U', 'portfolio', '-d', 'postgres',
                   '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1',
                   data=statement.encode()).decode().strip()


def ident(value):
    return '"' + value.replace('"', '""') + '"'


def manifest(context, namespace, pod):
    tables = json.loads(sql(context, namespace, pod,
        "SELECT coalesce(json_agg(tablename ORDER BY tablename), '[]') "
        "FROM pg_tables WHERE schemaname='public';"))
    result = {'tables': {}, 'sequences': {}}
    for name in tables:
        table = 'public.' + ident(name)
        raw = sql(context, namespace, pod,
                  f'SELECT row_to_json(t)::text FROM {table} t '
                  'ORDER BY row_to_json(t)::text COLLATE "C";')
        count = int(sql(context, namespace, pod, f'SELECT count(*) FROM {table};'))
        result['tables'][name] = {'rows': count,
                                  'rows_sha256': hashlib.sha256(raw.encode()).hexdigest()}
    seqs = json.loads(sql(context, namespace, pod,
        "SELECT coalesce(json_agg(sequencename ORDER BY sequencename), '[]') "
        "FROM pg_sequences WHERE schemaname='public';"))
    for name in seqs:
        result['sequences'][name] = sql(context, namespace, pod,
            f'SELECT last_value, is_called FROM public.{ident(name)};')
    columns = sql(context, namespace, pod,
        "SELECT table_name,column_name,data_type,is_nullable,column_default "
        "FROM information_schema.columns WHERE table_schema='public' "
        "ORDER BY table_name,ordinal_position;")
    result['columns_sha256'] = hashlib.sha256(columns.encode()).hexdigest()
    result['alembic_version'] = sql(context, namespace, pod, 'SELECT version_num FROM alembic_version;')
    return result


def require_matching_manifest(expected, actual):
    if not expected.get('tables') or 'messages' not in expected['tables']:
        raise ValueError('Expected manifest lacks the application data')
    if actual != expected:
        raise ValueError('Restored tables, row contents, sequences, columns or schema differ')


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)


def prepare(context, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lab = Lab(context, APP_IMAGE, run_id)
    report = {'result': 'FAIL', 'source_kind': 'isolated synthetic v2 workload',
              'external_storage_verified': False}
    try:
        lab.bootstrap()
        lab.successful_sync('0.1.0')
        canary = lab.probe('baseline')
        pod = next(p['metadata']['name'] for p in
                   lab.get('pods', namespace=lab.namespace)['items']
                   if p['metadata'].get('labels', {}).get('app') == 'postgres')
        before = manifest(context, lab.namespace, pod)
        started = time.monotonic()
        dump = kubectl(context, 'exec', '-n', lab.namespace, pod, '--',
                       'pg_dump', '-h', '/tmp', '-U', 'portfolio', '-d', 'postgres',
                       '--format=custom', '--no-owner', '--no-acl')
        if not dump.startswith(b'PGDMP'):
            raise ValueError('Expected a PostgreSQL custom-format dump')
        backup = directory / 'postgres.dump'
        with backup.open('xb') as output:
            output.write(dump)
        after = manifest(context, lab.namespace, pod)
        require_matching_manifest(before, after)
        report.update({'result': 'PREPARED', 'dump': fingerprint(backup), 'database': before,
                       'source_namespace': lab.namespace, 'source_run_id': run_id,
                       'accepted_events': canary['accepted_total'],
                       'dump_seconds': round(time.monotonic() - started, 3),
                       'consistency_scope': 'Quiescent synthetic source; manifests match before/after dump. Not an online snapshot-coordinated backup.',
                       'prepared_at': datetime.now(timezone.utc).isoformat()})
    finally:
        lab.cleanup()
        report['source_cleanup'] = lab.report.get('cleanup')
        write_json(directory / 'manifest.json', report)
    if not report['source_cleanup']['complete']:
        raise RuntimeError('Source lab cleanup requires attention')
    print(json.dumps({'result': report['result'], 'directory': str(directory),
                      'source_cleanup': report['source_cleanup']}))


def restore(context, dump, manifest_file, output):
    if Path(output).exists():
        raise FileExistsError('Evidence output already exists')
    expected = json.loads(Path(manifest_file).read_text(encoding='utf-8'))
    if expected['result'] != 'PREPARED' or fingerprint(dump) != expected['dump']:
        raise ValueError('Dump does not match the prepared source fingerprint')
    namespace = 'release-lab-restore-' + uuid.uuid4().hex[:12]
    validate_namespace(namespace)
    report = {'result': 'FAIL', 'namespace': namespace, 'context': context,
              'image': PG_IMAGE, 'dump': expected['dump'],
              'external_storage_verified': False,
              'scope': 'Isolated PostgreSQL restore; storage provenance must be joined with transfer receipt.'}
    uid = None
    started = time.monotonic()
    try:
        ns = {'apiVersion': 'v1', 'kind': 'Namespace',
              'metadata': {'name': namespace, 'labels': {LABEL: namespace}}}
        created = json.loads(kubectl(context, 'create', '-f', '-', '-o', 'json',
                                     data=json.dumps(ns).encode()))
        uid = created['metadata']['uid']
        command = ('/opt/bitnami/postgresql/bin/initdb -D /tmp/pgdata -U portfolio '
                   '--auth-local=trust --auth-host=reject >/tmp/initdb.log; '
                   'exec /opt/bitnami/postgresql/bin/postgres -D /tmp/pgdata '
                   "-c listen_addresses='' -c unix_socket_directories=/tmp -c shared_buffers=32MB")
        spec = pod_spec(PG_IMAGE, ['/opt/bitnami/scripts/postgresql-repmgr/entrypoint.sh',
                                    'bash', '-ec', command], uid=1001)
        spec['restartPolicy'] = 'Never'
        spec['containers'][0]['readinessProbe'] = {'exec': {'command': [
            'pg_isready', '-h', '/tmp', '-U', 'portfolio', '-d', 'postgres']}, 'periodSeconds': 2}
        pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': metadata('restore', namespace),
               'spec': spec}
        kubectl(context, 'create', '-f', '-', data=json.dumps(pod).encode())
        kubectl(context, 'wait', '-n', namespace, 'pod/restore', '--for=condition=Ready', '--timeout=120s')
        restore_started = time.monotonic()
        kubectl(context, 'exec', '-i', '-n', namespace, 'restore', '--',
                'pg_restore', '-h', '/tmp', '-U', 'portfolio', '-d', 'postgres',
                '--no-owner', '--no-acl', '--exit-on-error', '--single-transaction',
                data=Path(dump).read_bytes())
        report['restore_seconds'] = round(time.monotonic() - restore_started, 3)
        actual = manifest(context, namespace, 'restore')
        require_matching_manifest(expected['database'], actual)
        report.update({'result': 'PASS', 'database': actual,
                       'provision_restore_verify_seconds': round(time.monotonic() - started, 3),
                       'verified_at': datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        if uid:
            current = json.loads(kubectl(context, 'get', 'namespace', namespace, '-o', 'json'))
            if (current['metadata']['uid'] != uid or
                    current['metadata'].get('labels', {}).get(LABEL) != namespace):
                report['cleanup_complete'] = False
                write_json(output, report)
                raise RuntimeError('Restore namespace ownership mismatch; cleanup refused')
            kubectl(context, 'delete', 'namespace', namespace, '--wait=true', '--timeout=120s')
        report['cleanup_complete'] = True
        write_json(output, report)
    print(json.dumps({'result': report['result'], 'output': str(output),
                      'external_storage_verified': False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--directory', type=Path, required=True)
    res = sub.add_parser('restore')
    res.add_argument('--dump', type=Path, required=True)
    res.add_argument('--manifest', type=Path, required=True)
    res.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.context, args.directory)
    else:
        restore(args.context, args.dump, args.manifest, args.output)


if __name__ == '__main__':
    main()
