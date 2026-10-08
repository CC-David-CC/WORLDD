import pathlib,os,json,subprocess,tarfile,hashlib
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
with tarfile.open(p/'local/application-access-baseline.tar') as t:t.extractall(p,filter='data')
out=p/'local/m04-application-access-baseline-04';out.mkdir(exist_ok=False);(out/'python').mkdir()
cmd=['protoc','--python_out='+str(out/'python'),*['-I'+str(p/'proto/spatial'/kind/'v1') for kind in ['security','workspace','core','authority']],str(p/'proto/spatial/workspace/v1/broker.proto')]
subprocess.run(cmd,check=True)
binary=json.loads((p/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
env=dict(os.environ,SPATIAL_TEST_XSERVER=binary['path'],PYTHONPATH=str(out/'python')+':'+str(p/'build/python')+':'+str(p),SPATIAL_SECURITY_EVIDENCE=str(out/'server'))
cmd=['python3','tests/core/test_application_access.py',*[str(p/'build'/name) for name in ['identityd','spatial-issuer-client','worldd','spatial-security-auth']]]
with (out/'test.log').open('w') as f:r=subprocess.run(cmd,cwd=p,env=env,stdout=f,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(command=cmd,binary=binary,exit_code=r.returncode,broker_sha256=hashlib.sha256((p/'build/workspaced').read_bytes()).hexdigest()),indent=2)+'\n')
print((out/'test.log').read_text()[-4500:],flush=True)
