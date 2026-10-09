import hashlib,json,os,pathlib,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
draft=root/'local/m01-world-bootstrap-draft-01';source=draft/'source';build=draft/'build'
out=root/'local/m01-world-bootstrap-races-02';out.mkdir()
test=root/'local/test_world_bootstrap_races.py'
saved=out/test.name;saved.write_bytes(test.read_bytes())
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'))
command=['python3',str(saved),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd')]
with (out/'tests.log').open('wb') as log:
    result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
report={'scope':'isolated world draft; real issuer and revocation relay', 'command':command,
    'exit_code':result.returncode,'test_sha256':hashlib.sha256(saved.read_bytes()).hexdigest(),
    'binaries':{name:hashlib.sha256((build/name).read_bytes()).hexdigest() for name in ('identityd','spatial-issuer-client','worldd')}}
(out/'run.json').write_text(json.dumps(report,indent=2)+'\n')
print((out/'tests.log').read_text(),flush=True)
raise SystemExit(result.returncode)
