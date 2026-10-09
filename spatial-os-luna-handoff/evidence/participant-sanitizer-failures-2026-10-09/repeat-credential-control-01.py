import hashlib,json,os,socket,subprocess
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
source=r/'local/m02-participant-fixtures-07/source';build=source.parent/'runtime-sanitize/build'
manifest=json.loads((r/'local/participant-fixtures-candidate-06.json').read_text())['files']
for n,h in manifest.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
out=r/'local/credential-control-repeat-01';out.mkdir();results=[]
for index in range(10):
    folder=out/str(index);folder.mkdir()
    env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),SPATIAL_SECURITY_EVIDENCE=str(folder/'server'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    command=['python3',str(source/'tests/core/test_world_application_control.py')]+[str(build/n) for n in ['identityd','spatial-issuer-client','worldd','spatial-security-auth']]+['WorldApplicationControlTests.test_credential_revocation_releases_native_input']
    with (folder/'test.log').open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(index=index,command=command,exit_code=run.returncode));(out/'run.json').write_text(json.dumps(dict(host=host,candidate=6,results=results),indent=2)+'\n')
    print(host,index,run.returncode,flush=True)
    if run.returncode:print((folder/'test.log').read_text(),flush=True);raise SystemExit(run.returncode)
