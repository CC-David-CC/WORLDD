"""Run native owner-channel integration checks after full candidate03 qualification."""
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import threading
import time

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
preceding = r/'local/m02-control-owner-03'
source = preceding/'source'
test = r/'local/test_control_owner_world_01.py'
out = r/'local/control-owner-world-01'
out.mkdir(exist_ok=False)
expected_pid = {'sb16':1679232, 'sb16-2':1463045}[host]
record = dict(host=host, status='waiting', preceding_pid=expected_pid, profiles={}, started=time.time(),
              test_sha256=hashlib.sha256(test.read_bytes()).hexdigest(), compiler_overlap=[],
              production_supervisor_binding=False, production_recovery_release=False)
stop = threading.Event()
monitor = None

def save():
    (out/'summary.json').write_text(json.dumps(record,indent=2)+'\n')

def compilers():
    found=[]
    for path in Path('/proc').iterdir():
        if not path.name.isdigit(): continue
        try: name=(path/'comm').read_text().strip()
        except (FileNotFoundError,ProcessLookupError,PermissionError): continue
        if name in ('cc1','cc1plus','clang','clang++','lto1'):found.append(dict(pid=int(path.name),name=name))
    return found

def watch():
    with (out/'compiler-monitor.jsonl').open('x') as log:
        while not stop.is_set():
            found=compilers();entry=dict(time=time.time(),compilers=found)
            log.write(json.dumps(entry)+'\n');log.flush()
            if found:record['compiler_overlap'].append(entry)
            stop.wait(1)

save()
try:
    launch=json.loads((preceding/'launch.json').read_text())
    assert launch['pid']==expected_pid
    marker=Path('/proc')/str(expected_pid)/'cmdline'
    if marker.exists() and b'qualify-control-owner-03.py' in marker.read_bytes():
        descriptor=os.pidfd_open(expected_pid)
        try:
            poller=select.poll();poller.register(descriptor,select.POLLIN)
            while not poller.poll(2000):pass
        finally:os.close(descriptor)
    qualified=json.loads((preceding/'qualification/summary.json').read_text())
    if qualified['status']!='passed' or qualified['compiler_overlap']:
        record.update(status='not_run',reason='Full preceding qualification did not pass')
        save()
        raise SystemExit(0)
    manifest=json.loads((preceding/'manifest.json').read_text())
    assert len(manifest['files'])==297
    for group in ('files','locked_files'):
        assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==h for n,h in manifest[group].items())
    server=qualified['server']
    assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
    record['source_manifest']=manifest['files']
    record['preceding_summary_sha256']=hashlib.sha256((preceding/'qualification/summary.json').read_bytes()).hexdigest()
    record['status']='running';save()
    assert not compilers()
    monitor=threading.Thread(target=watch);monitor.start()
    for profile in ('debug','runtime-sanitize'):
        before=qualified['profiles'][profile];build=Path(before['build'])
        assert all(hashlib.sha256((build/n).read_bytes()).hexdigest()==h for n,h in before['binaries'].items())
        assert hashlib.sha256(test.read_bytes()).hexdigest()==record['test_sha256']
        target=out/profile;target.mkdir()
        env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source/'tests/core')+':'+str(source),
            SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),
            SPATIAL_SECURITY_EVIDENCE=str(target/'native'),SPATIAL_CONTROL_OWNER_WORLD_EVIDENCE=str(target/'witness'),
            ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
        env.pop('LSAN_OPTIONS',None)
        command=['python3',str(test),str(build/'identityd'),str(build/'spatial-issuer-client'),
                 str(build/'worldd'),str(build/'spatial-security-auth')]
        assert not compilers()
        started=time.time()
        with (target/'test.log').open('xb') as log:
            result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
        record['profiles'][profile]=dict(command=command,started=started,ended=time.time(),exit_code=result.returncode,
                                        binaries=before['binaries'])
        save();print(host,profile,result.returncode,flush=True)
        if result.returncode:
            print((target/'test.log').read_text()[-10000:],flush=True)
            raise RuntimeError('Native owner-channel integration failed')
    assert not record['compiler_overlap']
    record['status']='passed'
except SystemExit:
    raise
except BaseException:
    record['status']='failed'
    raise
finally:
    stop.set()
    if monitor:monitor.join()
    record['ended']=time.time()
    save()
