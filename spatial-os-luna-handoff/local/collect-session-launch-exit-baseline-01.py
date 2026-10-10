"""Preserve the independently reproduced supervisor-exit retry failure."""
import hashlib
import json
from pathlib import Path
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
root = r / 'local/m02-session-launch-exit-baseline-01'
run = json.loads((root / 'review.json').read_bytes())
assert run['status'] == 'rejected_exit_status_forgotten_on_retry'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(root / 'baseline.log') == run['log_sha256']
attachments = {name: sha(root / name) for name in ('review.json', 'build.log', 'baseline.log', 'input.cpp', 'input.py')}
review = dict(status='expected_exit_status_failure', passed=0, failed=1, attachments=attachments,
    finding='A final receipt paired with exit status 7 was rejected once, then accepted on retry because wait_child returned a default zero status after reaping.',
    scope='Independent lifecycle fault peer linked against unchanged production SessionLaunch. This is protocol and exit-validation evidence, not native cleanup evidence.')
with (root / 'baseline-review.json').open('x') as stream: json.dump(review, stream, indent=2); stream.write('\n')
archive = r / 'local/session-launch-exit-baseline-01-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in list(attachments) + ['baseline-review.json']:
        tar.add(root / name, arcname='session-launch-exit-baseline01/sb16/' + name, recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive), attachments=len(attachments))))
