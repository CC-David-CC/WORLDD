"""Collect replacement checks that reuse verified, unchanged compiled inputs."""
import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / 'm02-session-replacement-03'
run = json.loads((root / 'checks/review.json').read_bytes())
assert run['status'] in ('passed', 'failed') and 'ended' in run
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
prior_root = Path(run['reused_builds'])
assert prior_root == base / 'm02-session-replacement-02'
assert sha(prior_root / 'checks/review.json') == run['build_provenance_sha256']
prior = json.loads((prior_root / 'checks/review.json').read_bytes())
assert {name for name in run['source'] if run['source'][name] != prior['source'].get(name)} == {'tests/native/test_session_replacement.py'}
assert len(run['source']) == 343 and len(run['locked']) == 41
for group in ('source', 'locked'):
    assert all(sha(root / 'source' / name) == digest for name, digest in run[group].items())
assert all(sha(prior_root / 'source' / name) == digest for name, digest in prior['source'].items())
assert sha(Path(run['server']['path'])) == run['server']['sha256']
counts = {}
for profile, steps in run['profiles'].items():
    reuse = steps['reused_build']; build = Path(reuse['path'])
    assert build == prior_root / profile / 'build'
    assert reuse['binaries'] == prior['profiles'][profile]['binaries']
    assert all(sha(build / name) == digest for name, digest in reuse['binaries'].items())
    assert sha(build / 'CMakeCache.txt') == reuse['cache_sha256']
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in (build / 'CMakeCache.txt').read_text()
    assert sha(root / 'checks' / profile / 'ctest/CTestTestfile.cmake') == steps['ctest_file_sha256']
    for name in ('replacement', 'focused'):
        if name in steps and 'log_sha256' in steps[name]:
            assert sha(root / 'checks' / profile / (name + '.log')) == steps[name]['log_sha256']
    if run['status'] != 'passed': continue
    counts[profile] = {}
    for name in ('replacement', 'focused'):
        assert steps[name]['exit_code'] == 0
        text = (root / 'checks' / profile / (name + '.log')).read_text()
        assert not any(marker in text for marker in ('... skipped', 'skipped=', 'FAILED (', 'runtime error:',
            'ERROR: AddressSanitizer', 'ERROR: LeakSanitizer', 'AddressSanitizer:DEADLYSIGNAL'))
        cases = list(map(int, re.findall(r'^\d+: Ran (\d+) tests? in ', text, re.M)))
        assert len(cases) == (1 if name == 'replacement' else 7), cases
        if name == 'replacement': assert cases == [3]
        assert f'100% tests passed, 0 tests failed out of {len(cases)}' in text
        counts[profile][name] = cases
if run['status'] == 'passed': assert counts['debug'] == counts['runtime-sanitize']
attachments = {}
for path in (root / 'checks').rglob('*'):
    if path.is_file() and (path.suffix in ('.log', '.cmake') or path.name == 'review.json'):
        data = path.read_bytes()
        assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
        attachments[path.relative_to(root).as_posix()] = sha(path)
summary = dict(status='focused_passed_phase_gate_pending' if run['status'] == 'passed' else 'preserved_failure',
    host='sb16', source_files=343, locked_files=41, profiles=counts, attachments=attachments,
    selected_cases_per_profile=sum(sum(cases) for cases in counts.get('debug', {}).values()),
    production_launch_changed=False, compiled_input_changes_from_candidate02=0,
    reused_build_provenance=run['build_provenance_sha256'],
    limitations=['Full M02 qualification remains pending on both hosts.',
        'Broker replacement, failure races and cross-host cancellation remain required.',
        'The earlier participant-approval startup failure remains unresolved.',
        'Automatic application placement restoration and shared compositor lifetime remain required for M1.'])
with (root / 'collected-review.json').open('x') as stream:
    json.dump(summary, stream, indent=2); stream.write('\n')
prefix = 'session-replacement-03/sb16' if run['status'] == 'passed' else 'session-replacement-failure03/sb16'
archive = base / 'session-replacement-checks-03-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in attachments: tar.add(root / name, arcname=prefix + '/' + name, recursive=False)
    tar.add(root / 'collected-review.json', arcname=prefix + '/collected-review.json', recursive=False)
print(json.dumps(dict(status=summary['status'], archive=str(archive), sha256=sha(archive),
    selected_cases_per_profile=summary['selected_cases_per_profile'], attachments=len(attachments))))
