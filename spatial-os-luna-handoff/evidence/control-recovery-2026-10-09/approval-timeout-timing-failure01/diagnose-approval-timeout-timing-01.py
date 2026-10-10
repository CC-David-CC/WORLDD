"""Observe timing for one unchanged sanitizer case without ptrace or payload capture."""
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
source = root / 'source'
build = root / 'runtime-sanitize/build'
out = base / 'm02-approval-timeout-timing-01'
out.mkdir(exist_ok=False)
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
original = json.loads((root / 'checks/review.json').read_bytes())
assert original['status'] == 'failed'
record = dict(status='preparing', started=time.time(), original=str(root),
    original_review_sha256=sha(root / 'checks/review.json'), profile='runtime-sanitize',
    selection='AuthoritySupervisorTests.test_revocation_requires_child_acknowledgement_with_automatic_delivery',
    limit=10, instrumentation='Kernel syscall durations and off-CPU time for the test process tree. No buffers or request data.',
    source_changed=False, deadlines_changed=False, original_failure_resolved=False)

def save():
    (out / 'review.json').write_bytes((json.dumps(record, indent=2) + '\n').encode())

def audit():
    assert all(sha(source / name) == digest for name, digest in original['source'].items())
    assert all(sha(build / name) == digest for name, digest in original['profiles']['runtime-sanitize']['binaries'].items())
    assert sha(Path(original['server']['path'])) == original['server']['sha256']
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit(): continue
        try: name = (entry / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError, PermissionError): continue
        assert name not in ('ctest', 'cc1', 'cc1plus', 'clang', 'clang++', 'lto1'), (entry, name)

program = '''import json,os,sys,time,unittest
from pathlib import Path
out=Path(sys.argv.pop(1));source=Path(sys.argv.pop(1))
sys.path.insert(0,str(source/'tests/native'))
import test_authority_supervisor as t
(out/'test-pid').write_text(str(os.getpid()))
until=time.monotonic()+25
while not (out/'trace-ready').exists():
    if time.monotonic()>until: raise RuntimeError('Timing tracer did not become ready')
    time.sleep(.01)
results=[]
for index in range(1,11):
    directory=out/f'attempt-{index:02d}';directory.mkdir()
    os.environ['SPATIAL_SECURITY_EVIDENCE']=str(directory/'native')
    with (directory/'run.log').open('w') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.TestSuite([
            t.AuthoritySupervisorTests('test_revocation_requires_child_acknowledgement_with_automatic_delivery')]))
    results.append(dict(index=index,passed=result.wasSuccessful(),tests=result.testsRun,
        failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped)))
    (out/'results.json').write_text(json.dumps(results,indent=2)+'\\n')
    print(f'attempt {index}: '+('passed' if result.wasSuccessful() else 'failed'),flush=True)
    if not result.wasSuccessful(): break
raise SystemExit(0 if all(item['passed'] for item in results) else 1)
'''
(out / 'selected-case.py').write_bytes(program.encode())
save()
try:
    audit()
    metadata = json.loads(subprocess.check_output(['ctest', '--test-dir', str(build), '--show-only=json-v1', '-R', '^authority-supervisor$']))
    assert len(metadata['tests']) == 1
    command = metadata['tests'][0]['command']
    command = [arg if not arg.startswith('--unit=') else '--unit=spatial-approval-timeout-timing01' for arg in command]
    index = command.index(str(source / 'tests/native/test_authority_supervisor.py'))
    command[index:index+1] = [str(out / 'selected-case.py'), str(out), str(source)]
    command = [arg for arg in command if not arg.startswith('--setenv=SPATIAL_SECURITY_EVIDENCE=')]
    record['test_command'] = command
    save()
    with (out / 'run.log').open('xb') as log, (out / 'timing.log').open('xb') as timing:
        proc = subprocess.Popen(command, cwd=source, stdout=log, stderr=subprocess.STDOUT,
            env={key: value for key, value in os.environ.items() if key != 'LSAN_OPTIONS'})
        deadline = time.monotonic()+20
        while not (out / 'test-pid').exists():
            assert proc.poll() is None and time.monotonic()<deadline, 'Test did not start'
            time.sleep(.02)
        test_pid = int((out / 'test-pid').read_text())
        trace_command = ['sudo', '-n', 'bpftrace', '-q', '-B', 'none', str(base / 'approval-timeout-timing.bt'), str(test_pid)]
        record.update(test_pid=test_pid, trace_command=trace_command, trace_program_sha256=sha(base / 'approval-timeout-timing.bt'))
        save()
        tracer = subprocess.Popen(trace_command, stdout=timing, stderr=subprocess.STDOUT)
        deadline = time.monotonic()+20
        while b'READY root=' not in (out / 'timing.log').read_bytes():
            assert tracer.poll() is None and time.monotonic()<deadline, (out / 'timing.log').read_text()
            time.sleep(.05)
        (out / 'trace-ready').touch()
        record['status']='observing';save()
        reported=0
        while proc.poll() is None:
            time.sleep(.2)
            result_path=out/'results.json'
            if result_path.exists():
                try: results=json.loads(result_path.read_bytes())
                except json.JSONDecodeError: continue
                if len(results)>reported:
                    reported=len(results);print(results[-1],flush=True)
        record['test_exit_code']=proc.returncode
        record['trace_exit_code']=tracer.wait(timeout=8)
    audit()
    record['results']=json.loads((out/'results.json').read_bytes())
    record['status']='failure_reproduced' if proc.returncode else 'not_reproduced_cause_unconfirmed'
    assert record['trace_exit_code']==0 and 'ROOT_EXIT' in (out/'timing.log').read_text()
except BaseException:
    record['status']='diagnostic_error'
    raise
finally:
    record['ended']=time.time()
    record['attachments']={p.relative_to(out).as_posix():sha(p) for p in out.rglob('*')
        if p.is_file() and p.name!='review.json'}
    save()
    print(record['status'],flush=True)
