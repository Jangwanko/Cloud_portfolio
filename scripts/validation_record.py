"""Small durable records shared by read-only checks and command validation."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def source_identity():
    def git(*args):
        try:
            return subprocess.check_output(['git', *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    return {'commit': git('rev-parse', 'HEAD'), 'branch': git('branch', '--show-current'),
            'dirty': bool(git('status', '--porcelain'))}


def new_record(kind, output_dir=None):
    directory = Path(output_dir) if output_dir else ROOT / 'results' / 'validation'
    run_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    path = directory / run_id / 'summary.json'
    payload = {'schema_version': 1, 'kind': kind, 'run_id': run_id,
               'started_at': utc_now(), 'status': 'running', 'source': source_identity()}
    atomic_json(path, payload)
    return path, payload
