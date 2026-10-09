import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');assert socket.gethostname().split('.')[0]=='sb16'
out=root/'local/m02-participant-baseline-02';out.mkdir();source=out/'source';source.mkdir();bindings=out/'python';bindings.mkdir()
manifest=json.loads((root/'local/participant-baseline-01.json').read_text())
with tarfile.open(root/'local/participant-baseline-01.tar') as archive:
    for member in archive.getmembers():
        name=pathlib.PurePosixPath(member.name);assert member.isfile() and not name.is_absolute() and '..' not in name.parts
    archive.extractall(source,filter='data')
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
command=['protoc','--python_out='+str(bindings)]+['-I'+str(source/'proto/spatial'/part/'v1') for part in ('core','identity','authority','workspace','security','input')]
command += [str(p) for p in (source/'proto').rglob('*.proto')]
with (out/'protoc.log').open('wb') as log:result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
assert result.returncode==0
build=root/'local/m01-world-remote-debug-02/build'
tests=['test_empty_registry_is_explicit_and_owner_protected','test_owner_registers_distinct_service_without_actor_grants','test_registry_requires_owner_proof']
command=['python3',str(source/'tests/core/test_identity_participants.py'),str(build/'identityd')]+['ParticipantTests.'+name for name in tests]
env=dict(os.environ,PYTHONPATH=str(bindings)+':'+str(source))
with (out/'baseline.log').open('wb') as log:result=subprocess.run(command,env=env,cwd=source,stdout=log,stderr=subprocess.STDOUT,timeout=40)
text=(out/'baseline.log').read_text();record={'command':command,'exit_code':result.returncode,'tests_requested':tests,
    'source_files':len(manifest['files']),'world_remote_identityd_sha256':hashlib.sha256((build/'identityd').read_bytes()).hexdigest()}
(out/'run.json').write_text(json.dumps(record,indent=2)+'\n');print(text[-6000:],flush=True)
assert result.returncode==1 and 'FAILED (failures=3)' in text,'Baseline did not reach the expected unsupported-operation assertions'
