"""Collect selected coordinator-retirement checks without claiming phase qualification."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
root = r / 'local/m02-coordinator-retirement-02'
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
            assert len(cases) == (1 if name == 'retirement' else 10), cases
            if name == 'retirement': assert cases == [8]
            assert f'100% tests passed, 0 tests failed out of {len(cases)}' in text
            counts[profile][name] = cases
    assert all(sha(root / profile / 'build' / n) == h for n, h in value['binaries'].items())
    cache = (root / profile / 'build/CMakeCache.txt').read_text()
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in cache
assert counts['debug'] == counts['runtime-sanitize']
assert len(run['source']) == 337 and len(run['locked']) == 41
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
    source_files=337, locked_files=41,
    selected_cases_per_profile=sum(sum(c) for c in counts['debug'].values()),
    new_cases=8, failed=0, skipped=0, attachments=attachments,
    verified=[
        'The retained coordinator derives exact fenced ownership and hashes the unchanged durable dependent receipt.',
        'Real world crash cleanup and broker release precede the registered supervisor retirement proof.',
        'The issuer deactivates only the exact world generation and preserves its applied revision and unrelated broker generation.',
        'Successful retirement requires local schema 3 durability. Launch and dependent rows remain unchanged.',
        'Exact retries preserve the saved issuer result without reading the signing key again.',
        'Live, unrecovered, substituted, unfenced, tampered, wrong-key and read-only contexts fail before retirement.',
        'Issuer write failure leaves local schema 2. A local failure after issuer commit permits exact receipt recovery.',
        'Completed journals reject changed receipts and cannot reconstruct ownership or launch replacement processes.',
        'The surviving native application responds to resize and the broker continues serving requests.'
    ],
    limitations=[
        'Focused evidence on sb16 does not qualify the complete M02 phase or normal host sessions.',
        'Previously observed participant-approval startup failure remains unresolved.',
        'Shared compositor lifetime across world transitions remains required for M1.',
        'Complete M02 and M1 qualification on both hosts remains pending.',
        'This path retains a live trusted coordinator. Automatic coordinator restart recovery is not implemented.'
    ],
    baseline='../../coordinator-retirement-baseline01/sb16/baseline-review.json')
target = root / 'focused-review.json'
with target.open('x') as stream: json.dump(summary, stream, indent=2); stream.write('\n')
archive = r / 'local/coordinator-retirement-focused-02-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments: tar.add(root / name, arcname='coordinator-retirement-02/sb16/' + name, recursive=False)
    tar.add(target, arcname='coordinator-retirement-02/sb16/focused-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive),
    selected_cases=summary['selected_cases_per_profile'], attachments=len(attachments))))
