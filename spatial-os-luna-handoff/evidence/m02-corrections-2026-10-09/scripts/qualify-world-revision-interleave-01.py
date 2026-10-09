import json,os,socket,subprocess
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
source=r/'local/m02-participant-fixtures-07/source';out=r/'local/world-revision-interleave-01';out.mkdir();results=[]
for profile in ['debug','runtime-sanitize']:
    build=source.parent/profile/'build';target=out/profile;target.mkdir()
    env=dict(os.environ,PYTHONPATH=str(source/'tests/core')+':'+str(build/'python')+':'+str(source),SPATIAL_SECURITY_EVIDENCE=str(target/'server'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    command=['python3',str(r/'local/test_world_revision_interleave.py')]+[str(build/n) for n in ['identityd','spatial-issuer-client','worldd']]
    with (target/'test.log').open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(profile=profile,command=command,exit_code=run.returncode));print(host,profile,run.returncode,flush=True)
    print((target/'test.log').read_text(),flush=True)
(out/'run.json').write_text(json.dumps(dict(host=host,candidate=6,results=results),indent=2)+'\n')
