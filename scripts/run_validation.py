"""Record start before launching a command, stream logs to disk, preserve exit code.

Use only commands whose arguments/output contain no credentials. Records stay local.
A killed runner leaves status=running, which is incomplete, never a successful Gate.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import time

try:
    from .validation_record import ROOT, atomic_json, new_record, utc_now
except ImportError:
    from validation_record import ROOT, atomic_json, new_record, utc_now


def run(command, label, output_dir=None):
    path, record = new_record('command', output_dir)
    record.update(label=label, command=command, cwd=str(ROOT), log='output.log')
    atomic_json(path, record)
    print(f'Validation record: {path}', flush=True)
    started = time.monotonic()
    process = None
    code = 127
    try:
        with (path.parent / 'output.log').open('wb', buffering=0) as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL)
            record['pid'] = process.pid
            atomic_json(path, record)
            code = process.wait()
        record['status'] = 'passed' if code == 0 else 'failed'
    except KeyboardInterrupt:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait()
        code = 130
        record['status'] = 'interrupted'
    except OSError as exc:
        record.update(status='failed', error_type=type(exc).__name__)
    finally:
        record.update(exit_code=code, finished_at=utc_now(), duration_seconds=round(time.monotonic()-started, 3))
        atomic_json(path, record)
    print(f'{record["status"]}: exit={code}; log={path.parent / "output.log"}', flush=True)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('command required after --')
    return run(command, args.label, args.output_dir)


if __name__ == '__main__':
    sys.exit(main())
