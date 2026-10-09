import hashlib,json,os,pathlib,shutil,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0]=='sb16'
previous=root/'local/m01-bootstrap-adapter-baseline-01'
out=root/'local/m01-workspace-bootstrap-baseline-01';out.mkdir()
inputs=out/'inputs';inputs.mkdir()
for path in (previous/'inputs').iterdir():
    if path.is_file(): shutil.copyfile(path,inputs/path.name)
shutil.copyfile(root/'local/test_workspace_bootstrap-draft.py',inputs/'test_workspace_bootstrap.py')
shutil.copyfile(root/'tests/core/test_workspace_authority.py',inputs/'test_workspace_authority.py')
command=['python3',str(inputs/'test_workspace_bootstrap.py'),str(root/'build/identityd'),str(root/'build/spatial-issuer-client'),str(root/'build/worldd')]
env=dict(os.environ,PYTHONPATH=str(previous/'python')+':'+str(inputs)+':'+str(root))
with (out/'baseline.log').open('wb') as log:
    result=subprocess.run(command,cwd=out,env=env,stdout=log,stderr=subprocess.STDOUT)
report={'scope':'proposed authenticated workspace bootstrap against existing production binaries',
        'command':command,'test_exit_code':result.returncode,
        'schema_generator_report':str(previous/'run.json'),
        'inputs':{path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs.iterdir() if path.is_file()},
        'binaries':{name:hashlib.sha256((root/'build'/name).read_bytes()).hexdigest() for name in ('identityd','spatial-issuer-client','worldd','workspaced')}}
(out/'run.json').write_text(json.dumps(report,indent=2)+'\n')
print((out/'baseline.log').read_text()[-3800:],flush=True)
print('Workspace baseline exit',result.returncode,flush=True)
