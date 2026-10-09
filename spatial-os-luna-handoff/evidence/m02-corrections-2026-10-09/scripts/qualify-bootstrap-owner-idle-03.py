import hashlib,json,os,socket,subprocess
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
source=r/'local/m02-participant-fixtures-07/source';out=r/'local/bootstrap-owner-idle-03';out.mkdir()
manifest=json.loads((r/'local/participant-fixtures-candidate-06.json').read_text())['files']
for n,h in manifest.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
results=[]
for profile in ['debug','runtime-sanitize']:
    target=out/profile;target.mkdir();build=source.parent/profile/'build'
    env=dict(os.environ,PYTHONPATH=str(source/'tests/core')+':'+str(build/'python')+':'+str(source),SPATIAL_SECURITY_EVIDENCE=str(target/'server'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    command=['python3',str(r/'local/test_workspace_bootstrap_owner_idle.py')]+[str(build/n) for n in ['identityd','spatial-issuer-client','worldd']]
    with (target/'test.log').open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(profile=profile,command=command,exit_code=run.returncode));print(host,profile,run.returncode,flush=True)
    assert run.returncode==1 and 'Issuer TLS stream closed' in (target/'test.log').read_text()
(out/'run.json').write_text(json.dumps(dict(host=host,expected_failure=True,results=results),indent=2)+'\n')
