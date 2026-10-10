"""Preserve the missing coordinator operation after actual durable native cleanup."""
import hashlib
import json
from pathlib import Path
import socket
import tarfile

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
root = r / 'local/m02-broker-retirement-baseline-01'
review = json.loads((root / 'review.json').read_bytes())
assert review['status'] == 'missing_broker_retirement_reproduced'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
test = r / 'local/test-broker-retirement-baseline-01.py'
assert sha(test) == review['test_sha256']
assert sha(root / 'baseline.log') == review['log_sha256']
with (root / 'input.py').open('xb') as stream: stream.write(test.read_bytes())
attachments = {}
for path in root.rglob('*'):
    if not path.is_file() or path.suffix not in ('.log', '.json', '.py'): continue
    data = path.read_bytes()
    assert b'-----BEGIN PRIVATE KEY-----' not in data and b'Bearer fleet-' not in data
    attachments[path.relative_to(root).as_posix()] = sha(path)
summary = dict(status='expected_missing_step_failure', command=review['command'], host='sb16',
    source_files=len(review['source']), attachments=attachments,
    finding='The actual broker and its confined application processes have checked process/native cleanup and a committed issuer fence. The retained coordinator delivers the cleanup. Its issuer generation remains active, with no retirement. The proposed private retirement operation is missing.',
    limitations='One baseline case. This is failure evidence, not a qualification pass.')
with (root / 'baseline-review.json').open('x') as stream: json.dump(summary, stream, indent=2); stream.write('\n')
archive = r / 'local/broker-retirement-baseline-01-sb16.tar.gz'
with tarfile.open(archive, 'x:gz') as tar:
    for name in list(attachments) + ['baseline-review.json']:
        tar.add(root / name, arcname='broker-retirement-baseline01/sb16/' + name, recursive=False)
print(json.dumps(dict(archive=str(archive), sha256=sha(archive), attachments=len(attachments))))
