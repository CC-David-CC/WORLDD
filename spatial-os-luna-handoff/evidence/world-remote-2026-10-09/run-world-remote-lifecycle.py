import hashlib,json,os,pathlib,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
base=root/'local/m01-world-remote-draft-01';source=base/'source';build=base/'build'
out=root/'local/m01-world-remote-lifecycle-01';out.mkdir()
for name in ('test_world_remote_security.py','test_world_remote_lifecycle.py'):
    (out/name).write_bytes((root/'local'/name).read_bytes())
env=dict(os.environ,PYTHONPATH=':'.join(map(str,[build/'python',source,source/'tests/core'])))
command=['python3',str(out/'test_world_remote_lifecycle.py'),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd')]
with (out/'tests.log').open('wb') as log:
    result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
(out/'run.json').write_text(json.dumps({'host':'sb16','command':command,'exit_code':result.returncode,
    'tests':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob('*.py')},
    'worldd_sha256':hashlib.sha256((build/'worldd').read_bytes()).hexdigest()},indent=2)+'\n')
print('remote lifecycle exit',result.returncode,flush=True)
print((out/'tests.log').read_text()[-6500:],flush=True)
raise SystemExit(result.returncode)
