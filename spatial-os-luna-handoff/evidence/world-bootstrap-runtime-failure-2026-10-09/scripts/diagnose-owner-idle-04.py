"""Exercise the newly failing idle scenario with the existing exception diagnostic."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
prior=r/'local/world-bootstrap-diagnostic-run-01';source=prior/'source';build=prior/'build'
manifest=json.loads((r/'local/world-bootstrap-diagnostic-01.json').read_text())['files']
assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==v for n,v in manifest.items())
# This is a shared-path diagnostic, not a rerun or qualification of fencing292.
fencing=r/'local/m02-authority-fencing-01/source'
for name in ('tests/core/test_workspace_bootstrap_owner_idle.py','tests/core/test_workspace_bootstrap.py',
             'tests/core/test_world_identity.py','src/identity/tls_server.cpp','src/world/identity_sessions.cpp'):
    assert (source/name).read_bytes()==(fencing/name).read_bytes(),name
out=r/'local/owner-idle-diagnostic-04';out.mkdir()
listed=json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1']))
command=next(t['command'] for t in listed['tests'] if t['name']=='workspace-bootstrap-owner-idle')
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),
    ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
records=[]
for index in range(20):
    target=out/('repeat-'+str(index+1).zfill(2));target.mkdir()
    env['SPATIAL_SECURITY_EVIDENCE']=str(target/'service-logs')
    with (target/'test.log').open('xb') as log:
        result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=60)
    records.append(dict(repetition=index+1,command=command,exit_code=result.returncode))
    (out/'repetitions.json').write_text(json.dumps(records,indent=2)+'\n')
    print(host,'owner idle diagnostic',index+1,result.returncode,flush=True)
    if result.returncode:
        print((target/'test.log').read_text()[-6000:],flush=True)
        for p in sorted((target/'service-logs').glob('*.log')):
            value=p.read_text(errors='replace')
            if value:print(p.name,value[-5000:],flush=True)
        break
assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==v for n,v in manifest.items())
with tarfile.open(r/('local/owner-idle-diagnostic-'+host+'-04.tar.gz'),'w:gz') as archive:
    for p in sorted(out.rglob('*')):
        if p.is_file() and p.suffix in ('.log','.json'):
            archive.add(p,arcname='owner-idle-diagnostic-04/'+host+'/'+p.relative_to(out).as_posix())
print(host,'owner-idle diagnostic complete; no qualification claim',flush=True)
