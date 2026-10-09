import hashlib,json,os,pathlib,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');source=root/'local/m02-participant-barrier-draft-01/source';build=root/'local/m02-participant-barrier-draft-01/debug/build'
out=root/'local/m02-participant-barrier-focused-02';out.mkdir();test=root/'local/test_identity_participant_barrier_02.py'
manifest=json.loads((root/'local/participant-barrier-candidate-02.json').read_text())
for name,digest in manifest['files'].items():
    path=test if name=='tests/core/test_identity_participant_barrier.py' else source/name
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest,name
commands=[('barriers',['python3',str(test),str(build/'identityd')]),('regression',['ctest','--test-dir',str(build),'-V','-R','^(identity-participant-session(-client)?|identity-participants|identity-participant-client|identity-keys|identity-issuer|identity-tls|identity-bootstrap|identity-enrollment|identity-revocation)$'])]
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'))
record={'source_files':len(manifest['files']),'identityd_sha256':hashlib.sha256((build/'identityd').read_bytes()).hexdigest(),'results':[]}
for label,command in commands:
    with (out/(label+'.log')).open('wb') as log:result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    record['results'].append({'name':label,'command':command,'exit_code':result.returncode});(out/'run.json').write_text(json.dumps(record,indent=2)+'\n');print(label,result.returncode,flush=True)
    if result.returncode:print((out/(label+'.log')).read_text()[-10000:],flush=True);raise SystemExit(result.returncode)
print('Corrected durable barrier tests complete; primary review, checked client and full qualification pending',flush=True)
