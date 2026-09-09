import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from postgres_backup_drill import require_matching_manifest, restore


@pytest.mark.parametrize('change', ['rows', 'content', 'sequence', 'schema'])
def test_restore_manifest_detects_loss_or_semantic_change(change):
    expected = {'tables': {'messages': {'rows': 10, 'rows_sha256': 'a'}},
                'sequences': {'messages_id_seq': '10|t'}, 'alembic_version': '0008'}
    actual = copy.deepcopy(expected)
    if change == 'rows':
        actual['tables']['messages']['rows'] = 9
    elif change == 'content':
        actual['tables']['messages']['rows_sha256'] = 'b'
    elif change == 'sequence':
        actual['sequences']['messages_id_seq'] = '1|f'
    else:
        actual['alembic_version'] = '0007'
    with pytest.raises(ValueError):
        require_matching_manifest(expected, actual)


def test_corrupt_dump_rejected_before_any_kubernetes_call(tmp_path, monkeypatch):
    import json
    import postgres_backup_drill as module
    monkeypatch.setattr(module, 'kubectl', lambda *a, **kw: pytest.fail('Unexpected cluster call'))
    dump = tmp_path / 'dump'
    dump.write_bytes(b'corrupt')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'result': 'PREPARED', 'dump': {
        'sha256': '0' * 64, 'size_bytes': 7}}))
    with pytest.raises(ValueError):
        restore('kind-test', dump, manifest, tmp_path / 'result.json')
