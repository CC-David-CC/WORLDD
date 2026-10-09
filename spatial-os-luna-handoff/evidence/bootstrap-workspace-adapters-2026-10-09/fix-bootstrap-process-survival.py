import hashlib,json,os,pathlib,shutil,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
old=root/'local/m01-workspace-bootstrap-draft-01/source'
out=root/'local/m01-bootstrap-survival-fixed-02';out.mkdir();source=out/'source';shutil.copytree(old,source);build=out/'build'
(source/'src/workspace/authority.cpp').write_bytes((root/'local/workspace_authority.cpp').read_bytes())
(source/'tests/core/test_workspace_bootstrap.py').write_bytes((root/'local/test_workspace_bootstrap.py').read_bytes())
(source/'tests/core/test_workspace_bootstrap_races.py').write_bytes((root/'local/test_workspace_bootstrap_races.py').read_bytes())
manifest=json.loads((old/'source-manifest.json').read_text())
for name in ('src/workspace/authority.cpp','tests/core/test_workspace_bootstrap.py','tests/core/test_workspace_bootstrap_races.py'):
    manifest[name]=hashlib.sha256((source/name).read_bytes()).hexdigest()
(source/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source))
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug']),
    ('build',['cmake','--build',str(build),'--parallel','2']),
    ('workspace',['ctest','--test-dir',str(build),'-V','-R','^workspace-bootstrap$']),
    ('races',['python3',str(source/'tests/core/test_workspace_bootstrap_races.py'),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd')])]
results=[]
for name,command in commands:
    with (out/(name+'.log')).open('wb') as log:
        result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append({'name':name,'command':command,'exit_code':result.returncode})
    (out/'run.json').write_text(json.dumps({'scope':'bootstrap cancellation preserves independent process authority',
        'results':results,'source_files':len(manifest)},indent=2)+'\n');print(name,result.returncode,flush=True)
    if result.returncode:
        print((out/(name+'.log')).read_text()[-7500:],flush=True);raise SystemExit(result.returncode)
