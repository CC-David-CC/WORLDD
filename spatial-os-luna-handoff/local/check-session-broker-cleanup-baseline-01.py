"""Run the broker crash baseline after the coordinator retirement check job ends."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
root = r / 'local/m02-coordinator-retirement-02'
prior = json.loads((root / 'checks/review.json').read_bytes())
assert prior['status'] == 'passed'
source = root / 'source'; build = root / 'debug/build'
out = r / 'local/m02-session-broker-cleanup-baseline-01'; out.mkdir(exist_ok=False)
test = r / 'local/test-session-broker-cleanup-baseline-01.py'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try: name = (p / 'comm').read_text().strip()
    except (PermissionError, FileNotFoundError, ProcessLookupError): continue
    assert name not in ('ctest', 'cc1', 'cc1plus', 'clang', 'ninja'), (p, name)
def audit():
    assert all(sha(source / n) == h for n, h in prior['source'].items())
    assert all(sha(build / n) == h for n, h in prior['profiles']['debug']['binaries'].items())
    assert sha(Path(prior['server']['path'])) == prior['server']['sha256']
audit()
command = ['systemd-run', '--user', '--wait', '--pipe', '--collect', '--property=Delegate=yes',
    '--unit=spatial-session-broker-cleanup-baseline-01',
    '--setenv=PYTHONPATH=' + ':'.join(map(str, [build / 'python', source, source / 'tests/native'])),
    '--setenv=SPATIAL_TEST_XSERVER=' + prior['server']['path'],
    '--setenv=SPATIAL_SECURITY_EVIDENCE=' + str(out / 'native'),
    'python3', str(test), str(source), str(build / 'spatial-authority-launch-probe'),
    str(build / 'spatial-security-auth'), str(build / 'identityd'), str(build / 'spatial-issuer-client')]
record = dict(status='running', command=command, test_sha256=sha(test), source=prior['source'],
    binaries=prior['profiles']['debug']['binaries'], server=prior['server'], started=time.time())
save = lambda: (out / 'review.json').write_text(json.dumps(record, indent=2) + '\n')
save()
with (out / 'baseline.log').open('xb') as log:
    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
text = (out / 'baseline.log').read_text()
record.update(exit_code=result.returncode, ended=time.time(), log_sha256=sha(out / 'baseline.log'), status='unexpected_result')
save()
assert result.returncode == 1 and 'Ran 1 test' in text and 'FAILED (failures=1)' in text, text[-6000:]
assert 'Session discarded checked broker cleanup after its supervisor exited' in text, text[-6000:]
audit(); record['status'] = 'missing_broker_final_cleanup_delivery_reproduced'; save()
print(json.dumps(dict(status=record['status'], exit_code=result.returncode, log=str(out / 'baseline.log'))))
