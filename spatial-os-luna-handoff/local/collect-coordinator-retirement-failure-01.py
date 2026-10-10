"""Keep the first integration failure and its exact source context."""
import hashlib
import json
from pathlib import Path
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
root = r / 'local/m02-coordinator-retirement-01'
run = json.loads((root / 'checks/review.json').read_bytes())
assert run['status'] == 'failed'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
for group in ('source', 'locked'):
    assert all(sha(root / 'source' / n) == h for n, h in run[group].items())
text = (root / 'checks/debug/retirement.log').read_text()
assert 'Ran 8 tests' in text and 'FAILED (failures=7)' in text
assert 'Issuer client process must ignore SIGPIPE' in text
assert run['profiles']['debug']['build']['exit_code'] == 0
assert run['profiles']['debug']['retirement']['exit_code'] == 8
attachments = {}
for path in (root / 'checks').rglob('*'):
    if not path.is_file() or path.suffix not in ('.log', '.json'): continue
    data = path.read_bytes()
    assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
    attachments[path.relative_to(root).as_posix()] = sha(path)
for name in ('src/testing/session_application_probe.cpp', 'src/supervisor/session_coordinator.hpp'):
    attachments['source/' + name] = sha(root / 'source' / name)
review = dict(status='preserved_integration_failure', passed=1, failed=7, skipped=0,
    unrun=['related Debug suites', 'runtime sanitizer build and checks'], attachments=attachments,
    finding='The test driver did not install the SIGPIPE disposition required by the production IssuerClient. Requests failed before issuer proof. The unfenced rejection passed.',
    correction='Candidate02 installs SIG_IGN in the test process entry point and documents the transport requirement on SessionCoordinator. Assertions and production retirement logic stay unchanged.')
with (root / 'failure-review.json').open('x') as stream: json.dump(review, stream, indent=2); stream.write('\n')
archive = r / 'local/coordinator-retirement-failure-01-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in list(attachments) + ['failure-review.json']:
        tar.add(root / name, arcname='coordinator-retirement-failure01/sb16/' + name, recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive), attachments=len(attachments))))
