"""Preserve the diagnostic baseline and terminal focused checks without changing raw logs."""
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
root = base / ('m02-participant-diagnostics-' + tag)
run = json.loads((root / 'checks/review.json').read_bytes())
assert 'ended' in run
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert len(run['source']) == 344 and len(run['locked']) == 41
for group in ('source', 'locked'):
    assert all(sha(root / 'source' / name) == digest for name, digest in run[group].items())
counts = {}
if tag == '01':
    assert run['status'] == 'missing_diagnostics_reproduced' and run['exit_code'] == 1
    assert sha(root / 'checks/baseline.log') == run['log_sha256']
    text = (root / 'checks/baseline.log').read_text()
    assert 'Ran 2 tests' in text and 'FAILED (failures=2)' in text
    prior_root = Path(run['reused_builds'])
    assert sha(prior_root / 'checks/review.json') == run['build_provenance_sha256']
else:
    assert run['status'] in ('passed', 'failed')
    assert sha(Path(run['server']['path'])) == run['server']['sha256']
    for profile, steps in run['profiles'].items():
        if 'binaries' in steps:
            assert all(sha(root / profile / 'build' / name) == digest for name, digest in steps['binaries'].items())
        for label in ('configure', 'build', 'focused', 'startup-repeat'):
            if label in steps and 'log_sha256' in steps[label]:
                assert sha(root / 'checks' / profile / (label + '.log')) == steps[label]['log_sha256']
        if 'repeat_script_sha256' in steps:
            assert sha(base / 'repeat-participant-startup-02.py') == steps['repeat_script_sha256']
        if run['status'] != 'passed': continue
        assert all(steps[label]['exit_code'] == 0 for label in ('configure', 'build', 'focused', 'startup-repeat'))
        counts[profile] = {}
        for label in ('focused', 'startup-repeat'):
            text = (root / 'checks' / profile / (label + '.log')).read_text()
            assert not any(marker in text for marker in ('... skipped', 'skipped=', 'FAILED (', 'runtime error:',
                'ERROR: AddressSanitizer', 'ERROR: LeakSanitizer', 'AddressSanitizer:DEADLYSIGNAL'))
            pattern = r'^\d+: Ran (\d+) tests? in ' if label == 'focused' else r'^Ran (\d+) tests? in '
            cases = list(map(int, re.findall(pattern, text, re.M)))
            if label == 'focused':
                assert len(cases) == 4 and '100% tests passed, 0 tests failed out of 4' in text
            else: assert cases == [10] and '\nOK\n' in text
            counts[profile][label] = cases
        cache = (root / profile / 'build/CMakeCache.txt').read_text()
        assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in cache
    if run['status'] == 'passed': assert counts['debug'] == counts['runtime-sanitize']
attachments = {}
for path in (root / 'checks').rglob('*'):
    if path.is_file() and (path.suffix == '.log' or path.name == 'review.json'):
        data = path.read_bytes()
        assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
        attachments[path.relative_to(root).as_posix()] = sha(path)
summary = dict(status=run['status'], host='sb16', profiles=counts, attachments=attachments,
    source_files=344, locked_files=41,
    selected_cases_per_profile=sum(counts.get('debug', {}).get('focused', [])),
    startup_repetitions_per_profile=sum(counts.get('debug', {}).get('startup-repeat', [])),
    historical_startup_cause='unconfirmed', full_phase_gate='pending on both hosts')
with (root / 'collected-review.json').open('x') as stream:
    json.dump(summary, stream, indent=2); stream.write('\n')
prefix = 'participant-diagnostics-' + tag + '/sb16'
archive = base / ('participant-diagnostics-checks-' + tag + '-sb16.tar.gz')
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments: tar.add(root / name, arcname=prefix + '/' + name, recursive=False)
    tar.add(root / 'collected-review.json', arcname=prefix + '/collected-review.json', recursive=False)
    if tag == '01':
        tar.add(root / 'source/tests/core/test_identity_participant_runtime.py',
            arcname=prefix + '/test_identity_participant_runtime.py', recursive=False)
print(json.dumps(dict(status=summary['status'], archive=str(archive), sha256=sha(archive),
    selected_cases_per_profile=summary['selected_cases_per_profile'],
    startup_repetitions_per_profile=summary['startup_repetitions_per_profile'], attachments=len(attachments))))
