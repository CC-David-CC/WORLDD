import hashlib,json,os,pathlib,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');source=root/'local/m02-participant-barrier-draft-03/source';build=root/'local/m02-participant-barrier-draft-03/debug/build'
out=root/'local/m02-participant-barrier-client-focused-04';out.mkdir();test=root/'local/test_identity_participant_barrier_client_04.py'
manifest=json.loads((root/'local/participant-barrier-candidate-04.json').read_text())
for name,digest in manifest['files'].items():
    path=test if name=='tests/core/test_identity_participant_barrier_client.py' else source/name
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest,name
commands=[('client',['python3',str(test),str(build/'identityd'),str(build/'spatial-issuer-client')])]
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'))
record={'source_files':len(manifest['files']),'identityd_sha256':hashlib.sha256((build/'identityd').read_bytes()).hexdigest(),'results':[]}
for label,command in commands:
    with (out/(label+'.log')).open('wb') as log:result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    record['results'].append({'name':label,'command':command,'exit_code':result.returncode});(out/'run.json').write_text(json.dumps(record,indent=2)+'\n');print(label,result.returncode,flush=True)
    if result.returncode:print((out/(label+'.log')).read_text()[-10000:],flush=True);raise SystemExit(result.returncode)
print('Extended barrier client tests complete; full qualification pending',flush=True)
