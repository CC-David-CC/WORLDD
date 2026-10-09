"""Test the original suite order with unchanged diagnostic binaries and deadlines."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile

r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
prior=r/'local/world-bootstrap-diagnostic-run-01'
source=prior/'source';build=prior/'build'
manifest=json.loads((r/'local/world-bootstrap-diagnostic-01.json').read_text())['files']
assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==v for n,v in manifest.items())
completed=json.loads((prior/'repetitions.json').read_text())
assert len(completed)==40 and all(v['exit_code']==0 for v in completed)
out=r/'local/world-bootstrap-suite-diagnostic-02';out.mkdir()
listed=json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1']))
command=next(t['command'] for t in listed['tests'] if t['name']=='world-bootstrap')
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
         UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
records=[]
for index in range(8):
    target=out/('suite-'+str(index+1).zfill(2));target.mkdir()
    env['SPATIAL_SECURITY_EVIDENCE']=str(target/'service-logs')
    with (target/'test.log').open('xb') as log:
        result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
    records.append(dict(repetition=index+1,command=command,exit_code=result.returncode))
    (out/'repetitions.json').write_text(json.dumps(records,indent=2)+'\n')
    print(host,'full bootstrap diagnostic suite',index+1,result.returncode,flush=True)
    if result.returncode:
        print((target/'test.log').read_text()[-10000:],flush=True)
        for p in sorted((target/'service-logs').glob('*.log')):
            text=p.read_text(errors='replace')
            if 'participant diagnostic:' in text or 'identity service disabled' in text:
                print(p.name,text[-5000:],flush=True)
        break
assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==v for n,v in manifest.items())
with tarfile.open(r/('local/world-bootstrap-suite-diagnostic-'+host+'-02.tar.gz'),'w:gz') as archive:
    for p in sorted(out.rglob('*')):
        if p.is_file() and p.suffix in ('.log','.json'):
            archive.add(p,arcname='suite-diagnostic-02/'+host+'/'+p.relative_to(out).as_posix())
print(host,'suite diagnostic complete; no qualification claim',flush=True)
