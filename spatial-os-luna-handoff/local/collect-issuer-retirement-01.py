"""Collect selected issuer-retirement checks without claiming phase qualification."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
root = r / 'local/m02-issuer-retirement-01'
run = json.loads((root / 'checks/review.json').read_bytes())
assert run['status'] == 'passed'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
counts = {}
for profile in ('debug', 'runtime-sanitize'):
    value = run['profiles'][profile]; counts[profile] = {}
    for name in ('configure', 'build', 'retirement', 'focused'):
        entry = value[name]; log = root / 'checks' / profile / (name + '.log')
        assert entry['exit_code'] == 0 and sha(log) == entry['log_sha256']
        if name in ('retirement', 'focused'):
            text = log.read_text()
            assert not any(x in text for x in ('... skipped', 'skipped=', 'FAILED (', 'runtime error:',
                'ERROR: AddressSanitizer', 'ERROR: LeakSanitizer', 'AddressSanitizer:DEADLYSIGNAL'))
            cases = list(map(int, re.findall(r'^\d+: Ran (\d+) tests? in ', text, re.M)))
            assert len(cases) == (2 if name == 'retirement' else 12), cases
            if name == 'retirement': assert cases == [21, 6]
            assert f'100% tests passed, 0 tests failed out of {len(cases)}' in text
            counts[profile][name] = cases
    assert all(sha(root / profile / 'build' / n) == h for n, h in value['binaries'].items())
    cache = (root / profile / 'build/CMakeCache.txt').read_text()
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in cache
assert counts['debug'] == counts['runtime-sanitize']
assert len(run['source']) == 336 and len(run['locked']) == 41
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
    source_files=336, locked_files=41,
    selected_cases_per_profile=sum(sum(c) for c in counts['debug'].values()),
    new_cases=27, failed=0, skipped=0, attachments=attachments,
    verified=[
        'Fresh supervisor proofs bind the exact fenced generation, cleanup digest, issuer incarnation, and TLS connection.',
        'Retirement deactivates only that generation and preserves its applied revision, fence, and historical obligations.',
        'Barriers distinguish verified retirement from service-applied revisions. Unrelated participants remain active and pending.',
        'Revocations committed before retirement remain covered. Later revocations do not obligate the retired generation.',
        'Exact retries, unread replies, and issuer restarts preserve the original retirement record.',
        'Old service sockets and consumed launch authorization cannot reactivate. A replacement requires fresh authorization.',
        'Insert and deactivation failures roll back together. Explicit schema migration preserves identity and history.',
        'The production client rejects substituted challenge, result, proof, and retirement-aware barrier data.',
        'Existing fencing, launch, identity, barrier, and native durable-cleanup tests remain passing.'
    ],
    limitations=[
        'The issuer protocol tests supply fixture cleanup digests. They do not prove external cleanup.',
        'The trusted coordinator signer and actual cleanup-to-retirement integration remain unimplemented.',
        'No normal host issuer database was migrated and no normal native session adopted this candidate.',
        'Previously observed participant-approval startup failure remains unresolved.',
        'Shared compositor lifetime across world transitions remains required.',
        'Complete M02 and M1 qualification on both hosts remains pending.'
    ],
    baseline='../../retirement-baseline01/sb16/baseline-review.json')
target = root / 'focused-review.json'
with target.open('x') as stream: json.dump(summary, stream, indent=2); stream.write('\n')
archive = r / 'local/issuer-retirement-focused-01-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments: tar.add(root / name, arcname='issuer-retirement-01/sb16/' + name, recursive=False)
    tar.add(target, arcname='issuer-retirement-01/sb16/focused-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive),
    selected_cases=summary['selected_cases_per_profile'], attachments=len(attachments))))
