"""Capture complete workspace authority fixtures with passive transport diagnostics."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import time
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
prior=r/'local/transport-diagnostic-run-05';source=prior/'source';build=prior/'build'
manifest=json.loads((r/'local/transport-diagnostic-05.json').read_text())['files']
assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==v for n,v in manifest.items())
out=r/'local/authority-suite-diagnostic-08';out.mkdir()
listed=json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1']))
command=next(t['command'] for t in listed['tests'] if t['name']=='workspace-authority')
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],
    SPATIAL_XPROBE=str(build/'spatial-xprobe'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
    UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
records=[]
for index in range(3):
    target=out/('suite-'+str(index+1));target.mkdir()
    env['SPATIAL_SECURITY_EVIDENCE']=str(target/'service-logs')
    started=time.time()
    with (target/'test.log').open('xb') as log:
        result=subprocess.run(command,cwd=build,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240)
    records.append(dict(repetition=index+1,command=command,exit_code=result.returncode,start=started,end=time.time()))
    (out/'runs.json').write_text(json.dumps(records,indent=2)+'\n')
    print(host,'workspace authority suite',index+1,result.returncode,flush=True)
    if result.returncode:
        print((target/'test.log').read_text()[-7000:],flush=True)
        for p in sorted((target/'service-logs').glob('*.log')):
            value=p.read_text(errors='replace')
            if 'participant diagnostic:' in value or 'issuer-client diagnostic:' in value or 'admission-limit' in value:
                print(p.name,value[-18000:],flush=True)
        break
assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==v for n,v in manifest.items())
with tarfile.open(r/('local/authority-suite-diagnostic-'+host+'-08.tar.gz'),'w:gz') as archive:
    for p in sorted(out.rglob('*')):
        if p.is_file() and p.suffix in ('.log','.json'):
            archive.add(p,arcname='authority-suite-08/'+host+'/'+p.relative_to(out).as_posix())
print(host,'workspace authority diagnostic complete; no qualification claim',flush=True)
