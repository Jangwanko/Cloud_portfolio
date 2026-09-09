import io
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from object_storage_backup import download, fingerprint, upload, validate_destination


class Store:
    def put_object(self, **kw):
        self.put = kw
        self.data = kw['Body'].read()
        return {'ChecksumSHA256': kw['ChecksumSHA256'], 'VersionId': 'version-1'}

    def get_object(self, **kw):
        self.get = kw
        return {'Body': io.BytesIO(self.data), 'ContentLength': len(self.data)}


def receipt(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'PGDMP-fixture')
    store = Store()
    result = upload(store, source, 'private-backup-bucket', 'portfolio/drill')
    return store, result


def test_roundtrip_pins_version_and_requires_integrity(tmp_path):
    store, record = receipt(tmp_path)
    target = tmp_path / 'download'
    assert download(store, record, target)['download_verified']
    assert fingerprint(target)['sha256'] == record['sha256']
    assert store.get['VersionId'] == 'version-1'
    assert store.put['IfNoneMatch'] == '*'
    assert store.put['ServerSideEncryption'] == 'AES256'


@pytest.mark.parametrize('data', [b'corrupt-same!', b'short', b'PGDMP-fixture-extra'])
def test_corrupt_download_never_produces_restore_target(tmp_path, data):
    store, record = receipt(tmp_path)
    store.data = data
    target = tmp_path / 'download'
    with pytest.raises(ValueError):
        download(store, record, target)
    assert not target.exists()
    assert not list(tmp_path.glob('*.partial'))


def test_download_does_not_overwrite_existing_dump(tmp_path):
    store, record = receipt(tmp_path)
    target = tmp_path / 'download'
    target.write_bytes(b'preserve')
    with pytest.raises(FileExistsError):
        download(store, record, target)
    assert target.read_bytes() == b'preserve'


def test_put_without_checksum_confirmation_is_not_verified(tmp_path):
    class Unverified(Store):
        def put_object(self, **kwargs):
            return {}
    path = tmp_path / 'dump'
    path.write_bytes(b'dump')
    with pytest.raises(RuntimeError):
        upload(Unverified(), path, 'private-backup-bucket', 'drill')


@pytest.mark.parametrize('endpoint', ['http://localhost:9000', 'https://u:p@host',
                                      'https://host/?token=secret'])
def test_unsafe_endpoint_rejected(endpoint):
    with pytest.raises(ValueError):
        validate_destination('private-backup-bucket', 'drill', endpoint)
