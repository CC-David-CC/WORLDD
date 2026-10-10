"""Qualify frozen294 after diagnostic workloads finish, with no compiler overlap."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=r/'local/crash-fencing-serial-02';out.mkdir()
source=r/'local/m02-supervisor-crash-fencing-01/source'
manifest=json.loads((r/'local/supervisor-crash-fencing-candidate-01.json').read_text())['files']
assert len(manifest)==294
locks=json.loads((source/'acceptance.lock.json').read_text())['files'];assert len(locks)==41
def audit():
    for files in (manifest,locks):
        assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==v for n,v in files.items())
audit()
# Wait for the specific diagnostic that currently owns the compiler workload.
launch=json.loads((r/'local/issuer-sync-driver-09/launch.json').read_text())
pid=launch['pid'];wait_started=time.time()
while True:
    process=Path('/proc')/str(pid)
    try:
        command=(process/'cmdline').read_bytes()
        live=b'diagnose-issuer-sync-09.py' in command
    except FileNotFoundError:live=False
    if not live:break
    assert time.time()-wait_started<1800,'Diagnostic did not finish within the queue bound'
    time.sleep(2)
diagnostic=json.loads((r/'local/issuer-sync-diagnostic-09/summary.json').read_text())
assert diagnostic['build_exit_code']==0 and diagnostic['diagnostic_only']
def compilers():
    found=[]
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():continue
        try:name=(process/'comm').read_text().strip()
        except (FileNotFoundError,ProcessLookupError,PermissionError):continue
        if name in ('cc1','cc1plus','clang','clang++','lto1'):found.append(dict(pid=int(process.name),name=name))
    return found
assert not compilers(),'Compiler overlap prevents qualification start'
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
overlap=[];stop=threading.Event()
def monitor():
    with (out/'compiler-monitor.jsonl').open('x') as log:
        while not stop.is_set():
            found=compilers()
            record=dict(time=time.time(),compilers=found)
            log.write(json.dumps(record)+'\n');log.flush()
            if found:overlap.append(record)
            stop.wait(1)
thread=threading.Thread(target=monitor);thread.start()
record=dict(host=host,source_files=294,locked_files=41,source_manifest=manifest,
    qualification='running',started=time.time(),profiles={},compiler_overlap=overlap,
    scheduling='One qualification job per host. Both hosts run in parallel. No compiler jobs overlap qualification.')
def save():
    (out/'summary.json').write_text(json.dumps(record,indent=2)+'\n')
save()
try:
    for profile in ('debug','runtime-sanitize'):
        target=out/profile;target.mkdir()
        original=r/'local/m02-supervisor-crash-fencing-01'/profile
        build=original/'build'
        previous=json.loads((original/'run.json').read_text())['results']
        assert next(x for x in previous if x['name']=='build')['exit_code']==0
        env=dict(os.environ,SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),
            SPATIAL_SECURITY_EVIDENCE=str(target/'server'),PYTHONPATH=str(build/'python')+':'+str(source),
            SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]),SPATIAL_EVIDENCE_DIR=str(target/'cases'),
            ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        binaries={n:hashlib.sha256((build/n).read_bytes()).hexdigest() for n in
            ('identityd','worldd','workspaced','spatialwm','spatial-test-host','spatial-authority-supervisor','spatial-issuer-client')}
        results=[]
        record['profiles'][profile]=dict(build=str(build),binaries=binaries,results=results,server=server)
        commands=[('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),
            ('fencing',['ctest','--test-dir',str(build),'-V','-R','^authority-supervisor-crash-fencing$']),
            ('focused',['ctest','--test-dir',str(build),'-V','-R','^authority-','-E','^authority-supervisor-crash-fencing$']),
            ('core',['ctest','--test-dir',str(build),'-V','-E','^authority-']),
            ('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(target/'P10.json')])]
        for name,command in commands:
            audit();assert not compilers(),'Compiler detected before next qualification segment'
            started=time.time()
            with (target/(name+'.log')).open('xb') as log:
                result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
            results.append(dict(name=name,command=command,exit_code=result.returncode,start=started,end=time.time()))
            save();print(host,profile,name,result.returncode,flush=True)
            if result.returncode:
                record['qualification']='failed';save()
                print((target/(name+'.log')).read_text()[-12000:],flush=True)
                raise SystemExit(result.returncode)
        for n,digest in binaries.items():assert hashlib.sha256((build/n).read_bytes()).hexdigest()==digest
    audit()
    record['qualification']='passed' if not overlap else 'invalid_compiler_overlap'
except BaseException:
    if record['qualification']=='running':record['qualification']='driver_error'
    raise
finally:
    stop.set();thread.join();record['ended']=time.time();save()
print(host,'serial full qualification',record['qualification'],flush=True)
