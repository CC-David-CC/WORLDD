"""Trace the four-suite startup prefix on the same host and frozen Debug binaries."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

assert socket.gethostname().split('.')[0] == 'sb16-2'
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
gate = base/'m02-phase-gate-01'
source = gate/'source'
build = gate/'debug/build'
out = base/'m02-startup-timing-02'
lock = (base/'m02-phase-qualification.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
out.mkdir(exist_ok=False)
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
manifest = json.loads((gate/'manifest.json').read_bytes())
run = json.loads((gate/'summary.json').read_bytes())
assert run['status'] == 'failed' and 'ended' in run
assert sha(Path(run['server']['path'])) == run['server']['sha256']
assert all(sha(source/name) == digest for name, digest in manifest['files'].items())
assert all(sha(build/name) == digest for name, digest in run['profiles']['debug']['binaries'].items())
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try: name = (p/'comm').read_text().strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError): continue
    assert name not in ('ctest','ninja','cc1','cc1plus','clang','clang++','bpftrace'), (p, name)
tests = json.loads((gate/'debug/checks/ctest-inventory.log').read_bytes())['tests'][:4]
assert [t['name'] for t in tests] == ['control-supervisor-reply','authority-native-config','authority-native-scope','world-revision-interleave']
(out/'tests.json').write_text(json.dumps(tests, indent=2)+'\n')
(out/'timing.bt').write_bytes((base/'approval-timeout-timing.bt').read_bytes())
(out/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
(out/'worker.py').write_text('''import json,os,subprocess,sys,time
from pathlib import Path
out=Path(sys.argv[1]);source=Path(sys.argv[2]);build=Path(sys.argv[3])
deadline=time.monotonic()+25
while not (out/'trace-ready').exists():
 if time.monotonic()>deadline:raise RuntimeError('Tracer not ready')
 time.sleep(.01)
records=[]
for t in json.loads((out/'tests.json').read_bytes()):
 env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_SECURITY_EVIDENCE=str(out/'native'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
 env.pop('LSAN_OPTIONS',None)
 for prop in t['properties']:
  if prop['name']=='ENVIRONMENT':
   for setting in prop['value']:
    k,v=setting.split('=',1);env[k]=v
 entry=dict(suite=t['name'],started_ns=time.monotonic_ns(),argv=t['command'])
 with (out/(t['name']+'.log')).open('xb') as log:
  p=subprocess.run(t['command'],cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=70)
 entry.update(ended_ns=time.monotonic_ns(),exit_code=p.returncode);records.append(entry)
 (out/'results.json').write_text(json.dumps(records,indent=2)+'\\n')
 print(t['name'],p.returncode,flush=True)
 if p.returncode:break
raise SystemExit(any(r['exit_code'] for r in records))
''')
record = dict(status='preparing', host='sb16-2', source_commit=manifest['commit'],
    source=manifest['files'], binaries=run['profiles']['debug']['binaries'],
    selected=[t['name'] for t in tests], attempts=1, source_changed=False, deadlines_changed=False,
    original_failure_resolved=False, instrumentation='Syscall durations and scheduler waits; no payload buffers.',
    started=time.time())
def save():
    (out/'review.json').write_text(json.dumps(record, indent=2)+'\n')
save()
proc = tracer = None
try:
    with (out/'run.log').open('xb') as log, (out/'timing.log').open('xb') as timing:
        command=['python3',str(out/'worker.py'),str(out),str(source),str(build)]
        env={k:v for k,v in os.environ.items() if k not in ('LSAN_OPTIONS','DISPLAY','SPATIAL_TEST_DISPLAY','CTEST_PARALLEL_LEVEL')}
        env.update(SPATIAL_TEST_XSERVER=run['server']['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),
            SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]),SPATIAL_EVIDENCE_DIR=str(out/'cases'))
        proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env)
        record['worker_pid']=proc.pid
        trace=['sudo','-n','bpftrace','-q','-B','none',str(out/'timing.bt'),str(proc.pid)]
        record['trace_argv']=trace;save()
        tracer=subprocess.Popen(trace,stdout=timing,stderr=subprocess.STDOUT)
        until=time.monotonic()+20
        while b'READY root=' not in (out/'timing.log').read_bytes():
            assert tracer.poll() is None and time.monotonic()<until,(out/'timing.log').read_text()
            time.sleep(.05)
        (out/'trace-ready').touch();record['status']='observing';save()
        record['test_exit_code']=proc.wait(timeout=85)
        record['trace_exit_code']=tracer.wait(timeout=10)
    text=(out/'timing.log').read_text()
    assert record['trace_exit_code']==0 and 'ROOT_EXIT' in text and 'TRACE_DEADLINE' not in text
    record['results']=json.loads((out/'results.json').read_bytes())
    record['status']='target_suite_failed' if record['test_exit_code'] else 'not_reproduced_cause_unconfirmed'
    if len(record['results']) != 4:record['status']='prefix_failed_target_unrun'
    assert all(sha(source/name)==digest for name,digest in manifest['files'].items())
except BaseException as error:
    record['status']='diagnostic_error';record['error']=type(error).__name__+': '+str(error)
    if proc and proc.poll() is None:
        assert os.getpgid(proc.pid)==proc.pid
        os.killpg(proc.pid,signal.SIGTERM)
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=5)
    if tracer and tracer.poll() is None:
        tracer.wait(timeout=95)
    raise
finally:
    record['ended']=time.time()
    record['attachments']={p.relative_to(out).as_posix():sha(p) for p in out.rglob('*') if p.is_file() and p.name!='review.json'}
    save();print(json.dumps({k:v for k,v in record.items() if k not in ('source','binaries','attachments')}),flush=True)
