"""Retain one expected missing-operation failure with exact production source pins."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

assert socket.gethostname().split('.')[0] == 'sb16'
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
prior_root = r / 'local/m02-dependent-cleanup-01'
prior = json.loads((prior_root / 'checks/review.json').read_bytes())
assert prior['status'] == 'passed'
out = r / 'local/m02-retirement-baseline-01'; out.mkdir(exist_ok=False)
source = prior_root / 'source'; build = prior_root / 'debug/build'
test = r / 'local/test-retirement-baseline-01.py'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try: name = (p / 'comm').read_text().strip()
    except (PermissionError, FileNotFoundError, ProcessLookupError): continue
    assert name not in ('ctest', 'cc1', 'cc1plus', 'clang', 'ninja'), (p, name)
def audit():
    assert all(sha(source / n) == h for n, h in prior['source'].items())
    assert all(sha(build / n) == h for n, h in prior['profiles']['debug']['binaries'].items())
audit()
command = ['python3', str(test), str(build / 'identityd'), str(build / 'spatial-issuer-client')]
env = dict(os.environ, PYTHONPATH=':'.join(map(str, [build / 'python', source, source / 'tests/core'])))
record = dict(status='running', command=command, test_sha256=sha(test), source=prior['source'],
    binaries=prior['profiles']['debug']['binaries'], started=time.time(),
    scope='Issuer protocol only. The proposed digest is fixture data, not external cleanup evidence.')
save = lambda: (out / 'review.json').write_text(json.dumps(record, indent=2) + '\n')
save()
with (out / 'baseline.log').open('xb') as log:
    result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
text = (out / 'baseline.log').read_text()
record.update(exit_code=result.returncode, ended=time.time(), log_sha256=sha(out / 'baseline.log'), status='unexpected_result')
save()
assert result.returncode == 1 and 'Ran 1 test' in text and 'FAILED (failures=1)' in text, text[-6000:]
assert 'Retirement challenge not implemented' in text and 'INVALID_ARGUMENT' in text, text[-6000:]
audit(); record['status'] = 'missing_retirement_reproduced'; save()
print(json.dumps(dict(status=record['status'], exit_code=result.returncode, log=str(out / 'baseline.log'))))
