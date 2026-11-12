"""Prepare the pinned upstream emulator for this local Terraform trial only."""
import difflib
import hashlib
import json
from pathlib import Path
import re
import tomllib

root = Path('/src')
out = Path('/out')
out.mkdir(exist_ok=True)
workspace = tomllib.loads((root / 'Cargo.toml').read_text())
workspace_deps = workspace['workspace']['dependencies']
needed = {'acm', 'autoscaling', 'ec2', 'ecr', 'eks', 'iam', 'kafka', 'kms',
          'cloudwatchlogs', 'rds', 'route53', 'secretsmanager', 'ssm', 'sts', 'ebs'}
service_paths = [p for p in workspace['workspace']['members']
                 if p.startswith('services/service-com-amazonaws-')]
seeds = [root / p for p in service_paths
         if p.removeprefix('services/service-com-amazonaws-').split('-')[0] in needed]
seen = set()
def visit(path):
    path = path.resolve()
    if path in seen:
        return
    seen.add(path)
    manifest = tomllib.loads((path / 'Cargo.toml').read_text())
    sections = [manifest.get('dependencies', {}), manifest.get('build-dependencies', {})]
    for target in manifest.get('target', {}).values():
        sections.extend([target.get('dependencies', {}), target.get('build-dependencies', {})])
    for section in sections:
        for name, dep in section.items():
            if not isinstance(dep, dict):
                continue
            if dep.get('workspace'):
                dep = workspace_deps[name]
                base = root
            else:
                base = path
            if isinstance(dep, dict) and 'path' in dep:
                visit(base / dep['path'])
for path in seeds:
    visit(path)
services = {tomllib.loads((p / 'Cargo.toml').read_text())['package']['name']
            for p in seen if p.name.startswith('service-com-amazonaws-')}
originals = {}
def change(relative, transform):
    p = root / relative
    original = p.read_text()
    updated = transform(original)
    if original == updated:
        raise RuntimeError(f'Expected a change in {relative}')
    originals[relative] = original
    p.write_text(updated)

acl_path = 'services/service-com-amazonaws-ec2-amazonec2/src/state/instance_ids_and_instance_types.rs'
def fix_acl(s):
    old = '            nat_gateways,\n            network_insights_access_scope_analyses => view,'
    if s.count(old) != 1:
        raise RuntimeError('ACL source does not match pinned patch')
    return s.replace(old, old.replace('            network_insights', '            network_acls,\n            network_insights'), 1)
change(acl_path, fix_acl)

sg_path = 'services/service-com-amazonaws-ec2-amazonec2/src/state/vpcs_and_security_groups.rs'
def fix_sg(s):
    old = '        let ip_protocol = canonical_ip_protocol(ip_protocol.unwrap_or(ALL_IP_PROTOCOLS).trim());\n        Self {'
    new = '''        let ip_protocol = canonical_ip_protocol(ip_protocol.unwrap_or(ALL_IP_PROTOCOLS).trim());
        // All-protocol rules do not have a port range. AWS Provider sends 0/0
        // when revoking the default rule, while the stored rule omits ports.
        let (from_port, to_port) = if ip_protocol == ALL_IP_PROTOCOLS {
            (None, None)
        } else {
            (from_port, to_port)
        };
        Self {'''
    if s.count(old) != 1:
        raise RuntimeError('SG source does not match pinned patch')
    s = s.replace(old, new, 1)
    old_comment = '''    /// The port range is kept exactly as given — a rule's ports are values a request "must
    /// match … exactly" (`RevokeSecurityGroupIngress`), so neither end is dropped or
    /// defaulted for any protocol.'''
    return s.replace(old_comment, '''    /// All-protocol rules ignore port fields when matching. Other protocols
    /// retain the supplied range.''', 1)
change(sg_path, fix_sg)

# Keep the original server/auth/runtime code and registration chunks. Only trim
# unrelated service registrations/dependencies to avoid compiling every AWS API.
def trim_cargo(s):
    return '\n'.join(line for line in s.split('\n')
                     if not (line.startswith('service-com-amazonaws-')
                             and line.split(' = ')[0] not in services))
change('core/router/Cargo.toml', trim_cargo)
def trim_router(s):
    keep = {n.replace('-', '_') for n in services}
    return '\n'.join(line for line in s.split('\n')
                     if not ('router.register_service(service_com_amazonaws_' in line
                             and re.search(r'register_service\(([^:]+)::', line).group(1) not in keep))
change('core/router/src/lib.rs', trim_router)
def trim_lock(s):
    pattern = r'(\[\[package\]\]\nname = "aws-emulator-router"\n.*?dependencies = \[\n)(.*?)(\n\])'
    def replace(m):
        lines = [line for line in m[2].split('\n')
                 if not (line.strip().startswith('"service-com-amazonaws-')
                         and line.strip().strip('",') not in services)]
        return m[1] + '\n'.join(lines) + m[3]
    result, count = re.subn(pattern, replace, s, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError('Router package missing from lock file')
    return result
change('Cargo.lock', trim_lock)
for kind, paths in [('ec2-fixes', [acl_path, sg_path]),
                    ('build-service-selection', ['core/router/Cargo.toml', 'core/router/src/lib.rs', 'Cargo.lock'])]:
    patch = ''.join(''.join(difflib.unified_diff(originals[p].splitlines(True),
                       (root/p).read_text().splitlines(True), fromfile='a/'+p, tofile='b/'+p)) for p in paths)
    (out / (kind + '.patch')).write_text(patch)
manifest = {'upstream_commit': '7753983c6e495ec3612695b1336bf4936fc7b7fd',
            'purpose': 'Local Terraform-service build; not an official full-service release',
            'requested_services': sorted(needed), 'mounted_service_crates': sorted(services),
            'service_count': len(services),
            'modified_sha256': {p: hashlib.sha256((root/p).read_bytes()).hexdigest() for p in originals}}
(out / 'build-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
print(json.dumps({'mounted_service_count': len(services), 'services': sorted(services)}), flush=True)
