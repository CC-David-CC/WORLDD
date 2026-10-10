"""Capture the newly observed broker teardown failure with classified transport logs."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
prior=r/'local/transport-diagnostic-run-05';source=prior/'source';build=prior/'build'
manifest=json.loads((r/'local/transport-diagnostic-05.json').read_text())['files']
assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==v for n,v in manifest.items())
run=json.loads((prior/'run.json').read_text())['results']
assert run[0]['name']=='controls' and run[0]['exit_code']==0
base=r/'local/m02-supervisor-fencing-01/source'
for name in ('tests/core/test_actor_pixels.py','tests/core/test_application_access.py'):
    assert (source/name).read_bytes()==(base/name).read_bytes(),name
out=r/'local/pixel-teardown-diagnostic-06';out.mkdir()
listed=json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1']))
command=next(t['command'] for t in listed['tests'] if t['name']=='actor-pixels')
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],
    SPATIAL_XPROBE=str(build/'spatial-xprobe'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
    UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
records=[]
for index in range(11):
    target=out/('repeat-'+str(index+1).zfill(2));target.mkdir()
    # Preserve the original full-suite order once after ten exact-method checks.
    selected=command+(['ActorPixelTests.test_live_paint_changes_the_next_pixel_observation'] if index<10 else [])
    env['SPATIAL_SECURITY_EVIDENCE']=str(target/'service-logs')
    with (target/'test.log').open('xb') as log:
        result=subprocess.run(selected,cwd=build,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240)
    records.append(dict(repetition=index+1,command=selected,exit_code=result.returncode))
    (out/'repetitions.json').write_text(json.dumps(records,indent=2)+'\n')
    print(host,'pixel teardown diagnostic',index+1,result.returncode,flush=True)
    if result.returncode:
        print((target/'test.log').read_text()[-6000:],flush=True)
        for p in sorted((target/'service-logs').glob('*.log')):
            value=p.read_text(errors='replace')
            if 'diagnostic:' in value:print(p.name,value[-18000:],flush=True)
        break
assert all(hashlib.sha256((source/n).read_bytes()).hexdigest()==v for n,v in manifest.items())
with tarfile.open(r/('local/pixel-teardown-diagnostic-'+host+'-06.tar.gz'),'w:gz') as archive:
    for p in sorted(out.rglob('*')):
        if p.is_file() and p.suffix in ('.log','.json'):
            archive.add(p,arcname='pixel-teardown-diagnostic-06/'+host+'/'+p.relative_to(out).as_posix())
print(host,'pixel teardown diagnostic complete; no qualification claim',flush=True)
