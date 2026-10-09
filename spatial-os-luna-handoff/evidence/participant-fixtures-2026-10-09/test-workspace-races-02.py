import hashlib,json,os,pathlib,socket,subprocess,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');assert socket.gethostname().split('.')[0]=='sb16'
source=r/'local/m02-participant-fixtures-02/source';source.mkdir(parents=True)
with tarfile.open(r/'local/participant-fixtures-candidate-02.tar') as a:a.extractall(source,filter='data')
manifest=json.loads((r/'local/participant-fixtures-candidate-02.json').read_text())['files']
base=r/'local/m02-workspace-participant-focus-02/source';prior=json.loads((r/'local/workspace-participant-candidate-02.json').read_text())['files']
for n,h in manifest.items():
    assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
    if n.startswith(('src/','proto/')):assert h==prior[n] and hashlib.sha256((base/n).read_bytes()).hexdigest()==h,n
build=base.parent/'debug/build';out=source.parent/'debug';out.mkdir()
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source));results=[]
for name in ['workspace_participant_races','workspace_participant']:
    command=['python3',str(source/'tests/core'/('test_'+name+'.py'))]+[str(build/n) for n in ['identityd','spatial-issuer-client','worldd']]
    with (out/(name+'.log')).open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=120)
    results.append(dict(name=name,command=command,exit_code=run.returncode));(out/'run.json').write_text(json.dumps(dict(host='sb16',results=results,source_files=len(manifest),production_binaries='workspace02 Debug'),indent=2)+'\n');print(name,run.returncode,flush=True)
    if run.returncode:print((out/(name+'.log')).read_text(),flush=True);raise SystemExit(run.returncode)
