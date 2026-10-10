"""Check selected results and original cleanup digests before evidence export."""
import hashlib
import json
from pathlib import Path
import re
import socket
import sys
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / 'm02-deferred-fence-01'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
manifest = json.loads((root / 'manifest.json').read_bytes())
assert len(manifest['files']) == 348 and len(manifest['locked_files']) == 41
assert all(sha(root / 'source' / name) == digest for name,digest in manifest['files'].items())
assert all(manifest['files'][name] == digest for name,digest in manifest['locked_files'].items())
sys.path.insert(0, str(root / 'debug/build/python'))
import issuer_pb2 as identity
import launch_pb2 as launch
attachments = {}

def add(path):
    assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(base)
    assert path.suffix in ('.json', '.log', '.pb', '.py', '.cpp', '.hpp', '.txt')
    data = path.read_bytes()
    assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
    attachments[path.relative_to(root).as_posix()] = sha(path)

profiles = {}
for profile in ('debug', 'runtime-sanitize'):
    out = root / 'checks' / profile; build = root / profile / 'build'
    compiled = json.loads((out / 'build-review.json').read_bytes())
    assert compiled['status'] == 'built_tests_unrun' and compiled['source'] == manifest['files']
    assert all(sha(build / name) == digest for name,digest in compiled['binaries'].items())
    assert sha(Path(compiled['server']['path'])) == compiled['server']['sha256']
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in (build / 'CMakeCache.txt').read_text()
    tests = json.loads((out / 'test-review.json').read_bytes())
    assert len(tests['cases']) == 6
    qualified = [(case, out) for case in tests['cases']]
    if profile == 'debug':
        assert tests['status'] == 'failed' and tests['cases'][-1]['name'] == 'authority-launch'
        failed = tests['cases'][-1]
        assert failed['exit_code'] == 1 and failed['count'] == 14
        failure = out / 'authority-launch.log'; assert sha(failure) == failed['log_sha256']
        assert 'FAILED (errors=14)' in failure.read_text() and "PermissionError: [Errno 13] Permission denied:" in failure.read_text()
        retry = json.loads((out / 'authority-retry/test-review.json').read_bytes())
        assert retry['status'] == 'passed' and len(retry['cases']) == 1
        qualified = qualified[:-1] + [(retry['cases'][0], out / 'authority-retry')]
    else: assert tests['status'] == 'passed'
    for case, logroot in qualified:
        assert case['status'] == 'passed' and case['exit_code'] == 0 and case['count'] > 0
        log = logroot / (case['name'] + '.log'); assert sha(log) == case['log_sha256']
        assert len(re.findall(r'^test_.* \.\.\. ok$', log.read_text(), re.M)) == case['count']
    total = sum(case['count'] for case, logroot in qualified)
    assert total == 49
    scenarios = sorted((out / 'deferred-fence-native').glob('test_*'))
    scenarios = [path for path in scenarios if path.is_dir()]
    assert len(scenarios) == 5
    for scenario in scenarios:
        read = lambda name, kind: kind.FromString((scenario / (name + '.pb')).read_bytes())
        before = read('pending-before', identity.AuthorityBarrierStatus)
        restarted = read('pending-after-restart', identity.AuthorityBarrierStatus)
        final = read('final-barrier', identity.AuthorityBarrierStatus)
        assert before == restarted and before.pending_count == 2 and not before.complete
        assert final.complete and final.pending_count == 0 and final.participant_count == 2
        assert all(row.applied_revision == 0 and row.HasField('retirement') for row in final.obligations)
        dependent = (scenario / 'dependent-cleanup.pb').read_bytes()
        proof = launch.AuthorityDependentCleanup.FromString(dependent)
        for who in ('world', 'broker'):
            initial = read(who + '-before', launch.AuthorityLaunchRecord)
            cleanup = read(who + '-cleanup', launch.AuthorityLaunchRecord)
            fence = read(who + '-fence', identity.AuthorityFenceState)
            retired = read(who + '-retirement', identity.AuthorityRetirementState)
            assert cleanup.stage == launch.OWNED_SCOPES_STOPPED and not cleanup.HasField('fence')
            assert cleanup.process_stopped.identity == initial.process and cleanup.native_stopped.identity == initial.native
            assert getattr(proof, who) == cleanup and fence.launch_id == cleanup.process.launch_id
            encoded = dependent if who == 'world' else (scenario / 'broker-cleanup.pb').read_bytes()
            assert retired.request.cleanup_sha256 == hashlib.sha256(encoded).digest()
            assert retired.request.fence_id == fence.fence_id and retired.applied_revision == 0
            row = next(row for row in final.obligations if row.registration.participant.id == cleanup.binding.participant_id)
            assert row.retirement == retired
    for path in out.rglob('*'):
        if path.is_file(): add(path)
    profiles[profile] = dict(passed=total, failed=0, skipped=0, new_cases=5, suites=6)
add(root / 'manifest.json')
for name in manifest['changed_from_qualified']: add(root / 'source' / name)
review = dict(status='focused_passed', host='sb16', profiles=profiles, source=manifest['files'],
    locked=manifest['locked_files'], attachments=attachments, full_M02_gate='pending on both hosts')
review_path = root / 'collected-review.json'
with review_path.open('x') as stream: json.dump(review, stream, indent=2); stream.write('\n')
archive = base / 'deferred-fence-01-evidence.tar'
with tarfile.open(archive, 'x') as tar:
    for name in attachments: tar.add(root / name, arcname=name, recursive=False)
    tar.add(review_path, arcname='collected-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive), attachments=len(attachments), profiles=profiles)))
