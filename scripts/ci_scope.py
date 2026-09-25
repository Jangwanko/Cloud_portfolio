"""Conservative CI routing: unknown changes and unavailable history use full CI."""
import argparse
import json
import os
from pathlib import PurePosixPath
import re
import subprocess


def docs_only(paths):
    if not paths:
        return False
    for name in paths:
        path = PurePosixPath(name)
        # Never classify executable examples, workflow, tests or runtime JSON as docs.
        if '..' in path.parts or not (
            name in {'README.md', 'README_EN.md', 'AGENTS.md'}
            or name.startswith('docs/') and path.suffix == '.md'
            or name.startswith('docs/harness/') and path.suffix == '.json'
        ):
            return False
    return True


def changed_paths(base, head, *, run=subprocess.check_output):
    if not all(re.fullmatch(r'[0-9a-fA-F]{40}', rev or '') and set(rev) != {'0'} for rev in (base, head)):
        return None
    try:
        # --no-renames includes old and new names so code renamed into docs stays full.
        raw = run(['git', 'diff', '--no-renames', '--name-only', '-z', base, head, '--'], timeout=15)
        return [p.decode('utf-8') for p in raw.split(b'\0') if p]
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', default=os.environ.get('CI_BASE_SHA', ''))
    p.add_argument('--head', default=os.environ.get('CI_HEAD_SHA', ''))
    p.add_argument('--github-output', default=os.environ.get('GITHUB_OUTPUT'))
    args = p.parse_args()
    paths = changed_paths(args.base, args.head)
    value = docs_only(paths)
    print(json.dumps({'docs_only': value, 'changed_files': len(paths) if paths is not None else None}))
    if args.github_output:
        with open(args.github_output, 'a', encoding='utf-8') as stream:
            stream.write('docs_only=' + str(value).lower() + '\n')


if __name__ == '__main__':
    main()
