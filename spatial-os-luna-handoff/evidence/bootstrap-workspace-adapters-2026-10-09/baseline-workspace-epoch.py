import json,os,pathlib,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
draft=root/'local/m01-bootstrap-survival-fixed-02';source=draft/'source';build=draft/'build'
out=root/'local/m02-workspace-epoch-baseline-01';out.mkdir()
test=out/'test_workspace_bootstrap_races.py';test.write_bytes((root/'local'/test.name).read_bytes())
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'))
command=['python3',str(test),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd'),
    'WorkspaceBootstrapRaceTests.test_ordinary_workspace_access_race_preserves_independent_process_authority']
with (out/'tests.log').open('wb') as log:
    result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps({'scope':'existing workspace access epoch drift must not revoke independent execution',
    'command':command,'exit_code':result.returncode},indent=2)+'\n')
print((out/'tests.log').read_text(),flush=True)
