import hashlib,json,os,pathlib,subprocess,tarfile
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');out=root/'local/m02-participant-barrier-baseline-01';out.mkdir();source=out/'source';source.mkdir();bindings=out/'python';bindings.mkdir()
manifest=json.loads((root/'local/participant-barrier-baseline-01.json').read_text())
with tarfile.open(root/'local/participant-barrier-baseline-01.tar') as archive:archive.extractall(source,filter='data')
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
command=['protoc','--python_out='+str(bindings)]+['-I'+str(source/'proto/spatial'/part/'v1') for part in ('core','identity','authority','workspace','security','input')]+[str(p) for p in (source/'proto').rglob('*.proto')]
result=subprocess.run(command,capture_output=True);(out/'protoc.log').write_bytes(result.stdout+result.stderr)
record={'source_files':len(manifest['files']),'commands':[{'command':command,'exit_code':result.returncode}]};assert result.returncode==0
binary=root/'local/m02-participant-session-draft-03/debug/build/identityd'
command=['python3',str(source/'tests/core/test_identity_participant_barrier.py'),str(binary),'BarrierTests.test_activation_records_original_service_and_connection','BarrierTests.test_transport_alone_cannot_activate','BarrierTests.test_owner_reads_committed_barrier_with_no_active_participants']
env=dict(os.environ,PYTHONPATH=str(bindings)+':'+str(source))
with (out/'baseline.log').open('wb') as log:result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
record['commands'].append({'command':command,'exit_code':result.returncode});record['identityd_sha256']=hashlib.sha256(binary.read_bytes()).hexdigest();(out/'run.json').write_text(json.dumps(record,indent=2)+'\n')
text=(out/'baseline.log').read_text();print(text,flush=True)
assert result.returncode==1 and 'Ran 3 tests in ' in text and 'FAILED (failures=3)' in text and 'errors=' not in text
print('Three expected missing durable-participation failures preserved; downstream assertions remain unrun',flush=True)
