"""Upload/download a PostgreSQL dump with explicit S3 destination and SHA-256.

No bucket creation, public ACL, credential persistence, remote deletion, or DB writes.
Install requirements-backup.txt separately from the application dependencies.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse


MAX_SINGLE_PUT = 5 * 1024**3


def fingerprint(path):
    sha = hashlib.sha256()
    size = 0
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
            size += len(chunk)
    if not size:
        raise ValueError('Empty backup is not accepted')
    return {'size_bytes': size, 'sha256': sha.hexdigest()}


def validate_destination(bucket, prefix, endpoint=None):
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket):
        raise ValueError('Expected an explicit bucket name')
    if not prefix or any(p in {'', '.', '..'} for p in prefix.split('/')):
        raise ValueError('Expected a nonempty object prefix without traversal')
    if endpoint:
        url = urlparse(endpoint)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in {'', '/'}):
            raise ValueError('S3 endpoint must be an HTTPS origin without credentials')


def upload(client, path, bucket, prefix):
    validate_destination(bucket, prefix)
    source = fingerprint(path)
    if source['size_bytes'] > MAX_SINGLE_PUT:
        raise ValueError('This drill supports single PUT backups up to 5 GiB')
    run = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex
    filename = Path(path).name
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', filename):
        raise ValueError('Use a simple artifact filename')
    key = f'{prefix}/{run}/{filename}'
    checksum = base64.b64encode(bytes.fromhex(source['sha256'])).decode()
    with Path(path).open('rb') as stream:
        response = client.put_object(
            Bucket=bucket, Key=key, Body=stream, ContentLength=source['size_bytes'],
            ContentType='application/octet-stream', IfNoneMatch='*',
            ChecksumSHA256=checksum, ServerSideEncryption='AES256',
            Metadata={'sha256': source['sha256']})
    if response.get('ChecksumSHA256') != checksum:
        raise RuntimeError('Remote PUT did not confirm the supplied SHA-256; do not mark verified')
    return {'schema_version': 'postgres.object-backup.v1', 'bucket': bucket, 'key': key,
            'version_id': response.get('VersionId'), **source,
            'uploaded_at': datetime.now(timezone.utc).isoformat(),
            'server_checksum_confirmed': True, 'download_verified': False}


def download(client, receipt, target):
    target = Path(target)
    if target.exists():
        raise FileExistsError('Refusing to overwrite download target')
    if (not re.fullmatch(r'[0-9a-f]{64}', receipt['sha256'])
            or not isinstance(receipt['size_bytes'], int) or receipt['size_bytes'] <= 0):
        raise ValueError('Invalid expected fingerprint')
    args = {'Bucket': receipt['bucket'], 'Key': receipt['key'], 'ChecksumMode': 'ENABLED'}
    if receipt.get('version_id'):
        args['VersionId'] = receipt['version_id']
    response = client.get_object(**args)
    partial = target.with_name(target.name + '.' + uuid.uuid4().hex + '.partial')
    body = response['Body']
    try:
        if response['ContentLength'] != receipt['size_bytes']:
            raise ValueError('Remote backup size mismatch')
        sha, size = hashlib.sha256(), 0
        with partial.open('xb') as stream:
            for chunk in iter(lambda: body.read(1024 * 1024), b''):
                size += len(chunk)
                if size > receipt['size_bytes']:
                    raise ValueError('Download exceeded expected size')
                sha.update(chunk)
                stream.write(chunk)
        if size != receipt['size_bytes'] or sha.hexdigest() != receipt['sha256']:
            raise ValueError('Backup integrity mismatch; restore must not proceed')
        # Publish only a complete verified file; hard-link creation refuses overwrite.
        # The temporary file is on the same filesystem as the destination.
        os.link(partial, target)
        return {**receipt, 'download_verified': True,
                'downloaded_at': datetime.now(timezone.utc).isoformat()}
    finally:
        body.close()
        if partial.exists():
            partial.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile')
    parser.add_argument('--region', required=True)
    parser.add_argument('--endpoint-url')
    sub = parser.add_subparsers(dest='action', required=True)
    put = sub.add_parser('upload')
    put.add_argument('--file', type=Path, required=True)
    put.add_argument('--bucket', required=True)
    put.add_argument('--prefix', required=True)
    put.add_argument('--receipt', type=Path, required=True)
    get = sub.add_parser('download')
    get.add_argument('--receipt', type=Path, required=True)
    get.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    receipt = None
    if args.action == 'upload':
        if args.receipt.exists():
            parser.error('Receipt already exists')
        validate_destination(args.bucket, args.prefix, args.endpoint_url)
    else:
        receipt = json.loads(args.receipt.read_text(encoding='utf-8'))
        validate_destination(receipt['bucket'], receipt['key'], args.endpoint_url)
    import boto3
    from botocore.config import Config
    client = boto3.Session(profile_name=args.profile, region_name=args.region).client(
        's3', endpoint_url=args.endpoint_url,
        config=Config(connect_timeout=10, read_timeout=120, retries={'max_attempts': 3}))
    if args.action == 'upload':
        result = upload(client, args.file, args.bucket, args.prefix)
        with args.receipt.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
    else:
        result = download(client, receipt, args.output)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
