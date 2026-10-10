"""Collect focused broker cleanup evidence without claiming full qualification."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
root = r / 'local/m02-session-broker-cleanup-01'
run = json.loads((root / 'checks/review.json').read_bytes())
assert run['status'] == 'passed'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
counts = {}
for profile in ('debug', 'runtime-sanitize'):
    value = run['profiles'][profile]; counts[profile] = {}
    for name in ('configure', 'build', 'broker-cleanup', 'focused'):
        entry = value[name]; log = root / 'checks' / profile / (name + '.log')
        assert entry['exit_code'] == 0 and sha(log) == entry['log_sha256']
        if name in ('broker-cleanup', 'focused'):
            text = log.read_text()
            assert not any(x in text for x in ('... skipped', 'skipped=', 'FAILED (', 'runtime error:',
                'ERROR: AddressSanitizer', 'ERROR: LeakSanitizer', 'AddressSanitizer:DEADLYSIGNAL'))
            cases = list(map(int, re.findall(r'^\d+: Ran (\d+) tests? in ', text, re.M)))
            assert len(cases) == (2 if name == 'broker-cleanup' else 9), cases
            if name == 'broker-cleanup': assert cases == [2, 4], cases
            assert f'100% tests passed, 0 tests failed out of {len(cases)}' in text
            counts[profile][name] = cases
    assert all(sha(root / profile / 'build' / n) == h for n, h in value['binaries'].items())
    cache = (root / profile / 'build/CMakeCache.txt').read_text()
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in cache
assert counts['debug'] == counts['runtime-sanitize']
assert len(run['source']) == 340 and len(run['locked']) == 41
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
    source_files=340, locked_files=41, selected_cases_per_profile=sum(sum(c) for c in counts['debug'].values()),
    new_cases=6, failed=0, skipped=0, attachments=attachments,
    verified=[
        'Actual broker, confined application processes and original supervisor exit through retained pidfd witnesses.',
        'The retained session delivers cleanup after supervisor exit and repeats the exact checked receipt.',
        'An independent native credential remains usable after broker scope cleanup.',
        'Broker cleanup does not replace world cleanup. Exact world ownership remains required after broker loss.',
        'Pausing the disposable native server leaves cleanup pending despite process exit. Completion requires its resumed native reply.',
        'Supervisor death without a final receipt cannot claim successful cleanup on initial or repeated requests.',
        'An independent lifecycle fault peer proves rejected exit status remains rejected across retries.',
        'Issuer retirement remains pending. Cleanup does not implicitly deactivate an authority generation.'
    ],
    limitations=[
        'Focused sb16 evidence does not qualify M02, Spatial-M1 or normal host sessions.',
        'Broker issuer retirement and dependent recovery after stopped broker remain unimplemented.',
        'Previously observed participant-approval startup failure remains unresolved.',
        'Replacement recovery, failure races, cross-host cancellation and full two-host qualification remain pending.',
        'Shared compositor lifetime across world transitions remains required for M1.'
    ],
    baselines=['../../session-broker-cleanup-baseline01/sb16/baseline-review.json',
        '../../session-launch-exit-baseline01/sb16/baseline-review.json'])
target = root / 'focused-review.json'
with target.open('x') as stream: json.dump(summary, stream, indent=2); stream.write('\n')
archive = r / 'local/session-broker-cleanup-focused-01-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments: tar.add(root / name, arcname='session-broker-cleanup-01/sb16/' + name, recursive=False)
    tar.add(target, arcname='session-broker-cleanup-01/sb16/focused-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive),
    selected_cases=summary['selected_cases_per_profile'], attachments=len(attachments))))
