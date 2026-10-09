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
prior=1266474 if host=='sb16' else 1047059
# Wait for that exact qualifier's sanitizer build before using two compiler jobs.
while True:
    path=r/'local/m02-supervisor-fencing-01/runtime-sanitize/run.json'
    try:results=json.loads(path.read_text())['results']
    except (FileNotFoundError,json.JSONDecodeError):results=[]
    built=next((v for v in results if v['name']=='build'),None)
    if built:
        assert built['exit_code']==0;break
    os.kill(prior,0)
    assert b'qualify-supervisor-fencing-01.py' in Path('/proc',str(prior),'cmdline').read_bytes(), 'Queued prerequisite is no longer running'
    time.sleep(2)
out=r/'local/world-bootstrap-diagnostic-run-01';source=out/'source';source.mkdir(parents=True)
with tarfile.open(r/'local/world-bootstrap-diagnostic-01.tar') as archive:archive.extractall(source,filter='data')
manifest=json.loads((r/'local/world-bootstrap-diagnostic-01.json').read_text())['files']
assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==v for n,v in manifest.items())
build=out/'build'
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
env=dict(os.environ,SPATIAL_TEST_XSERVER=server['path'],PYTHONPATH=str(build/'python')+':'+str(source),
    ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
results=[]
for name,command in [('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS=ON']),
                     ('build',['cmake','--build',str(build),'--parallel','2'])]:
    with (out/(name+'.log')).open('xb') as log:result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(name=name,command=command,exit_code=result.returncode))
    (out/'build-run.json').write_text(json.dumps(results,indent=2)+'\n')
    print(host,name,result.returncode,flush=True);assert result.returncode==0
listed=json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1']))
command=next(t['command'] for t in listed['tests'] if t['name']=='world-bootstrap')
command += ['WorldBootstrapTests.test_public_entry_reports_unbound_template_without_action_authority']
records=[]
for index in range(40):
    target=out/('repeat-'+str(index+1).zfill(2));target.mkdir()
    env['SPATIAL_SECURITY_EVIDENCE']=str(target/'service-logs')
    with (target/'test.log').open('xb') as log:result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=60)
    entry=dict(repetition=index+1,command=command,exit_code=result.returncode)
    records.append(entry);(out/'repetitions.json').write_text(json.dumps(records,indent=2)+'\n')
    print(host,'bootstrap diagnostic repetition',index+1,result.returncode,flush=True)
    if result.returncode:
        print((target/'test.log').read_text()[-5000:],flush=True)
        for p in sorted((target/'service-logs').glob('*.log')):
            print(p.name,p.read_text(errors='replace')[-3000:],flush=True)
        break
assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==v for n,v in manifest.items())
with tarfile.open(r/('local/world-bootstrap-diagnostic-'+host+'-01.tar.gz'),'w:gz') as archive:
    for p in sorted(out.rglob('*')):
        if p.is_file() and 'source' not in p.relative_to(out).parts and 'build' not in p.relative_to(out).parts and p.suffix in ('.log','.json'):
            archive.add(p,arcname='diagnostic-01/'+host+'/'+p.relative_to(out).as_posix())
print(host,'diagnostic complete; no qualification claim',flush=True)
