import copy
import importlib.util
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[1] / 'scripts/release_failure_lab.py'
SPEC = importlib.util.spec_from_file_location('release_failure_lab', PATH)
lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab)
NS = 'release-lab-unit-test'


@pytest.mark.parametrize('namespace', ['messaging-app', 'default', 'argocd', 'release-lab-', '../lab'])
def test_lab_rejects_shared_or_unsafe_namespace(namespace):
    with pytest.raises(ValueError):
        lab.validate_namespace(namespace)


def test_lab_rejects_cross_namespace_resource_and_wide_project():
    resource = lab.service('api', NS, 8000)
    resource['metadata']['namespace'] = 'messaging-app'
    with pytest.raises(ValueError, match='escapes'):
        lab.validate_resource(resource, NS)
    project = {'kind': 'AppProject',
               'metadata': {'name': NS, 'namespace': 'argocd', 'labels': {lab.LABEL: NS}},
               'spec': {'destinations': [{'namespace': '*', 'server': '*'}]}}
    with pytest.raises(ValueError, match='escapes'):
        lab.validate_resource(project, NS)


def test_only_candidate_pods_mount_synthetic_migration_and_wave_order_is_preserved():
    baseline = lab.release_resources(NS, lab.APP_IMAGE, '0.1.0')
    candidate = lab.release_resources(NS, lab.APP_IMAGE, '0.2.0')
    for resource in baseline + candidate:
        lab.validate_resource(resource, NS)
    mapping = {item['metadata']['name']: item for item in candidate}
    waves = [int(mapping[name]['metadata']['annotations']['argocd.argoproj.io/sync-wave'])
             for name in ('lab-migration-0-2-0', 'messaging-schema-migration', 'worker', 'api')]
    assert waves == [-3, -2, -1, 0]
    for resource in baseline:
        spec = resource['spec']['template']['spec']
        assert all(volume['name'] != 'lab-migration' for volume in spec['volumes'])
    migration = mapping['messaging-schema-migration']
    assert migration['spec']['backoffLimit'] == 0
    assert not any('hook' in key for key in migration['metadata']['annotations'])
    api_volumes = mapping['api']['spec']['template']['spec']['volumes']
    assert next(v for v in api_volumes if v['name'] == 'lab-migration')['configMap']['name'] == 'lab-migration-0-2-0'


@pytest.mark.parametrize('changed', ['uid', 'generation', 'template_hash', 'release', 'available'])
def test_wave_block_evaluator_rejects_changed_or_unavailable_workload(changed):
    before = {name: {'uid': name, 'generation': 1, 'template_hash': 'abc',
                     'release': '0.1.0', 'ready': 1, 'available': 1}
              for name in ('api', 'worker', 'notification-worker')}
    after = copy.deepcopy(before)
    after['worker'][changed] = 0
    with pytest.raises(AssertionError):
        lab.assert_blocked(before, after)


def test_failure_and_forward_revision_have_same_additive_operation_but_different_fault():
    failed, forward = lab.migration_source(True), lab.migration_source(False)
    assert failed.replace("    op.execute('SELECT 1 / 0')\n", '') == forward
    assert 'CREATE TABLE release_lab_marker' in failed
    assert 'DROP TABLE' not in failed
    compile(failed, '<failure fixture>', 'exec')
    compile(forward, '<forward fixture>', 'exec')


def test_helm_repository_can_be_read_as_charts_without_credentials():
    import base64
    import io
    import json
    import tarfile

    repository, hashes = lab.chart_repository(NS, lab.APP_IMAGE, 'http://chart-repository:8080')
    assert set(hashes) == {'0.1.0', '0.2.0', '0.3.0'}
    for version, digest in hashes.items():
        raw = base64.b64decode(repository['binaryData'][f'release-lab-{version}.tgz'])
        assert lab.digest(raw) == digest
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
            chart = json.load(archive.extractfile('release-lab/Chart.yaml'))
            resources = archive.extractfile('release-lab/templates/resources.yaml').read().decode()
        assert chart['version'] == version
        for document in resources.split('\n---\n'):
            resource = json.loads(document)
            lab.validate_resource(resource, NS)
            assert resource['kind'] != 'Secret'
