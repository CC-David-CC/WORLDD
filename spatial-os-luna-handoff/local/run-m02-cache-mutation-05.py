"""Reassess the required world-cache witness using the restored isolated build."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

assert socket.gethostname().split('.')[0]=='sb16'
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
lock=(base/'m02-phase-qualification.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
prior=base/'m02-focused-mutations-04';source=prior/'worktree/spatial-os-luna-handoff';build=prior/'build'
manifest=json.loads((base/'m02-phase-gate-01/manifest.json').read_bytes())
review=json.loads((base/'m02-mutations-review-04.json').read_bytes())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert review['status']=='four_mutation_witnesses_verified'
assert all(sha(build/n)==d for n,d in review['restored_binaries'].items())
assert all(sha(source/n)==d for n,d in manifest['files'].items())
assert subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=source)==b''
for p in Path('/proc').iterdir():
    if not p.name.isdigit():continue
    try:n=(p/'comm').read_text().strip()
    except (FileNotFoundError,ProcessLookupError,PermissionError):continue
    assert n not in ('ctest','ninja','cc1','cc1plus','clang','clang++'),(p,n)
out=base/'m02-cache-mutation-05';out.mkdir(exist_ok=False)
target=source/'src/world/authority.cpp';before=target.read_bytes()
old=b'const auto result = store_.revoke(actor, id, revision, [this](const auto& subject) { return active(subject); });\n    decisions_.clear(); return result;'
new=b'const auto result = store_.revoke(actor, id, revision, [this](const auto& subject) { return active(subject); });\n    return result;'
assert before.count(old)==1
record=dict(status='running',source_commit=manifest['commit'],host='sb16',profile='Debug',phase_complete=False,
    test='WorldAuthorityTests.test_revocation_stops_motion_without_a_recipient_request',
    file='src/world/authority.cpp',before_sha256=sha(target),after_sha256=hashlib.sha256(before.replace(old,new)).hexdigest(),
    commands=[],binary_pins={},started=time.time())
def save():
    (out/'summary.json').write_text(json.dumps(record,indent=2)+'\n')
env={k:v for k,v in os.environ.items() if k not in ('DISPLAY','SPATIAL_TEST_DISPLAY','LSAN_OPTIONS')}
server=json.loads((base/'m02-phase-gate-01/summary.json').read_bytes())['server']
assert sha(Path(server['path']))==server['sha256']
env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(build/'python')+':'+str(source),
    SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),SPATIAL_SECURITY_EVIDENCE=str(out/'native'))
suite=next(t for t in json.loads((prior/'inventory.log').read_bytes())['tests'] if t['name']=='world-authority')
def run(label,argv):
    entry=dict(label=label,argv=argv,started=time.time());record['commands'].append(entry);save()
    with (out/(label+'.log')).open('xb') as log:
        p=subprocess.run(argv,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240)
    entry.update(exit_code=p.returncode,ended=time.time(),sha256=sha(out/(label+'.log')));save()
    print(label,p.returncode,flush=True);return p.returncode,(out/(label+'.log')).read_text()
def witness(label):
    record['binary_pins'][label]={n:sha(build/n) for n in ('identityd','spatial-issuer-client','worldd')};save()
    code,text=run(label,suite['command']+[record['test']])
    assert re.search(r'^Ran 1 test in ',text,re.M) and 'skipped' not in text and 'ERROR:' not in text
    if label!='mutant':assert code==0 and text.rstrip().endswith('OK')
    else:
        if code==1:
            assert 'FAIL: test_revocation_stops_motion_without_a_recipient_request' in text and 'FAILED (failures=1)' in text
            record['mutation_result']='intended_assertion_failed'
        else:
            assert code==0 and text.rstrip().endswith('OK')
            record['mutation_result']='not_detected_requires_review'
    save()
save()
try:
    witness('baseline')
    try:
        target.write_bytes(before.replace(old,new))
        (out/'mutation.patch').write_bytes(subprocess.check_output(['git','diff','--','src/world/authority.cpp'],cwd=source))
        assert run('mutant-build',['cmake','--build',str(build),'--parallel','2','--target','worldd'])[0]==0
        witness('mutant')
    finally:target.write_bytes(before)
    assert run('restored-build',['cmake','--build',str(build),'--parallel','2','--target','worldd'])[0]==0
    witness('restored')
    assert all(sha(source/n)==d for n,d in manifest['files'].items())
    assert subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=source)==b''
    record['status']='completed';record['source_restored']=True
except BaseException as error:
    record.update(status='failed',error=type(error).__name__+': '+str(error));raise
finally:
    record['ended']=time.time();save();print(record['status'],record.get('mutation_result'),flush=True)
