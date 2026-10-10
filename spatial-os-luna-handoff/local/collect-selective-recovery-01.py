"""Collect focused selective recovery evidence without claiming the full phase gate."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
root = r / 'local/m02-selective-recovery-01'
run = json.loads((root / 'checks/review.json').read_bytes())
assert run['status'] == 'passed'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
counts = {}
for profile in ('debug', 'runtime-sanitize'):
    value = run['profiles'][profile]; counts[profile] = {}
    for name in ('configure', 'build', 'native-application', 'focused'):
        entry = value[name]; log = root / 'checks' / profile / (name + '.log')
        assert entry['exit_code'] == 0 and sha(log) == entry['log_sha256']
        if name in ('native-application', 'focused'):
            text = log.read_text()
            assert not any(x in text for x in ('... skipped', 'skipped=', 'FAILED (', 'runtime error:',
                'ERROR: AddressSanitizer', 'ERROR: LeakSanitizer', 'AddressSanitizer:DEADLYSIGNAL'))
            cases = list(map(int, re.findall(r'^\d+: Ran (\d+) tests? in ', text, re.M)))
            assert len(cases) == (1 if name == 'native-application' else 9), cases
            if name == 'native-application': assert cases == [3]
            assert f'100% tests passed, 0 tests failed out of {len(cases)}' in text
            counts[profile][name] = cases
    assert all(sha(root / profile / 'build' / n) == h for n, h in value['binaries'].items())
    cache = (root / profile / 'build/CMakeCache.txt').read_text()
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in cache
assert counts['debug'] == counts['runtime-sanitize']
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
    source_files=len(run['source']), locked_files=len(run['locked']),
    selected_cases_per_profile=sum(sum(c) for c in counts['debug'].values()),
    new_cases=15, failed=0, skipped=0, attachments=attachments,
    verified=[
        'Actual world crash cleanup authorizes selective reservation recovery through retained coordinator ownership.',
        'The live application and broker remain responsive after recovery.',
        'Recovery before checked cleanup and substituted owners are rejected.',
        'Private broker recovery rejects missing, mismatched, and changed cleanup receipts.',
        'World channels cannot invoke recovery. Recovery accepts no transferred descriptors.',
        'Unrelated owners retain their reservations and application authority.',
        'Exact retry and inspection return the original receipt, including after a discarded reply.',
        'Recovery includes acquisitions whose replies were lost and retains the old owner fence.',
        'Client reply validation checks complete receipts and released reservation identities.'
    ],
    limitations=[
        'The broker caches receipts only for its lifetime. Durable dependent receipt and issuer retirement remain unimplemented.',
        'Component coordinator fixtures supply receipt data. The native session scenario verifies actual external cleanup.',
        'Previously observed participant-approval startup failure remains unresolved.',
        'Shared compositor lifetime across world transitions remains required.',
        'Complete M02 and M1 qualification on both hosts remains pending.'
    ],
    baseline='../connected-applications-03/sb16/primary-review.json')
target = root / 'focused-review.json'
with target.open('x') as stream: json.dump(summary, stream, indent=2); stream.write('\n')
archive = r / 'local/selective-recovery-focused-01-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments: tar.add(root / name, arcname='selective-recovery-01/sb16/' + name, recursive=False)
    tar.add(target, arcname='selective-recovery-01/sb16/focused-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive),
    selected_cases=summary['selected_cases_per_profile'], attachments=len(attachments))))
