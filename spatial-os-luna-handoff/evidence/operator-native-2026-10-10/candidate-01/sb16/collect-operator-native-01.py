"""Review actual command receipts and native observations before evidence export."""
import hashlib
import json
from pathlib import Path
import re
import socket
import sys
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / 'm02-operator-native-01'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
manifest = json.loads((root / 'manifest.json').read_bytes())
assert len(manifest['files']) == 351 and len(manifest['locked_files']) == 41
assert all(sha(root / 'source' / name) == digest for name, digest in manifest['files'].items())
assert all(manifest['files'][name] == digest for name, digest in manifest['locked_files'].items())
sys.path.insert(0, str(root / 'debug/build/python'))
import core_pb2 as core
import issuer_pb2 as identity
import launch_pb2 as launch
names = ['test_completion_waits_for_real_key_button_and_lease_release',
    'test_pending_timeout_resumes_without_another_revocation',
    'test_crash_requires_checked_recovery_and_retirement_before_completion']
profiles = {}
attachments = {}

def add(path):
    assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(base)
    assert path.suffix in ('.json', '.log', '.pb', '.frame', '.txt', '.py')
    data = path.read_bytes()
    assert b'-----BEGIN ' + b'PRIVATE KEY-----' not in data
    assert not re.search(rb'Bearer ' + rb'fleet-[A-Za-z0-9_-]{12,}', data)
    attachments[path.relative_to(base).as_posix()] = sha(path)

for profile in ('debug', 'runtime-sanitize'):
    out = root / 'checks' / profile
    record = json.loads((out / 'review.json').read_bytes())
    assert record['status'] == 'passed' and record['cases'] == 3
    build = root / profile / 'build'
    assert all(sha(build / name) == digest for name, digest in record['binaries'].items())
    assert sha(Path(record['server']['path'])) == record['server']['sha256']
    assert record['source'] == manifest['files']
    assert 'SPATIAL_SANITIZERS:BOOL=' + ('ON' if profile == 'runtime-sanitize' else 'OFF') in (build / 'CMakeCache.txt').read_text()
    for command in record['commands']:
        assert command['exit_code'] == 0 and sha(out / (command['label'] + '.log')) == command['sha256']
    text = (out / 'native.log').read_text()
    assert len(re.findall(r'^test_.* \.\.\. ok$', text, re.M)) == 3
    assert '\nOK\n' in text and 'skipped=' not in text
    rows = {}
    for name in names:
        where = out / 'native' / name
        load = lambda filename, kind: kind.FromString((where / filename).read_bytes())
        held = load('barrier-held.pb', identity.AuthorityBarrierStatus)
        assert held.participant_count == 2 and held.pending_count > 0 and not held.complete
        before = json.loads((where / 'native-held.json').read_bytes())
        after = json.loads((where / 'native-released.json').read_bytes())
        for event in ('key', 'button'):
            assert any(row.get('event') == event and row.get('pressed') for row in before)
            assert not any(row.get('event') == event and not row.get('pressed') for row in before)
            assert any(row.get('event') == event and not row.get('pressed') for row in after)
        assert any(row.get('event') == 'ack' for row in after)
        index = int(name == names[1])
        result = load(f'command-{index}-reply.pb', identity.RevocationResult)
        assert result.state == identity.REVOCATION_COMPLETE and result.revision == 1
        assert result.barrier.target == held.target and result.barrier.issuer_id == held.issuer_id
        assert result.barrier.complete and result.barrier.pending_count == 0
        assert (where / f'command-{index}-exit.txt').read_text().strip() == '0'
        retired = [item for item in result.barrier.obligations if item.HasField('retirement')]
        assert len(retired) == int(name == names[2])
        assert all(item.applied_revision == (0 if item.HasField('retirement') else 1) for item in result.barrier.obligations)
        if name == names[1]:
            pending = load('command-0-reply.pb', identity.RevocationResult)
            assert pending.state == identity.REVOCATION_COMMITTED and pending.error.code == core.TIMEOUT
            assert not pending.barrier.complete and pending.barrier.pending_count > 0
            request = load('command-1-request.pb', identity.RevocationCommand)
            assert request.HasField('resume') and request.resume.revision == 1 and request.resume.target == held.target
        if retired:
            receipt = load('world-retirement.pb', identity.AuthorityRetirementState)
            cleanup = load('crashed-world-cleanup.pb', launch.AuthorityLaunchRecord)
            assert retired[0].retirement == receipt and receipt.applied_revision == 0
            assert receipt.request.fence_id == cleanup.fence.fence_id
            assert cleanup.stage == launch.OWNED_SCOPES_STOPPED
        rows[name] = dict(pending_observed=True, native_released=True, application_acknowledged=True,
            completed_revision=1, retired=len(retired))
    profiles[profile] = dict(cases=3, results=rows, binaries=record['binaries'], server=record['server'])

add(root / 'manifest.json')
for name in manifest['changed']: add(root / 'source' / name)
for path in (root / 'checks').rglob('*'):
    if path.is_file(): add(path)
for name in ('check-operator-native-01.py', 'collect-operator-native-01.py'): add(base / name)
review = dict(status='native_focused_passed_phase_gate_pending', host='sb16', source_files=351,
    locked_files_unchanged=41, profiles=profiles, attachments=attachments,
    production_changed=False, existing_assertions_changed=False,
    limitations=['Full M02 qualification remains pending on both hosts.',
        'The two previously preserved intermittent failures remain unresolved.',
        'This is a software native XLibre session, not GPU, physical-device, or performance qualification.'])
review_path = root / 'primary-review.json'
with review_path.open('xb') as stream: stream.write((json.dumps(review, indent=2) + '\n').encode())
archive = base / 'operator-native-evidence-01.tar'
with tarfile.open(archive, 'x') as tar:
    for name in sorted(attachments): tar.add(base / name, arcname='sb16/' + name, recursive=False)
    tar.add(review_path, arcname='primary-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive), attachments=len(attachments), profiles=2, cases_per_profile=3)))
