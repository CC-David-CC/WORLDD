"""Preserve the failed assumptions and confirmed production defect without private fixture files."""
import hashlib
import json
from pathlib import Path
import socket
import sys
import tarfile
assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
prior = base / 'm02-crosshost-cancellation-01'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
compiled = json.loads((prior / 'checks/debug/build-review.json').read_bytes())
assert all(sha(prior / 'source' / name) == digest for name,digest in compiled['source'].items())
assert all(sha(prior / 'debug/build' / name) == digest for name,digest in compiled['binaries'].items())
assert sha(Path(compiled['server']['path'])) == compiled['server']['sha256']
sys.path.insert(0, str(prior / 'debug/build/python'))
import issuer_pb2 as identity
import launch_pb2 as launch
import session_probe_pb2 as probe
attachments = {}

def add(path):
    assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(base)
    assert path.suffix in ('.json', '.log', '.pb', '.py')
    data = path.read_bytes()
    assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
    attachments[path.relative_to(base).as_posix()] = sha(path)

for tag in ('01', '02', '03'):
    root = base / ('m02-issuer-loss-baseline-' + tag)
    review = json.loads((root / 'review.json').read_bytes())
    assert review['exit_code'] == 1 and review['ended'] > review['started']
    assert sha(root / 'baseline.log') == review['log_sha256']
    assert sha(root / ('test-issuer-loss-baseline-' + tag + '.py')) == review['test_sha256']
    assert sha(prior / 'checks/debug/build-review.json') == review['build_review_sha256']
    if tag != '03':
        assert review['status'] == 'failed' and 'self.exited()' in (root / 'baseline.log').read_text()
    else:
        assert review['status'] == 'missing_deferred_fence_reproduced'
        witness = json.loads((root / 'witness/witness.json').read_bytes())
        assert witness['checked_world_cleanup'] and witness['checked_broker_cleanup']
        assert not witness['world_fence'] and not witness['broker_fence']
        assert witness['pending_survived_restart'] and witness['project_data_preserved']
        assert witness['independent_native_credential_survived'] and witness['final_pending'] == 2
        barriers = [identity.AuthorityBarrierStatus.FromString((root / ('witness/' + name + '.pb')).read_bytes())
            for name in ('pending-before', 'pending-after-restart', 'final-barrier')]
        assert barriers[0] == barriers[1] == barriers[2]
        for who in ('world', 'broker'):
            cleanup = launch.AuthorityLaunchRecord.FromString((root / ('witness/' + who + '-cleanup.pb')).read_bytes())
            assert cleanup.stage == launch.OWNED_SCOPES_STOPPED and not cleanup.HasField('fence')
            assert cleanup.process == cleanup.process_stopped.identity and cleanup.native == cleanup.native_stopped.identity
            reply = probe.SessionProbeReply.FromString((root / ('witness/' + who + '-retirement-reply.pb')).read_bytes())
            assert reply.HasField('error') and 'issuer fence' in reply.error.message
    for path in root.rglob('*'):
        if path.is_file(): add(path)
    add(base / ('check-issuer-loss-baseline-' + tag + '.py'))
add(prior / 'checks/debug/build-review.json')
review = dict(status='confirmed_missing_deferred_fence', host='sb16', profile='debug',
    original_failed_fixture_candidates=['01', '02'], confirmed_candidate='03',
    production_source_files=len(compiled['source']), locked_files=len(compiled['locked']), attachments=attachments)
review_path = base / 'issuer-loss-baseline-03-review.json'
with review_path.open('x') as stream: json.dump(review, stream, indent=2); stream.write('\n')
archive = base / 'issuer-loss-baseline-03-evidence.tar'
with tarfile.open(archive, 'x') as tar:
    for name in attachments: tar.add(base / name, arcname=name, recursive=False)
    tar.add(review_path, arcname=review_path.name, recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive), attachments=len(attachments))))
