"""Run selected native regressions against immutable, hash-checked binaries."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

assert socket.gethostname().split('.')[0] == 'sb16'
profile = sys.argv[1]; assert profile in ('debug', 'runtime-sanitize')
root = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/m02-deferred-fence-01')
source = root / 'source'; build = root / profile / 'build'; out = root / 'checks' / profile
compiled = json.loads((out / 'build-review.json').read_bytes()); assert compiled['status'] == 'built_tests_unrun'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
selections = [('deferred-fence', 'test_deferred_fence.py'),
    ('coordinator-retirement', 'test_coordinator_retirement.py'),
    ('broker-retirement', 'test_broker_retirement.py'),
    ('stopped-broker-dependency', 'test_stopped_broker_dependency.py'),
    ('broker-cleanup', 'test_session_broker_cleanup.py'),
    ('authority-launch', 'test_authority_launch.py')]
if len(sys.argv) == 3:
    assert sys.argv[2] == 'authority-retry'
    selections = selections[-1:]
    out = out / 'authority-retry'; out.mkdir(exist_ok=False)
record = dict(status='running', host='sb16', profile=profile, selected=selections, started=time.time(), cases=[])
review = out / 'test-review.json'; assert not review.exists()
save = lambda: review.write_bytes((json.dumps(record, indent=2) + '\n').encode())

def audit():
    assert all(sha(source / name) == value for name, value in compiled['source'].items())
    assert all(sha(build / name) == value for name, value in compiled['binaries'].items())
    assert sha(Path(compiled['server']['path'])) == compiled['server']['sha256']
    for path in Path('/proc').iterdir():
        if not path.name.isdigit(): continue
        try: name = (path / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError): continue
        assert name not in ('ctest', 'cc1', 'cc1plus', 'clang', 'clang++', 'ninja'), (path, name)

save()
try:
    for label, filename in selections:
        audit(); evidence = out / (label + '-native'); evidence.mkdir(mode=0o700)
        command = ['systemd-run', '--user', '--wait', '--pipe', '--collect',
            '--unit=spatial-deferred-01-' + profile + '-' + label, '--property=Delegate=yes',
            '--property=RuntimeMaxSec=240', '--property=TimeoutStopSec=20',
            '--setenv=PYTHONPATH=' + str(build / 'python') + ':' + str(source),
            '--setenv=SPATIAL_TEST_XSERVER=' + compiled['server']['path'],
            '--setenv=SPATIAL_SECURITY_EVIDENCE=' + str(evidence),
            '--setenv=ASAN_OPTIONS=detect_leaks=1:abort_on_error=1',
            '--setenv=UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1',
            '/usr/bin/python3', str(source / 'tests/native' / filename), str(source),
            str(build / 'spatial-authority-launch-probe'), str(build / 'spatial-security-auth')]
        if label == 'authority-launch': command.remove(str(source))
        if label != 'authority-launch': command += [str(build / 'identityd'), str(build / 'spatial-issuer-client')]
        item = dict(name=label, command=command, started=time.time()); record['cases'].append(item); save()
        logpath = out / (label + '.log')
        with logpath.open('xb') as log:
            result = subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT,
                env={name:value for name,value in os.environ.items() if name != 'LSAN_OPTIONS'}, timeout=280)
        audit(); text = logpath.read_text(); match = re.search(r'Ran (\d+) tests? in', text)
        item.update(exit_code=result.returncode, ended=time.time(), count=int(match.group(1)) if match else None,
            log_sha256=sha(logpath), status='passed' if result.returncode == 0 and '\nOK\n' in text else 'failed')
        save(); print(profile, label, item['status'], item['count'], flush=True)
        if item['status'] != 'passed':
            print(text[-10000:], flush=True); raise RuntimeError(label + ' failed')
    record['status'] = 'passed'
except BaseException as error:
    record['status'] = 'failed'; record['error'] = type(error).__name__ + ': ' + str(error); raise
finally:
    record['ended'] = time.time(); save()
