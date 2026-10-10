"""Collect terminal native replacement evidence without treating failures as passes."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
root = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
candidate = root / 'local/m02-session-replacement-01'
run = json.loads((candidate / 'checks/review.json').read_bytes())
assert run['status'] in ('passed', 'failed') and 'ended' in run
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert len(run['source']) == 343 and len(run['locked']) == 41
for group in ('source', 'locked'):
    assert all(sha(candidate / 'source' / name) == digest for name, digest in run[group].items())
assert sha(Path(run['server']['path'])) == run['server']['sha256']
counts = {}
for profile, steps in run['profiles'].items():
    for name, step in steps.items():
        if name == 'binaries':
            assert all(sha(candidate / profile / 'build' / binary) == digest for binary, digest in step.items())
        elif 'log_sha256' in step:
            assert sha(candidate / 'checks' / profile / (name + '.log')) == step['log_sha256']
    if run['status'] != 'passed':
        continue
    counts[profile] = {}
    for name in ('configure', 'build', 'replacement', 'focused'):
        assert steps[name]['exit_code'] == 0
        if name not in ('replacement', 'focused'):
            continue
        text = (candidate / 'checks' / profile / (name + '.log')).read_text()
        assert not any(marker in text for marker in ('... skipped', 'skipped=', 'FAILED (', 'runtime error:',
            'ERROR: AddressSanitizer', 'ERROR: LeakSanitizer', 'AddressSanitizer:DEADLYSIGNAL'))
        cases = list(map(int, re.findall(r'^\d+: Ran (\d+) tests? in ', text, re.M)))
        assert len(cases) == (1 if name == 'replacement' else 7), cases
        if name == 'replacement':
            assert cases == [3]
        assert f'100% tests passed, 0 tests failed out of {len(cases)}' in text
        counts[profile][name] = cases
    cache = (candidate / profile / 'build/CMakeCache.txt').read_text()
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in cache
if run['status'] == 'passed':
    assert counts['debug'] == counts['runtime-sanitize']
attachments = {}
for path in (candidate / 'checks').rglob('*'):
    if path.is_file() and (path.suffix == '.log' or path.name == 'review.json'):
        data = path.read_bytes()
        assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
        attachments[path.relative_to(candidate).as_posix()] = sha(path)
summary = dict(status='focused_passed_phase_gate_pending' if run['status'] == 'passed' else 'preserved_failure',
    host='sb16', source_files=343, locked_files=41, profiles=counts, attachments=attachments,
    selected_cases_per_profile=sum(sum(cases) for cases in counts.get('debug', {}).values()),
    production_launch_changed=False,
    limitations=['Full M02 qualification remains pending on both hosts.',
        'Broker replacement, failure races and cross-host cancellation remain required.',
        'The earlier participant-approval startup failure remains unresolved.',
        'Automatic application placement restoration and shared compositor lifetime remain required for M1.'])
with (candidate / 'collected-review.json').open('x') as stream:
    json.dump(summary, stream, indent=2); stream.write('\n')
prefix = 'session-replacement-01/sb16' if run['status'] == 'passed' else 'session-replacement-failure01/sb16'
archive = root / 'local/session-replacement-checks-01-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments:
        tar.add(candidate / name, arcname=prefix + '/' + name, recursive=False)
    tar.add(candidate / 'collected-review.json', arcname=prefix + '/collected-review.json', recursive=False)
print(json.dumps(dict(status=summary['status'], archive=str(archive), sha256=sha(archive),
    selected_cases_per_profile=summary['selected_cases_per_profile'], attachments=len(attachments))))
