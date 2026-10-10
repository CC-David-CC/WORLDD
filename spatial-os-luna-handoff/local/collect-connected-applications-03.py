"""Retain focused local actor and application recovery evidence without claiming a phase gate."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
root = r / 'local/m02-connected-applications-03'
run = json.loads((root / 'checks/review.json').read_bytes())
assert run['status'] == 'passed'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
counts = {}
for profile in ('debug', 'runtime-sanitize'):
    value = run['profiles'][profile]
    counts[profile] = {}
    for name in ('configure', 'build', 'native-application', 'focused'):
        entry = value[name]; log = root / 'checks' / profile / (name + '.log')
        assert entry['exit_code'] == 0 and sha(log) == entry['log_sha256']
        if name in ('native-application', 'focused'):
            text = log.read_text()
            assert not any(x in text for x in ('... skipped', 'skipped=', 'FAILED (', 'runtime error:',
                'ERROR: AddressSanitizer', 'ERROR: LeakSanitizer', 'AddressSanitizer:DEADLYSIGNAL'))
            cases = list(map(int, re.findall(r'^\d+: Ran (\d+) tests? in ', text, re.M)))
            assert len(cases) == (1 if name == 'native-application' else 7), cases
            if name == 'native-application': assert cases == [2]
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
    new_native_application_cases=2, failed=0, skipped=0, attachments=attachments,
    verified=['Coordinator-created local actor endpoint reaches the real world',
        'World and workspace participants activate through their retained supervisors',
        'Signed actor launches a confined native application through the broker',
        'The application has a distinct mount namespace, no_new_privs, and no inherited private descriptors',
        'The application cannot see an ungranted host file',
        'Actor cancellation releases real native input and its control reservation',
        'World crash releases native input while the application still answers native commands',
        'Checked cleanup matches the durable world journal while the broker remains usable',
        'The exact fenced owner reservation remains pending before selective recovery'],
    pending=['Selective reservation release', 'Durable dependent receipt and issuer retirement',
        'Previously observed participant-approval startup failure', 'Shared compositor lifetime across world transitions',
        'Full M02 and M1 qualification on both hosts'],
    baseline='connected-application-baseline01 preserves remote-input rejection and one fixture evidence-directory error.')
target = root / 'focused-review.json'
with target.open('x') as stream: json.dump(summary, stream, indent=2); stream.write('\n')
archive = r / 'local/connected-applications-focused-03-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments: tar.add(root / name, arcname='connected-applications-03/sb16/' + name, recursive=False)
    tar.add(target, arcname='connected-applications-03/sb16/focused-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive), selected_cases=summary['selected_cases_per_profile'], attachments=len(attachments))))
