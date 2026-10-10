"""Collect terminal interruption checks and preserve any related failed runs."""
import hashlib
import json
from pathlib import Path
import re
import socket
import sys
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
tag = sys.argv[1]; assert tag in ('01', '02', '03')
reused = tag in ('01', '03')
single_label = 'interruption' if tag == '03' else 'replacement'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / ('m02-approval-interruption-' + tag)
run = json.loads((root / 'checks/review.json').read_bytes())
assert run['status'] in ('passed', 'failed') and 'ended' in run
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert len(run['source']) == 345 and len(run['locked']) == 41
for group in ('source', 'locked'):
    assert all(sha(root / 'source' / name) == digest for name, digest in run[group].items())
assert sha(Path(run['server']['path'])) == run['server']['sha256']
if reused:
    prior_root = Path(run['reused_builds'])
    assert prior_root == base / ('m02-approval-interruption-02' if tag == '03' else 'm02-participant-diagnostics-02')
    assert sha(prior_root / 'checks/review.json') == run['build_provenance_sha256']
    prior = json.loads((prior_root / 'checks/review.json').read_bytes())
    assert all(sha(prior_root / 'source' / name) == digest for name, digest in prior['source'].items())
counts = {}
for profile, steps in run['profiles'].items():
    build = (prior_root if reused else root) / profile / 'build'
    if 'binaries' in steps:
        assert all(sha(build / name) == digest for name, digest in steps['binaries'].items())
    if reused:
        assert prior['profiles'][profile]['build']['exit_code'] == 0
        assert steps['binaries'] == prior['profiles'][profile]['binaries']
        assert sha(root / 'checks' / profile / (single_label + '.log')) == steps['log_sha256']
    else:
        for label in ('configure', 'build', 'replacement', 'focused'):
            if label in steps and 'log_sha256' in steps[label]:
                assert sha(root / 'checks' / profile / (label + '.log')) == steps[label]['log_sha256']
    if run['status'] != 'passed': continue
    cache = (build / 'CMakeCache.txt').read_text()
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in cache
    counts[profile] = {}
    for label in ([single_label] if reused else ['configure', 'build', 'replacement', 'focused']):
        assert (steps if reused else steps[label])['exit_code'] == 0
        if label in ('configure', 'build'): continue
        text = (root / 'checks' / profile / (label + '.log')).read_text()
        assert not any(marker in text for marker in ('... skipped', 'skipped=', 'FAILED (', 'runtime error:',
            'ERROR: AddressSanitizer', 'ERROR: LeakSanitizer', 'AddressSanitizer:DEADLYSIGNAL'))
        pattern = r'^Ran (\d+) tests? in ' if reused else r'^\d+: Ran (\d+) tests? in '
        cases = list(map(int, re.findall(pattern, text, re.M)))
        assert len(cases) == (4 if label == 'focused' else 1), cases
        if label in ('replacement', 'interruption'): assert cases == ([4] if tag == '03' else [3])
        if reused: assert '\nOK\n' in text
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
    host='sb16', source_files=345, locked_files=41, profiles=counts, attachments=attachments,
    selected_cases_per_profile=sum(sum(cases) for cases in counts.get('debug', {}).values()),
    production_launch_changed=False, reused_builds=run.get('reused_builds'),
    limitations=['Full M02 qualification remains pending on both hosts.',
        'Failure races and cross-host cancellation remain required.',
        'The earlier participant-approval startup failure remains unresolved.',
        'Candidate02 also had an unexplained active-generation revocation-read timeout. Passing controlled faults do not resolve it.',
        'Automatic application placement restoration and shared compositor lifetime remain required for M1.'])
with (root / 'collected-review.json').open('x') as stream:
    json.dump(summary, stream, indent=2); stream.write('\n')
prefix = ('approval-interruption-' if run['status'] == 'passed' else 'approval-interruption-failure') + tag + '/sb16'
archive = base / ('approval-interruption-checks-' + tag + '-sb16.tar.gz')
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments: tar.add(root / name, arcname=prefix + '/' + name, recursive=False)
    tar.add(root / 'collected-review.json', arcname=prefix + '/collected-review.json', recursive=False)
    tar.add(root / 'source/tests/native/test_approval_interruption.py', arcname=prefix + '/test_approval_interruption.py', recursive=False)
print(json.dumps(dict(status=summary['status'], archive=str(archive), sha256=sha(archive),
    selected_cases_per_profile=summary['selected_cases_per_profile'], attachments=len(attachments))))
