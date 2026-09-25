"""Validate changed local Markdown links and conflict markers without network access."""
import argparse
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def anchors(text):
    result, seen = set(), {}
    # Fenced examples do not contribute headings.
    text = re.sub(r'^```.*?^```\s*$', '', text, flags=re.M | re.S)
    for heading in re.findall(r'^#{1,6}\s+(.+?)\s*#*$', text, re.M):
        heading = re.sub(r'<[^>]+>', '', heading)
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(slug + (f'-{count}' if count else ''))
    result.update(re.findall(r'(?:id|name)=["\']([^"\']+)["\']', text))
    return result


def check_file(path, root=ROOT):
    text = path.read_text(encoding='utf-8-sig')
    errors = []
    if re.search(r'^(?:<{7}|={7}|>{7})(?: |$)', text, re.M):
        errors.append('merge conflict marker')
    if path.suffix == '.json':
        json.loads(text)
        return errors
    # Ignore fenced code examples and external/mailto links.
    prose = re.sub(r'^```.*?^```\s*$', '', text, flags=re.M | re.S)
    for link in re.findall(r'\]\(([^)]+)\)', prose):
        parsed = urlsplit(link.strip('<>'))
        if parsed.scheme or parsed.netloc:
            continue
        target = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path.resolve()
        if not target.is_relative_to(root.resolve()):
            errors.append('link outside repository: ' + link)
        elif not target.exists():
            errors.append('missing link: ' + link)
        elif parsed.fragment and target.suffix == '.md':
            if unquote(parsed.fragment) not in anchors(target.read_text(encoding='utf-8-sig')):
                errors.append('missing anchor: ' + link)
    return errors


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('paths', nargs='*')
    p.add_argument('--base')
    p.add_argument('--head', default='HEAD')
    args = p.parse_args()
    names = args.paths
    if args.base:
        names = subprocess.check_output(['git', 'diff', '--name-only', '--diff-filter=ACMR', args.base, args.head, '--'], text=True).splitlines()
    if not names:
        names = ['README.md', 'README_EN.md']
    errors = []
    for name in names:
        path = ROOT / name
        if path.suffix in {'.md', '.json'} and path.is_file():
            errors.extend(f'{name}: {error}' for error in check_file(path))
    for error in errors:
        print(error)
    print(f'Document validation: {len(errors)} errors')
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
