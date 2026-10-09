import hashlib,json,os,pathlib,socket,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
source=root/'local/m02-participant-session-source-03'
manifest=json.loads((root/'local/participant-session-candidate-03.json').read_text())
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
fix=json.loads((root/'local/workspace-relay-fix-01.json').read_text())
script=root/'local/test_workspace_relay_fixed.py'
assert hashlib.sha256(script.read_bytes()).hexdigest()==fix['changes']['tests/core/test_workspace_authority.py']['after']
out=root/'local/workspace-relay-fix-validation-01';out.mkdir()
results=[]
for label,test,case,expected in [('runtime-sanitize','test_workspace_relay_idle_baseline.py','WorkspaceAuthorityTests.test_retry_revocation_uses_live_owner_after_setup_exceeds_idle_timeout',1),('debug','test_workspace_relay_fixed.py',None,0),('runtime-sanitize','test_workspace_relay_fixed.py',None,0)]:
    build=root/('local/m02-participant-session-'+label+'-03/build')
    env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    command=['python3',str(root/'local'/test),str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd')]+([case] if case else [])
    name=label+('-baseline' if case else '-fixed')
    with (out/(name+'.log')).open('wb') as log:result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(name=name,command=command,exit_code=result.returncode,expected_exit_code=expected,binaries={n:hashlib.sha256((build/n).read_bytes()).hexdigest() for n in ['identityd','spatial-issuer-client','worldd','workspaced']},test_sha256=hashlib.sha256((root/'local'/test).read_bytes()).hexdigest()))
    (out/'run.json').write_text(json.dumps(dict(host=host,results=results),indent=2)+'\n');print(host,name,result.returncode,flush=True)
    if result.returncode!=expected:print((out/(name+'.log')).read_text(),flush=True);raise SystemExit(1)
print('Baseline and full corrected workspace suites finished',flush=True)
