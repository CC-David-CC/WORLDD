"""Collect the selected durable-cleanup checks without claiming phase qualification."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
root = r / 'local/m02-dependent-cleanup-01'
run = json.loads((root / 'checks/review.json').read_bytes())
assert run['status'] == 'passed'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
counts = {}
for profile in ('debug', 'runtime-sanitize'):
    value = run['profiles'][profile]; counts[profile] = {}
    for name in ('configure', 'build', 'dependent-cleanup', 'focused'):
        entry = value[name]; log = root / 'checks' / profile / (name + '.log')
        assert entry['exit_code'] == 0 and sha(log) == entry['log_sha256']
        if name in ('dependent-cleanup', 'focused'):
            text = log.read_text()
            assert not any(x in text for x in ('... skipped', 'skipped=', 'FAILED (', 'runtime error:',
                'ERROR: AddressSanitizer', 'ERROR: LeakSanitizer', 'AddressSanitizer:DEADLYSIGNAL'))
            cases = list(map(int, re.findall(r'^\d+: Ran (\d+) tests? in ', text, re.M)))
            assert len(cases) == (1 if name == 'dependent-cleanup' else 8), cases
            if name == 'dependent-cleanup': assert cases == [5]
            assert f'100% tests passed, 0 tests failed out of {len(cases)}' in text
            counts[profile][name] = cases
    assert all(sha(root / profile / 'build' / n) == h for n, h in value['binaries'].items())
    cache = (root / profile / 'build/CMakeCache.txt').read_text()
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in cache
assert counts['debug'] == counts['runtime-sanitize']
assert len(run['source']) == 334 and len(run['locked']) == 41
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
    source_files=334, locked_files=41,
    selected_cases_per_profile=sum(sum(c) for c in counts['debug'].values()),
    new_cases=5, failed=0, skipped=0, attachments=attachments,
    verified=[
        'An actual world crash produces checked process and native cleanup before broker recovery.',
        'Successful recovery persists the exact world, live broker, and released-reservation receipt.',
        'The explicit schema migration preserves the original launch row and revision.',
        'Exact retries preserve byte-identical records and the independently authorized application responds.',
        'Read-only or locked journals report failure after broker release and allow an exact retry after repair.',
        'Changed launch records and corrupted or changed dependent records are rejected without replacement.',
        'Reopening a completed journal does not recreate ownership or permit replacement execution.'
    ],
    limitations=[
        'Issuer retirement remains unimplemented. Persistence alone does not clear issuer obligations.',
        'Recovery requires the original retained coordinator and broker ownership. No restart recovery path is claimed.',
        'No physical power-loss injection was performed; storage relies on the existing SQLite durability configuration and directory fsync.',
        'Previously observed participant-approval startup failure remains unresolved.',
        'Shared compositor lifetime across world transitions remains required.',
        'Complete M02 and M1 qualification on both hosts remains pending.'
    ],
    baseline='../../dependent-cleanup-baseline01/sb16/baseline-review.json')
target = root / 'focused-review.json'
with target.open('x') as stream: json.dump(summary, stream, indent=2); stream.write('\n')
archive = r / 'local/dependent-cleanup-focused-01-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments: tar.add(root / name, arcname='dependent-cleanup-01/sb16/' + name, recursive=False)
    tar.add(target, arcname='dependent-cleanup-01/sb16/focused-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive),
    selected_cases=summary['selected_cases_per_profile'], attachments=len(attachments))))
