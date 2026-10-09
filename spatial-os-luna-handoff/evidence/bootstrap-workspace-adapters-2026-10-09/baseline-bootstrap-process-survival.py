import hashlib,json,os,pathlib,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
draft=root/'local/m01-workspace-bootstrap-draft-01';source=draft/'source';build=draft/'build'
out=root/'local/m01-bootstrap-survival-baseline-01';out.mkdir()
test=out/'test_workspace_bootstrap_races.py';test.write_bytes((root/'local'/test.name).read_bytes())
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'))
command=['python3',str(test),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd'),
    'WorkspaceBootstrapRaceTests.test_credential_revoked_during_credential_reply_preserves_authorized_process',
    'WorkspaceBootstrapRaceTests.test_credential_revoked_during_actor_reply_preserves_authorized_process']
with (out/'tests.log').open('wb') as log:
    result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps({'scope':'credential loss must not stop independent process authority',
    'command':command,'exit_code':result.returncode,'test_sha256':hashlib.sha256(test.read_bytes()).hexdigest()},indent=2)+'\n')
print((out/'tests.log').read_text(),flush=True)
