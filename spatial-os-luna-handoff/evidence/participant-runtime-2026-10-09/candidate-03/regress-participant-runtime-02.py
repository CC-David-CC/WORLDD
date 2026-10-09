import hashlib,json,pathlib,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');draft=root/'local/m02-participant-runtime-draft-02';source=draft/'source';build=draft/'debug/build'
manifest=json.loads((root/'local/participant-runtime-candidate-02.json').read_text())
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
out=draft/'regression';out.mkdir()
command=['ctest','--test-dir',str(build),'-V','-R','^identity-participant(s|-client|-session(-client)?|-barrier(-client)?)$']
with (out/'tests.log').open('wb') as log:r=subprocess.run(command,cwd=source,stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(command=command,exit_code=r.returncode),indent=2)+'\n');print('worker-base-regression',r.returncode,flush=True)
if r.returncode:print((out/'tests.log').read_text()[-5000:],flush=True)
raise SystemExit(r.returncode)
