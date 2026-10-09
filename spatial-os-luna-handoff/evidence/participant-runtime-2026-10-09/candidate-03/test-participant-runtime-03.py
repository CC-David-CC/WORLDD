import hashlib,json,os,pathlib,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');base=root/'local/m02-participant-runtime-draft-02';build=base/'debug/build'
old=json.loads((root/'local/participant-runtime-candidate-02.json').read_text());new=json.loads((root/'local/participant-runtime-candidate-03.json').read_text());test='tests/core/test_identity_participant_runtime.py'
assert [n for n in old['files'] if old['files'][n]!=new['files'][n]]==[test]
for name,digest in old['files'].items():assert hashlib.sha256((base/'source'/name).read_bytes()).hexdigest()==digest,name
out=root/'local/m02-participant-runtime-tests-03';out.mkdir();script=out/'test_identity_participant_runtime.py'
with tarfile.open(root/'local/participant-runtime-candidate-03.tar') as archive:data=archive.extractfile(test).read()
assert hashlib.sha256(data).hexdigest()==new['files'][test];script.write_bytes(data)
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(base/'source')+':'+str(base/'source/tests/core'))
command=['python3',str(script),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'spatial-participant-runtime-probe')]
with (out/'tests.log').open('wb') as log:r=subprocess.run(command,cwd=base/'source',env=env,stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(command=command,exit_code=r.returncode,test_sha256=new['files'][test],unchanged_compiled_inputs=True,binaries={n:hashlib.sha256((build/n).read_bytes()).hexdigest() for n in ['identityd','spatial-issuer-client','spatial-participant-runtime-probe']}),indent=2)+'\n');print('participant-runtime03',r.returncode,flush=True)
if r.returncode:print((out/'tests.log').read_text()[-6000:],flush=True)
raise SystemExit(r.returncode)
