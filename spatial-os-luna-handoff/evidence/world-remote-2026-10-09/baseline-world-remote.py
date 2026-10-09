import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
source=root/'local/m01-adapters-source-04';build=root/'local/m01-adapters-debug-04/build'
out=root/'local/m01-world-remote-baseline-01';out.mkdir()
test=root/'local/test_world_remote.py';snapshot=out/test.name;snapshot.write_bytes(test.read_bytes())
env=dict(os.environ,PYTHONPATH=':'.join(map(str,[build/'python',source,source/'tests/core'])))
command=['python3',str(snapshot),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd')]
with (out/'tests.log').open('wb') as log:
    result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
record={'host':'sb16','command':command,'exit_code':result.returncode,
    'test_sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest(),
    'worldd_sha256':hashlib.sha256((build/'worldd').read_bytes()).hexdigest(),
    'scope':'Baseline against qualified candidate 04. Remote transport is not implemented.'}
(out/'run.json').write_text(json.dumps(record,indent=2)+'\n')
with tarfile.open(root/'local/world-remote-baseline-sb16.tar.gz','w:gz') as archive:
    for path in out.iterdir():archive.add(path,arcname='sb16/'+path.name)
print(json.dumps(record),flush=True)
print((out/'tests.log').read_text()[-6000:],flush=True)
