"""Collect focused stopped-broker dependency checks, preserving exact source and evidence."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
root = r / 'local/m02-stopped-broker-dependency-01'
run = json.loads((root / 'checks/review.json').read_bytes()); assert run['status'] == 'passed'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
counts = {}
for profile in ('debug', 'runtime-sanitize'):
    value = run['profiles'][profile]; counts[profile] = {}
    for name in ('configure', 'build', 'stopped-broker-dependency', 'focused'):
        entry = value[name]; log = root / 'checks' / profile / (name + '.log')
        assert entry['exit_code'] == 0 and sha(log) == entry['log_sha256']
        if name in ('stopped-broker-dependency', 'focused'):
            text = log.read_text()
            assert not any(x in text for x in ('... skipped', 'skipped=', 'FAILED (', 'runtime error:',
                'ERROR: AddressSanitizer', 'ERROR: LeakSanitizer', 'AddressSanitizer:DEADLYSIGNAL'))
            cases = list(map(int, re.findall(r'^\d+: Ran (\d+) tests? in ', text, re.M)))
            assert len(cases) == (1 if name == 'stopped-broker-dependency' else 8), cases
            if name == 'stopped-broker-dependency': assert cases == [9], cases
            assert f'100% tests passed, 0 tests failed out of {len(cases)}' in text
            counts[profile][name] = cases
    assert all(sha(root / profile / 'build' / n) == h for n, h in value['binaries'].items())
    cache = (root / profile / 'build/CMakeCache.txt').read_text()
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in cache
assert counts['debug'] == counts['runtime-sanitize']
assert len(run['source']) == 342 and len(run['locked']) == 41
for group in ('source', 'locked'):
    assert all(sha(root / 'source' / n) == h for n, h in run[group].items())
assert sha(Path(run['server']['path'])) == run['server']['sha256']
attachments = {}
for path in (root / 'checks').rglob('*'):
    if path.is_file() and (path.suffix == '.log' or path.name == 'review.json'):
        data = path.read_bytes()
        assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
        attachments[path.relative_to(root).as_posix()] = sha(path)
summary = dict(status='focused_passed_phase_gate_pending', host='sb16', profiles=counts,
    source_files=342, locked_files=41, selected_cases_per_profile=sum(sum(c) for c in counts['debug'].values()),
    new_cases=9, failed=0, skipped=0, attachments=attachments,
    verified=[
        'Actual world and broker crashes have checked retained process and native cleanup before dependency recovery.',
        'Version 2 binds the exact stopped world and broker records without inventing a control reply or released reservation list.',
        'The original world launch bytes and revision remain unchanged during the explicit dependency migration.',
        'World retirement binds the exact durable dependency bytes and remains available through retained ownership after broker exit.',
        'Existing version 1 receipts remain valid after broker exit and cannot be replaced with a different proof version.',
        'Live, unchecked, substituted, missing-receipt, corrupted and reordered contexts cannot authorize recovery or retirement.',
        'Failed local dependency storage leaves retirement pending and permits exact retry.',
        'Issuer write failure and local failure after issuer commit preserve the exact original retirement result for retry.',
        'Reopened dependency and retirement journals cannot recreate process ownership or authorize replacement execution.'
    ],
    limitations=[
        'Focused sb16 evidence does not qualify M02, Spatial-M1 or normal host sessions.',
        'Replacement recovery, failure races, cross-host cancellation and full two-host qualification remain pending.',
        'The earlier participant-approval startup failure remains unresolved.',
        'Shared compositor lifetime across world transitions remains required for M1.'
    ], baseline='../../stopped-broker-dependency-baseline01/sb16/baseline-review.json')
target = root / 'focused-review.json'
with target.open('x') as stream: json.dump(summary, stream, indent=2); stream.write('\n')
archive = r / 'local/stopped-broker-dependency-focused-01-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments: tar.add(root / name, arcname='stopped-broker-dependency-01/sb16/' + name, recursive=False)
    tar.add(target, arcname='stopped-broker-dependency-01/sb16/focused-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive),
    selected_cases=summary['selected_cases_per_profile'], attachments=len(attachments))))
