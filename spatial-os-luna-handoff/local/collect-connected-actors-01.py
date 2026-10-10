"""Retain focused actor admission evidence without claiming a phase gate."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
root = r / 'local/m02-connected-actors-01'
run = json.loads((root / 'checks/review.json').read_bytes())
assert run['status'] == 'passed'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
counts = {}
for profile in ('debug', 'runtime-sanitize'):
    value = run['profiles'][profile]
    counts[profile] = {}
    for name in ('configure', 'build', 'world-registration', 'focused'):
        entry = value[name]; log = root / 'checks' / profile / (name + '.log')
        assert entry['exit_code'] == 0 and sha(log) == entry['log_sha256']
        if name in ('world-registration', 'focused'):
            text = log.read_text()
            assert not any(x in text for x in ('... skipped', 'skipped=', 'FAILED (', 'runtime error:',
                'ERROR: AddressSanitizer', 'ERROR: LeakSanitizer', 'AddressSanitizer:DEADLYSIGNAL'))
            cases = list(map(int, re.findall(r'^\d+: Ran (\d+) tests? in ', text, re.M)))
            assert len(cases) == (1 if name == 'world-registration' else 11), cases
            if name == 'world-registration': assert cases == [11]
            assert f'100% tests passed, 0 tests failed out of {len(cases)}' in text
            counts[profile][name] = cases
    assert all(sha(root / profile / 'build' / n) == h for n, h in value['binaries'].items())
    cache = (root / profile / 'build/CMakeCache.txt').read_text()
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in cache
assert counts['debug'] == counts['runtime-sanitize']
for group in ('source', 'locked'):
    assert all(sha(root / 'source' / n) == h for n, h in run[group].items())
assert sha(Path(run['server']['path'])) == run['server']['sha256']
baseline = root / 'checks/baseline/missing-listener.log'
assert sha(baseline) == run['profiles']['baseline']['missing-listener']['log_sha256']
assert 'CONFIRMED: valid inherited TCP listener rejected before world launch' in baseline.read_text()
attachments = {}
for path in (root / 'checks').rglob('*'):
    if path.is_file() and (path.suffix == '.log' or path.name == 'review.json'):
        data = path.read_bytes()
        assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
        attachments[path.relative_to(root).as_posix()] = sha(path)
summary = dict(status='focused_passed_phase_gate_pending', host='sb16', profiles=counts,
    source_files=len(run['source']), locked_files=len(run['locked']),
    selected_cases_per_profile=sum(sum(c) for c in counts['debug'].values()),
    new_actor_cases=5, failed=0, skipped=0, attachments=attachments,
    verified=['Validated TCP listener reaches the actual supervised world',
        'Real issuer credential and signed actor proof permit only granted actor actions',
        'TLS connection alone does not authenticate an actor',
        'Actor transport rejects management commands and ungranted observation',
        'Private coordinator sockets remain absent from world and compositor descriptor inventories',
        'UNIX and unlistening sockets, missing TLS config, and actor descriptor substitutions fail before world ownership'],
    pending=['Confined application recovery through this coordinator', 'Selective reservation release',
        'Durable dependent receipt and issuer retirement', 'Previously observed participant-approval startup failure',
        'Shared compositor lifetime across world transitions', 'Full M02 and M1 qualification on both hosts'])
target = root / 'focused-review.json'
with target.open('x') as stream: json.dump(summary, stream, indent=2); stream.write('\n')
archive = r / 'local/connected-actors-focused-01-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments: tar.add(root / name, arcname='connected-actors-01/sb16/' + name, recursive=False)
    tar.add(target, arcname='connected-actors-01/sb16/focused-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive), selected_cases=summary['selected_cases_per_profile'], attachments=len(attachments))))
