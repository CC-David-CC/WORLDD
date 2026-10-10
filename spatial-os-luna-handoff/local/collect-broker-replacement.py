"""Collect terminal exploratory or registered broker replacement checks."""
import hashlib
import json
from pathlib import Path
import re
import socket
import sys
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
tag = sys.argv[1]; assert tag in ('01', '02')
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / ('m02-broker-replacement-' + tag)
run = json.loads((root / 'checks/review.json').read_bytes())
assert run['status'] in ('passed', 'failed') and 'ended' in run
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert len(run['source']) == 344 and len(run['locked']) == 41
for group in ('source', 'locked'):
    assert all(sha(root / 'source' / name) == digest for name, digest in run[group].items())
assert sha(Path(run['server']['path'])) == run['server']['sha256']
if tag == '01':
    prior_root = Path(run['reused_builds'])
    assert prior_root == base / 'm02-session-replacement-02'
    assert sha(prior_root / 'checks/review.json') == run['build_provenance_sha256']
    prior = json.loads((prior_root / 'checks/review.json').read_bytes())
    assert all(sha(prior_root / 'source' / name) == digest for name, digest in prior['source'].items())
counts = {}
for profile, steps in run['profiles'].items():
    build = (prior_root if tag == '01' else root) / profile / 'build'
    if 'binaries' in steps:
        assert all(sha(build / name) == digest for name, digest in steps['binaries'].items())
    if tag == '01':
        assert prior['profiles'][profile]['build']['exit_code'] == 0
        assert steps['binaries'] == prior['profiles'][profile]['binaries']
        assert sha(root / 'checks' / profile / 'replacement.log') == steps['log_sha256']
    else:
        for label in ('configure', 'build', 'replacement', 'focused'):
            if label in steps and 'log_sha256' in steps[label]:
                assert sha(root / 'checks' / profile / (label + '.log')) == steps[label]['log_sha256']
    if run['status'] != 'passed': continue
    cache = (build / 'CMakeCache.txt').read_text()
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in cache
    counts[profile] = {}
    for label in (['replacement'] if tag == '01' else ['configure', 'build', 'replacement', 'focused']):
        assert (steps if tag == '01' else steps[label])['exit_code'] == 0
        if label in ('configure', 'build'): continue
        text = (root / 'checks' / profile / (label + '.log')).read_text()
        assert not any(marker in text for marker in ('... skipped', 'skipped=', 'FAILED (', 'runtime error:',
            'ERROR: AddressSanitizer', 'ERROR: LeakSanitizer', 'AddressSanitizer:DEADLYSIGNAL'))
        pattern = r'^Ran (\d+) tests? in ' if tag == '01' else r'^\d+: Ran (\d+) tests? in '
        cases = list(map(int, re.findall(pattern, text, re.M)))
        assert len(cases) == (1 if label == 'replacement' else 4), cases
        if label == 'replacement': assert cases == [3]
        if tag == '01': assert '\nOK\n' in text
        else: assert f'100% tests passed, 0 tests failed out of {len(cases)}' in text
        counts[profile][label] = cases
if run['status'] == 'passed': assert counts['debug'] == counts['runtime-sanitize']
attachments = {}
for path in (root / 'checks').rglob('*'):
    if path.is_file() and (path.suffix == '.log' or path.name == 'review.json'):
        data = path.read_bytes()
        assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
        attachments[path.relative_to(root).as_posix()] = sha(path)
summary = dict(status='focused_passed_phase_gate_pending' if run['status'] == 'passed' else 'preserved_failure',
    host='sb16', source_files=344, locked_files=41, profiles=counts, attachments=attachments,
    selected_cases_per_profile=sum(sum(cases) for cases in counts.get('debug', {}).values()),
    production_launch_changed=False, reused_builds=run.get('reused_builds'),
    limitations=['Full M02 qualification remains pending on both hosts.',
        'Failure races and cross-host cancellation remain required.',
        'The earlier participant-approval startup failure remains unresolved.',
        'Automatic application placement restoration and shared compositor lifetime remain required for M1.'])
with (root / 'collected-review.json').open('x') as stream:
    json.dump(summary, stream, indent=2); stream.write('\n')
prefix = ('broker-replacement-' if run['status'] == 'passed' else 'broker-replacement-failure') + tag + '/sb16'
archive = base / ('broker-replacement-checks-' + tag + '-sb16.tar.gz')
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments: tar.add(root / name, arcname=prefix + '/' + name, recursive=False)
    tar.add(root / 'collected-review.json', arcname=prefix + '/collected-review.json', recursive=False)
    tar.add(root / 'source/tests/native/test_broker_replacement.py', arcname=prefix + '/test_broker_replacement.py', recursive=False)
print(json.dumps(dict(status=summary['status'], archive=str(archive), sha256=sha(archive),
    selected_cases_per_profile=summary['selected_cases_per_profile'], attachments=len(attachments))))
