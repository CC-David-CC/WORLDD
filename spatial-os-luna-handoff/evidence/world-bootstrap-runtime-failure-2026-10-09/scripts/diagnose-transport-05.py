"""Build passive diagnostics, verify fault classification, then exercise unchanged failures."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
completed=json.loads((r/'local/m02-supervisor-crash-fencing-01/runtime-sanitize/run.json').read_text())['results']
assert next(v for v in completed if v['name']=='build')['exit_code']==0
out=r/'local/transport-diagnostic-run-05';source=out/'source';source.mkdir(parents=True)
with tarfile.open(r/'local/transport-diagnostic-05.tar') as archive:archive.extractall(source,filter='data')
manifest=json.loads((r/'local/transport-diagnostic-05.json').read_text())['files']
def audit():
    assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==v for n,v in manifest.items())
audit();build=out/'build'
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),
    SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),
    ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
results=[]
for name,command in [('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS=ON']),
                     ('build',['cmake','--build',str(build),'--parallel','2'])]:
    with (out/(name+'.log')).open('xb') as log:
        result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(name=name,command=command,exit_code=result.returncode))
    (out/'build-run.json').write_text(json.dumps(results,indent=2)+'\n')
    print(host,name,result.returncode,flush=True)
    if result.returncode:
        print((out/(name+'.log')).read_text()[-5000:],flush=True);raise SystemExit(result.returncode)
listed=json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1']))
commands={t['name']:t['command'] for t in listed['tests']}
control=list(commands['world-bootstrap'])
index=control.index(str(source/'tests/core/test_world_bootstrap.py'))
control[index:index+1]=[str(r/'local/test_transport_diagnostic_controls_05.py'),str(source)]
records=[]
def run(name,command):
    target=out/name;target.mkdir()
    env['SPATIAL_SECURITY_EVIDENCE']=str(target/'service-logs')
    with (target/'test.log').open('xb') as log:
        result=subprocess.run(command,cwd=build,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
    records.append(dict(name=name,command=command,exit_code=result.returncode))
    (out/'run.json').write_text(json.dumps(dict(diagnostic_only=True,qualification=False,results=records),indent=2)+'\n')
    print(host,name,result.returncode,flush=True)
    if result.returncode:
        print((target/'test.log').read_text()[-7000:],flush=True)
        for p in sorted((target/'service-logs').glob('*.log')):
            value=p.read_text(errors='replace')
            if 'participant diagnostic:' in value or 'issuer-client diagnostic:' in value or p.name.endswith('tls-0.log'):
                print(p.name,value[-15000:],flush=True)
    return result.returncode
failed=run('controls',control)
if not failed:
    for index in range(6):
        for name in ('workspace-bootstrap-owner-idle','world-bootstrap'):
            failed=run('repeat-'+str(index+1).zfill(2)+'-'+name,commands[name])
            if failed:break
        if failed:break
audit()
with tarfile.open(r/('local/transport-diagnostic-'+host+'-05.tar.gz'),'w:gz') as archive:
    for p in sorted(out.rglob('*')):
        relative=p.relative_to(out)
        if p.is_file() and 'source' not in relative.parts and 'build' not in relative.parts and p.suffix in ('.log','.json'):
            archive.add(p,arcname='transport-diagnostic-05/'+host+'/'+relative.as_posix())
print(host,'transport diagnostic complete',failed,'; no qualification claim',flush=True)
