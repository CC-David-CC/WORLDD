import hashlib,json,os,pathlib,socket,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');assert socket.gethostname().split('.')[0]=='sb16'
out=root/'local/m02-participant-session-baseline-01';out.mkdir();source=out/'source';source.mkdir();bindings=out/'python';bindings.mkdir()
manifest=json.loads((root/'local/participant-session-baseline-01.json').read_text())
with tarfile.open(root/'local/participant-session-baseline-01.tar') as archive:
    for member in archive.getmembers():
        name=pathlib.PurePosixPath(member.name);assert member.isfile() and not name.is_absolute() and '..' not in name.parts
    archive.extractall(source,filter='data')
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
command=['protoc','--python_out='+str(bindings)]+['-I'+str(source/'proto/spatial'/part/'v1') for part in ('core','identity','authority','workspace','security','input')]
command += [str(p) for p in (source/'proto').rglob('*.proto')]
with (out/'protoc.log').open('wb') as log:result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
assert result.returncode==0
build=root/'local/m02-participant-debug-02/build'
summary=json.loads((root/'local/participant-summary-debug-02.json').read_text())
assert hashlib.sha256((build/'identityd').read_bytes()).hexdigest()==summary['debug']['binaries']['identityd']
tests=['test_registered_service_proof_binds_issuer_process_and_tls_connection','test_proof_from_one_connection_cannot_authenticate_another','test_management_pipe_cannot_create_service_session']
command=['python3',str(source/'tests/core/test_identity_participant_session.py'),str(build/'identityd')]+['ParticipantSessionTests.'+name for name in tests]
env=dict(os.environ,PYTHONPATH=str(bindings)+':'+str(source))
with (out/'baseline.log').open('wb') as log:result=subprocess.run(command,env=env,cwd=source,stdout=log,stderr=subprocess.STDOUT,timeout=45)
text=(out/'baseline.log').read_text();record={'command':command,'exit_code':result.returncode,'tests_requested':tests,'source_files':len(manifest['files']),'registry_candidate_identityd_sha256':summary['debug']['binaries']['identityd']}
(out/'run.json').write_text(json.dumps(record,indent=2)+'\n');print(text[-7000:],flush=True)
assert result.returncode==1 and 'FAILED (failures=3)' in text,'Baseline did not reach the expected unsupported-operation assertions'
