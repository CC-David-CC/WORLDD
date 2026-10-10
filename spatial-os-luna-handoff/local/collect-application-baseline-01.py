"""Preserve the remote-input restriction and initial fixture evidence failure."""
import hashlib
import json
from pathlib import Path
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
root = r / 'local/m02-connected-applications-01'
run = json.loads((root / 'checks/review.json').read_bytes())
assert run['status'] == 'failed'
assert run['profiles']['debug']['build']['exit_code'] == 0
assert run['profiles']['debug']['native-application']['exit_code'] == 8
assert 'runtime-sanitize' not in run['profiles'] and 'focused' not in run['profiles']['debug']
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
assert all(sha(root / 'source' / n) == h for n, h in run['source'].items())
assert all(sha(root / 'debug/build' / n) == h for n, h in run['profiles']['debug']['binaries'].items())
text = (root / 'checks/debug/native-application.log').read_text()
assert 'FAILED (failures=2, errors=1)' in text
assert text.count('message: "Remote application input is not supported"') == 2
assert 'FileNotFoundError' in text and 'probe.jsonl.log' in text
previous = json.loads((r / 'local/m02-connected-actors-01/checks/review.json').read_bytes())
runtime = {n: h for n, h in previous['source'].items() if n.startswith(('src/', 'proto/')) and not n.startswith('src/testing/')}
assert all(run['source'][n] == h for n, h in runtime.items())
files = {p.relative_to(root).as_posix(): sha(p) for p in (root / 'checks').rglob('*')
    if p.is_file() and (p.suffix == '.log' or p.name == 'review.json')}
review = dict(status='remote_input_restriction_confirmed_local_transport_required', host='sb16',
    debug_cases=2, failures=2, fixture_cleanup_errors=1, related_checks='unrun', sanitizers='unrun',
    unchanged_runtime_sources=len(runtime), attachments=files,
    finding='The real actor launched a confined native application. TLS actors correctly cannot control native application input in M1.',
    next_action='Create local actor endpoints inside SessionCoordinator. Preserve remote-input rejection.',
    missing_evidence='The first application raw log was not retained because its destination directory was not yet created. The failure and second application log remain available.')
target = root / 'baseline-review.json'
with target.open('x') as stream: json.dump(review, stream, indent=2); stream.write('\n')
archive = r / 'local/connected-application-baseline-01-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in files: tar.add(root / name, arcname='connected-application-baseline01/sb16/' + name, recursive=False)
    tar.add(target, arcname='connected-application-baseline01/sb16/baseline-review.json', recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive), attachments=len(files))))
