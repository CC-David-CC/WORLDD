"""Demonstrate the missing diagnostic against the preserved pre-change binary."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess

assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / 'm02-issuer-timeout-baseline-01'
root.mkdir(exist_ok=False)
original = base / 'm02-operator-native-01'
build = original / 'debug/build'
source = original / 'source'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
old = json.loads((original / 'checks/debug/review.json').read_bytes())
assert old['status'] == 'passed'
assert all(sha(build / name) == digest for name, digest in old['binaries'].items())
assert all(sha(source / name) == digest for name, digest in old['source'].items())
test = root / 'test_issuer_client.py'
test.write_bytes((base / 'issuer-timeout-test-01.py').read_bytes())
env = dict(os.environ, PYTHONPATH=str(build / 'python') + ':' + str(source) + ':' + str(source / 'tests/core'))
command = ['python3', str(test), str(build / 'identityd'), str(build / 'spatial-issuer-client'),
    'ClientTests.test_stalled_reply_header_reports_phase_and_partial_progress',
    'ClientTests.test_stalled_reply_payload_reports_phase_and_partial_progress',
    'ClientTests.test_tcp_handshake_timeout_is_bounded']
with (root / 'baseline.log').open('xb') as log:
    result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=40)
text = (root / 'baseline.log').read_text()
assert result.returncode == 1 and 'Ran 3 tests' in text and 'FAILED (failures=3)' in text, text
assert 'Issuer client deadline exceeded' in text and 'phase=reply_header' in text and 'phase=reply_payload' in text
assert all(sha(build / name) == digest for name, digest in old['binaries'].items())
record = dict(status='missing_diagnostic_reproduced', command=command, environment={'PYTHONPATH':env['PYTHONPATH']},
    source=old['source'], binaries=old['binaries'], exit_code=result.returncode, expected_failures=3,
    historical_timeout_reproduced=False, deadline_changed=False, test_sha256=sha(test),
    log_sha256=sha(root / 'baseline.log'))
(root / 'review.json').write_bytes((json.dumps(record, indent=2) + '\n').encode())
print(record['status'], record['expected_failures'], flush=True)
