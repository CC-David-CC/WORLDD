"""Capture TLS progress for the actual failing case without changing its candidate."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

assert socket.gethostname().split('.')[0]=='sb16'
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
original=base/'m02-issuer-timeout-01'
source=original/'source';build=original/'runtime-sanitize/build'
out=base/'m02-participant-tls-progress-02';out.mkdir(exist_ok=False)
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
failed=json.loads((original/'checks/runtime-sanitize/review.json').read_bytes())
assert failed['status']=='failed'
record=dict(status='preparing',original_review_sha256=sha(original/'checks/runtime-sanitize/review.json'),
    source=failed['source'],binaries=failed['binaries'],source_changed=False,deadlines_changed=False,
    started=time.time(),maximum_case_attempts=20,
    hypothesis='The client kept checking while no reply header arrived. Observe issuer TLS progress and readiness together with syscall and off-CPU stalls.')
def save():(out/'review.json').write_bytes((json.dumps(record,indent=2)+'\n').encode())
def audit():
    assert all(sha(source/name)==digest for name,digest in failed['source'].items())
    assert all(sha(build/name)==digest for name,digest in failed['binaries'].items())
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():continue
        try:name=(path/'comm').read_text().strip()
        except (FileNotFoundError,ProcessLookupError,PermissionError):continue
        assert name not in ('ctest','ninja','cc1','cc1plus','clang','clang++','bpftrace'),(path,name)
program=r'''import json,os,sys,time,unittest
from pathlib import Path
out=Path(sys.argv.pop(1));source=Path(sys.argv.pop(1))
sys.path.insert(0,str(source/'tests/core'))
import test_identity_participant_runtime as t
(out/'test-pid').write_text(str(os.getpid()))
deadline=time.monotonic()+25
while not (out/'trace-ready').exists():
    if time.monotonic()>deadline:raise RuntimeError('Tracer not ready')
    time.sleep(.01)
events=[];results=[]
for attempt in range(1,21):
    directory=out/f'attempt-{attempt:02d}';directory.mkdir()
    os.environ['SPATIAL_SECURITY_EVIDENCE']=str(directory/'native')
    class TimedResult(unittest.TextTestResult):
        def record(self,event,test):
            events.append(dict(attempt=attempt,event=event,test=test.id(),monotonic_ns=time.monotonic_ns()))
            (out/'case-timing.json').write_text(json.dumps(events,indent=2)+'\n')
        def startTest(self,test):self.record('start',test);super().startTest(test)
        def stopTest(self,test):super().stopTest(test);self.record('stop',test)
    suite=unittest.TestSuite([t.ParticipantRuntimeTests('test_real_barrier_waits_for_explicit_application_acknowledgement')])
    assert suite.countTestCases()==1
    with (directory/'suite.log').open('w') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=TimedResult).run(suite)
    results.append(dict(attempt=attempt,passed=result.wasSuccessful(),tests=result.testsRun,
        failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped)))
    (out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    print(results[-1],flush=True)
    if not result.wasSuccessful():break
raise SystemExit(0 if all(row['passed'] for row in results) else 1)
'''
(out/'selected-suite.py').write_bytes(program.encode())
(out/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
kernel=(base/'approval-timeout-timing.bt').read_text()
tls=(base/'participant-tls-progress.bt').read_text()
head,cleanup=tls.rsplit('END {',1)
assert cleanup.rstrip().endswith('}') and kernel.count('END {')==1
kernel=kernel.replace('END {','END {'+cleanup.rstrip()[:-1],1)
(out/'progress.bt').write_bytes((kernel+'\n'+head).encode())
record['trace_program_sha256']=sha(out/'progress.bt')
record['openssl_library_sha256']=sha(Path('/lib/x86_64-linux-gnu/libssl.so.3'))
env={name:value for name,value in os.environ.items() if name!='LSAN_OPTIONS'}
env.update(PYTHONPATH=str(build/'python')+':'+str(source),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
    UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
save()
try:
    audit()
    preceding=base/'m02-participant-tls-progress-01'
    prefix_review=json.loads((preceding/'review.json').read_bytes())
    assert prefix_review['prefix_exit_code']==0
    assert sha(preceding/'prefix.log')==prefix_review['attachments']['prefix.log']
    record['passed_prefix_evidence']=dict(path=str(preceding/'prefix.log'),sha256=sha(preceding/'prefix.log'))
    save()
    command=['systemd-run','--user','--wait','--pipe','--collect','--unit=spatial-participant-tls-progress02',
        '--property=Delegate=yes','--property=RuntimeMaxSec=110','--property=TimeoutStopSec=15']
    command += ['--setenv='+key+'='+env[key] for key in ('PYTHONPATH','ASAN_OPTIONS','UBSAN_OPTIONS')]
    command += ['python3',str(out/'selected-suite.py'),str(out),str(source),str(build/'identityd'),
        str(build/'spatial-issuer-client'),str(build/'spatial-participant-runtime-probe')]
    record['command']=command;save()
    with (out/'run.log').open('xb') as log,(out/'timing.log').open('xb') as timing:
        proc=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+20
        while not (out/'test-pid').exists():
            assert proc.poll() is None and time.monotonic()<deadline,'Wrapper did not start'
            time.sleep(.02)
        pid=int((out/'test-pid').read_text())
        tracer_command=['sudo','-n','bpftrace','-q','-B','none',str(out/'progress.bt'),str(pid)]
        record.update(test_pid=pid,trace_command=tracer_command);save()
        tracer=subprocess.Popen(tracer_command,stdout=timing,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+20
        while b'READY root=' not in (out/'timing.log').read_bytes():
            assert tracer.poll() is None and time.monotonic()<deadline,'Tracer did not start'
            time.sleep(.05)
        (out/'trace-ready').touch();record['status']='observing';save()
        reported=0
        while proc.poll() is None:
            time.sleep(.2)
            path=out/'results.json'
            if path.exists():
                try:results=json.loads(path.read_bytes())
                except json.JSONDecodeError:continue
                if len(results)>reported:reported=len(results);print(results[-1],flush=True)
        record.update(exit_code=proc.returncode,trace_exit_code=tracer.wait(timeout=8))
    audit()
    record['results']=json.loads((out/'results.json').read_bytes())
    assert record['trace_exit_code']==0 and 'ROOT_EXIT' in (out/'timing.log').read_text()
    record['status']='failure_reproduced' if proc.returncode else 'not_reproduced_cause_unconfirmed'
except BaseException:
    record['status']='diagnostic_error'
    if 'proc' in globals() and proc.poll() is None:
        subprocess.run(['systemctl','--user','stop','spatial-participant-tls-progress02.service'],timeout=25,check=False)
        proc.wait(timeout=5)
    raise
finally:
    record['ended']=time.time()
    record['attachments']={path.relative_to(out).as_posix():sha(path) for path in out.rglob('*')
        if path.is_file() and path.name!='review.json'}
    save();print(record['status'],flush=True)
