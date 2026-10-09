import hashlib,json,os,pathlib,socket,subprocess,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host=='sb16'
source=r/'local/m02-workspace-participant-02/source';source.mkdir(parents=True)
with tarfile.open(r/'local/workspace-participant-candidate-02.tar') as archive:archive.extractall(source,filter='data')
manifest=json.loads((r/'local/workspace-participant-candidate-02.json').read_text())['files']
for name,digest in manifest.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest
base=r/'local/m02-workspace-participant-01/source';prior=json.loads((r/'local/workspace-participant-candidate-01.json').read_text())['files']
for name,digest in prior.items():
    assert hashlib.sha256((base/name).read_bytes()).hexdigest()==digest
    if name.startswith(('src/','proto/')):assert manifest[name]==digest
build=base.parent/'debug/build';out=source.parent/'debug';out.mkdir()
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb'];assert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest()==server['sha256']
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_SECURITY_EVIDENCE=str(out/'server'))
results=[]
for name,file,args in [('native','test_workspace_participant_native.py',['spatial-security-auth']),('workspace','test_workspace_participant.py',[])]:
    command=['python3',str(source/'tests/core'/file)]+[str(build/n) for n in ['identityd','spatial-issuer-client','worldd',*args]]
    with (out/(name+'.log')).open('wb') as log:result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(name=name,command=command,exit_code=result.returncode))
    (out/'run.json').write_text(json.dumps(dict(host=host,results=results,server=server,binaries={n:hashlib.sha256((build/n).read_bytes()).hexdigest() for n in ['workspaced','identityd','spatial-issuer-client','spatial-security-auth']}),indent=2)+'\n')
    print(host,name,result.returncode,flush=True)
    if result.returncode:print((out/(name+'.log')).read_text(),flush=True);raise SystemExit(result.returncode)
print('Native lease settlement and all six workspace checks passed')
