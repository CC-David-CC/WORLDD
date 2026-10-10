import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import time

r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
root=r/'local/m02-connected-recovery-03'
checked=json.loads((root/'checks/review.json').read_bytes());assert checked['status']=='passed'
assert json.loads((root/'checks-observation06/review.json').read_bytes())['status']=='passed'
source=root/'source';build=root/'debug/build';out=root/'crash-baseline01';out.mkdir(exist_ok=False)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
cpp=r/'local/session-world-crash-probe-01.cpp';fixture=r/'local/test_session_world_crash_01.py'
record=dict(status='running',source_sha256=sha(cpp),fixture_sha256=sha(fixture),production_source=checked['source'],
    purpose='Real world and supervisor exit after checked native/process cleanup must still deliver the exact receipt to the retained coordinator.',commands=[])
def save(): (out/'review.json').write_text(json.dumps(record,indent=2)+'\n')
def audit():
    assert all(sha(source/n)==h for n,h in checked['source'].items())
    assert all(sha(build/n)==h for n,h in checked['profiles']['debug']['binaries'].items())
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:name=(p/'comm').read_text().strip()
        except (FileNotFoundError,ProcessLookupError,PermissionError):continue
        assert name not in ('ctest','cc1','cc1plus','clang','clang++'),(p,name)
def run(name,command,env=None):
    audit(); entry=dict(name=name,command=command,started=time.time());record['commands'].append(entry);save()
    log=out/(name+'.log')
    with log.open('xb') as stream: result=subprocess.run(command,cwd=build,env=env,stdout=stream,stderr=subprocess.STDOUT)
    entry.update(exit_code=result.returncode,ended=time.time(),sha256=sha(log));save();audit()
    print(name,result.returncode,flush=True)
    if result.returncode: print(log.read_text()[-12000:],flush=True)
    return result.returncode
save()
try:
    # Reuse the actual compile/link commands with a separate diagnostic object and executable.
    target='CMakeFiles/spatial-session-world-probe.dir/src/testing/session_world_probe.cpp.o'
    raw=subprocess.check_output(['ninja','-t','commands',target],cwd=build,text=True).strip().splitlines()[-1]
    compile_args=shlex.split(raw)
    index=compile_args.index('-o');compile_args[index+1]=str(out/'crash-probe.o')
    for flag, name in (('-MF','crash-probe.o.d'),('-MT','crash-probe.o')):
        if flag in compile_args:compile_args[compile_args.index(flag)+1]=str(out/name)
    compile_args[-1]=str(cpp)
    assert run('compile-probe',compile_args)==0
    raw=subprocess.check_output(['ninja','-t','commands','spatial-session-world-probe'],cwd=build,text=True).strip().splitlines()[-1]
    link=shlex.split(raw);assert link[:2]==[':','&&'] and link[-2:]==['&&',':'];link=link[2:-2]
    link=[str(out/'crash-probe.o') if arg==target else arg for arg in link]
    link[link.index('-o')+1]=str(out/'spatial-session-world-crash-probe')
    assert run('link-probe',link)==0
    record['probe_sha256']=sha(out/'spatial-session-world-crash-probe');save()
    env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/native')+':'+str(r/'local'),
        SPATIAL_TEST_XSERVER=checked['server']['path'],SPATIAL_SECURITY_EVIDENCE=str(out/'native'),
        SPATIAL_WORLD_CRASH_PROBE=str(out/'spatial-session-world-crash-probe'))
    cmd=['systemd-run','--user','--wait','--pipe','--collect','--unit=spatial-world-crash-baseline01','--property=Delegate=yes']
    for key in ('PYTHONPATH','SPATIAL_TEST_XSERVER','SPATIAL_SECURITY_EVIDENCE','SPATIAL_WORLD_CRASH_PROBE'):cmd.append('--setenv='+key+'='+env[key])
    cmd+=['python3',str(fixture),str(source),str(build/'spatial-authority-launch-probe'),str(build/'spatial-security-auth'),str(build/'identityd'),str(build/'spatial-issuer-client')]
    rc=run('crash-recovery',cmd,env)
    text=(out/'crash-recovery.log').read_text()
    assert rc==1 and 'Missing checked cleanup delivery after world crash' in text and 'Session supervisor ownership lost' in text
    record['status']='expected_recovery_failure_reproduced'
except BaseException:
    record['status']='diagnostic_error';raise
finally:save()
