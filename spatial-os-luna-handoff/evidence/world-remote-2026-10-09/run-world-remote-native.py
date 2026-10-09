import hashlib,json,os,pathlib,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
base=root/'local/m01-world-remote-draft-01';source=base/'source';build=base/'build'
out=root/'local/m01-world-remote-native-01';out.mkdir()
test=out/'test_world_remote_native.py';test.write_bytes((root/'local'/test.name).read_bytes())
server=json.loads((root/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
env=dict(os.environ,PYTHONPATH=':'.join(map(str,[build/'python',source,source/'tests/core'])),
    SPATIAL_TEST_XSERVER=server['path'],SPATIAL_SECURITY_EVIDENCE=str(out/'server'))
command=['python3',str(test),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd'),str(build/'spatial-security-auth')]
with (out/'tests.log').open('wb') as log:
    result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
(out/'run.json').write_text(json.dumps({'host':'sb16','command':command,'exit_code':result.returncode,
    'test_sha256':hashlib.sha256(test.read_bytes()).hexdigest(),'server':server,
    'worldd_sha256':hashlib.sha256((build/'worldd').read_bytes()).hexdigest()},indent=2)+'\n')
print('remote native exit',result.returncode,flush=True)
print((out/'tests.log').read_text()[-6500:],flush=True)
raise SystemExit(result.returncode)
