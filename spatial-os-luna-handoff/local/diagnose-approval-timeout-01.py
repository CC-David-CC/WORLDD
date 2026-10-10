"""Repeat one unchanged failing case; never overwrite the original failed run."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

assert socket.gethostname().split('.')[0] == 'sb16'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / 'm02-approval-interruption-02'
out = base / 'm02-approval-timeout-diagnosis-01'
out.mkdir(exist_ok=False)
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
original = json.loads((root / 'checks/review.json').read_bytes())
assert original['status'] == 'failed'
profile = 'runtime-sanitize'
build = root / profile / 'build'
source = root / 'source'
case = 'test_revocation_requires_child_acknowledgement_with_automatic_delivery'
record = dict(status='running', purpose='Diagnose repeatability without changing source, assertions, or deadlines.',
    original_review_sha256=sha(root / 'checks/review.json'), original=str(root),
    profile=profile, case='AuthoritySupervisorTests.' + case, started=time.time(), attempts=[])

def save():
    (out / 'review.json').write_bytes((json.dumps(record, indent=2) + '\n').encode())

def audit():
    assert all(sha(source / name) == digest for name, digest in original['source'].items())
    assert all(sha(build / name) == digest for name, digest in original['profiles'][profile]['binaries'].items())
    assert sha(Path(original['server']['path'])) == original['server']['sha256']
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            name = (entry / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        assert name not in ('cc1', 'cc1plus', 'clang', 'clang++', 'lto1', 'ctest'), (entry, name)

save()
try:
    audit()
    metadata = json.loads(subprocess.check_output(['ctest', '--test-dir', str(build), '--show-only=json-v1',
        '-R', '^authority-supervisor$']))
    assert len(metadata['tests']) == 1
    test = metadata['tests'][0]
    assert test['name'] == 'authority-supervisor'
    template = test['command']
    script_index = template.index(str(source / 'tests/native/test_authority_supervisor.py'))
    assert template[script_index-1] == '/usr/bin/python3'
    program = ('import sys,unittest;sys.path.insert(0,' + repr(str(source / 'tests/native')) + ');'
        'import test_authority_supervisor as t;'
        'result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([t.AuthoritySupervisorTests('
        + repr(case) + ')]));raise SystemExit(not result.wasSuccessful())')
    for index in range(1, 6):
        audit()
        target = out / f'attempt-{index:02d}'
        target.mkdir()
        command = [arg if not arg.startswith('--unit=') else f'--unit=spatial-approval-timeout-diag01-{index}'
            for arg in template]
        command = [arg for arg in command if not arg.startswith('--setenv=SPATIAL_SECURITY_EVIDENCE=')]
        script_index = command.index(str(source / 'tests/native/test_authority_supervisor.py'))
        command[script_index:script_index+1] = ['-c', program]
        command.insert(command.index('/usr/bin/python3'), '--setenv=SPATIAL_SECURITY_EVIDENCE=' + str(target / 'native'))
        attempt = dict(command=command, started=time.time())
        record['attempts'].append(attempt)
        save()
        with (target / 'run.log').open('xb') as stream:
            result = subprocess.run(command, cwd=source, stdout=stream, stderr=subprocess.STDOUT,
                env={key: value for key, value in os.environ.items() if key != 'LSAN_OPTIONS'}, timeout=60)
        attempt.update(exit_code=result.returncode, ended=time.time(), log_sha256=sha(target / 'run.log'))
        audit()
        save()
        print(f'attempt {index}: exit {result.returncode}', flush=True)
        if result.returncode:
            record['status'] = 'failure_reproduced'
            print((target / 'run.log').read_text()[-5000:], flush=True)
            break
    else:
        record['status'] = 'not_reproduced_cause_unconfirmed'
except BaseException:
    record['status'] = 'diagnostic_error'
    raise
finally:
    record['ended'] = time.time()
    save()
    print(record['status'], flush=True)
