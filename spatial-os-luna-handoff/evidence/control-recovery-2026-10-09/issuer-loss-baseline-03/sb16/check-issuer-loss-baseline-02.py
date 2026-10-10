"""Run one issuer-loss baseline with unchanged, verified Debug binaries."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
prior = base / 'm02-crosshost-cancellation-01'; source = prior / 'source'; build = prior / 'debug/build'
build_path = prior / 'checks/debug/build-review.json'; compiled = json.loads(build_path.read_bytes())
assert compiled['status'] == 'built_tests_unrun'
out = base / 'm02-issuer-loss-baseline-02'; out.mkdir(mode=0o700, exist_ok=False)
test = out / 'test-issuer-loss-baseline-02.py'
shutil.copyfile(base / test.name, test)
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()

def audit():
    assert all(sha(source / name) == digest for name, digest in compiled['source'].items())
    assert all(sha(build / name) == digest for name, digest in compiled['binaries'].items())
    assert sha(Path(compiled['server']['path'])) == compiled['server']['sha256']

audit()
for path in Path('/proc').iterdir():
    if not path.name.isdigit(): continue
    try: name = (path / 'comm').read_text().strip()
    except (FileNotFoundError, ProcessLookupError, PermissionError): continue
    assert name not in ('ctest', 'cc1', 'cc1plus', 'clang', 'clang++', 'ninja'), (path, name)
command = ['systemd-run', '--user', '--wait', '--pipe', '--collect',
    '--unit=spatial-issuer-loss-baseline-02', '--property=Delegate=yes',
    '--property=RuntimeMaxSec=120', '--property=TimeoutStopSec=20',
    '--setenv=SPATIAL_TEST_XSERVER=' + compiled['server']['path'],
    '/usr/bin/python3', str(test), '--source', str(source), '--build', str(build), '--output', str(out / 'witness')]
record = dict(status='running', host='sb16', command=command, started=time.time(),
    build_review_sha256=sha(build_path), test_sha256=sha(test))
save = lambda: (out / 'review.json').write_bytes((json.dumps(record, indent=2) + '\n').encode())
save()
try:
    with (out / 'baseline.log').open('xb') as log:
        result = subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT,
            env={name: value for name, value in os.environ.items() if name != 'LSAN_OPTIONS'}, timeout=155)
    record['exit_code'] = result.returncode; audit()
    text = (out / 'baseline.log').read_text()
    record['status'] = 'passed' if result.returncode == 0 else 'failed'
    if result.returncode == 1 and 'FAILED (failures=1)' in text and 'Retirement requires stopped ownership, an issuer fence, and dependent cleanup' in text:
        witness = json.loads((out / 'witness/witness.json').read_bytes())
        assert witness['pending_survived_restart'] and witness['final_pending'] == 2
        assert witness['checked_world_cleanup'] and witness['checked_broker_cleanup']
        assert not witness['world_fence'] and not witness['broker_fence']
        record['status'] = 'missing_deferred_fence_reproduced'
    print(json.dumps(dict(status=record['status'], exit_code=result.returncode, log=str(out / 'baseline.log'))), flush=True)
    print(text[-8000:], flush=True)
except BaseException as error:
    record['status'] = 'launcher_error'; record['error'] = type(error).__name__ + ': ' + str(error); raise
finally:
    record['ended'] = time.time(); record['log_sha256'] = sha(out / 'baseline.log'); save()
raise SystemExit(0 if record['status'] in ('passed', 'missing_deferred_fence_reproduced') else 1)
