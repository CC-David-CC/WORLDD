import hashlib,json,os,pathlib,re,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
out=root/'local/m02-participant-session-focused-02';out.mkdir()
source=root/'local/m02-participant-session-draft-01/source';build=root/'local/m02-participant-session-draft-01/debug/build'
manifest=json.loads((root/'local/participant-session-candidate-02.json').read_text())
test=root/'local/test_participant_session_corrected_02.py'
for name,digest in manifest['files'].items():
    path=test if name=='tests/core/test_identity_participant_session.py' else source/name
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest,name
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'))
commands=[('sessions',['python3',str(test),str(build/'identityd')]),
    ('regression',['ctest','--test-dir',str(build),'-V','-R','^(identity-participants|identity-participant-client|identity-keys|identity-issuer|identity-tls)$'])]
record={'source_files':len(manifest['files']),'binary_source':'participant-session-candidate-01.json','tested_source':'participant-session-candidate-02.json','identityd_sha256':hashlib.sha256((build/'identityd').read_bytes()).hexdigest(),'results':[]}
for name,command in commands:
    with (out/(name+'.log')).open('wb') as log:result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    record['results'].append({'name':name,'command':command,'exit_code':result.returncode})
    (out/'run.json').write_text(json.dumps(record,indent=2)+'\n');print(name,result.returncode,flush=True)
    if result.returncode:print((out/(name+'.log')).read_text()[-8000:]);raise SystemExit(result.returncode)
text=(out/'sessions.log').read_text();assert 'Ran 20 tests in ' in text and 'OK' in text and 'skipped=' not in text
text=(out/'regression.log').read_text();counts=[int(n) for n in re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M)]
assert len(counts)==5 and sum(counts)==88 and '100% tests passed, 0 tests failed out of 5' in text and 'skipped=' not in text
print('20 service-session and88 regression checks passed; checked-client and full qualification pending',flush=True)
