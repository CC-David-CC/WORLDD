import hashlib,json,os,pathlib,shutil,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
old=root/'local/m01-bootstrap-survival-fixed-02/source'
out=root/'local/m02-workspace-epoch-fixed-02';out.mkdir();source=out/'source';shutil.copytree(old,source);build=out/'build'
manifest=json.loads((old/'source-manifest.json').read_text())
for local,name in [('workspace_authority.cpp','src/workspace/authority.cpp'),
    ('test_workspace_bootstrap_races.py','tests/core/test_workspace_bootstrap_races.py')]:
    (source/name).write_bytes((root/'local'/local).read_bytes());manifest[name]=hashlib.sha256((source/name).read_bytes()).hexdigest()
(source/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source))
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug']),
    ('build',['cmake','--build',str(build),'--parallel','2']),
    ('workspace-regressions',['ctest','--test-dir',str(build),'-V','-R','^(workspace-bootstrap|workspace-authority|broker-bootstrap-client)$']),
    ('races',['python3',str(source/'tests/core/test_workspace_bootstrap_races.py'),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd')])]
results=[]
for name,command in commands:
    with (out/(name+'.log')).open('wb') as log:
        result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append({'name':name,'command':command,'exit_code':result.returncode})
    (out/'run.json').write_text(json.dumps({'scope':'workspace issuer epoch changes must not revoke independent process authority',
        'results':results,'source_files':len(manifest)},indent=2)+'\n');print(name,result.returncode,flush=True)
    if result.returncode:
        print((out/(name+'.log')).read_text()[-7500:],flush=True);raise SystemExit(result.returncode)
