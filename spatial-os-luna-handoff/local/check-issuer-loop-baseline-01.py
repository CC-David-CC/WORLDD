"""Demonstrate the missing diagnostic against the preserved pre-change binary."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess

assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / 'm02-issuer-loop-baseline-01'
root.mkdir(exist_ok=False)
original = base / 'm02-operator-native-01'
build = original / 'debug/build'
source = original / 'source'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
old = json.loads((original / 'checks/debug/review.json').read_bytes())
assert old['status'] == 'passed'
assert all(sha(build / name) == digest for name, digest in old['binaries'].items())
assert all(sha(source / name) == digest for name, digest in old['source'].items())
test = root / 'test_identity_tls.py'
test.write_bytes((base / 'issuer-loop-test-01.py').read_bytes())
env = dict(os.environ, PYTHONPATH=str(build / 'python') + ':' + str(source) + ':' + str(source / 'tests/core'))
command = ['python3', str(test), str(build / 'identityd'), 'TlsTests.test_server_records_long_loop_delay_without_changing_protocol']
with (root / 'baseline.log').open('xb') as log:
    result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=40)
text = (root / 'baseline.log').read_text()
assert result.returncode == 1 and 'Ran 1 test' in text and 'FAILED (failures=1)' in text, text
assert 'The issuer did not retain the injected event-loop delay' in text
assert all(sha(build / name) == digest for name, digest in old['binaries'].items())
record = dict(status='missing_loop_diagnostic_reproduced', command=command, environment={'PYTHONPATH':env['PYTHONPATH']},
    source=old['source'], binaries=old['binaries'], exit_code=result.returncode, expected_failures=1,
    historical_timeout_reproduced=False, deadline_changed=False, test_sha256=sha(test),
    log_sha256=sha(root / 'baseline.log'))
(root / 'review.json').write_bytes((json.dumps(record, indent=2) + '\n').encode())
print(record['status'], record['expected_failures'], flush=True)
