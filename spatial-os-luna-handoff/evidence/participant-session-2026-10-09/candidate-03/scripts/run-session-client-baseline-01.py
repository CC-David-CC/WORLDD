import hashlib,json,os,pathlib,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');out=root/'local/m02-session-client-baseline-01';out.mkdir()
source=root/'local/m02-participant-session-draft-01/source';build=root/'local/m02-participant-session-draft-01/debug/build'
test=root/'local/test_session_client_baseline_01.py'
command=['python3',str(test),str(build/'identityd'),str(build/'spatial-issuer-client'),'SessionClientTests.test_checked_client_completes_real_registered_service_proof']
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'))
with (out/'test.log').open('wb') as log:result=subprocess.run(command,env=env,cwd=source,stdout=log,stderr=subprocess.STDOUT,timeout=30)
record={'command':command,'exit_code':result.returncode,'test_sha256':hashlib.sha256(test.read_bytes()).hexdigest(),'binaries':{name:hashlib.sha256((build/name).read_bytes()).hexdigest() for name in ('identityd','spatial-issuer-client')}}
(out/'run.json').write_text(json.dumps(record,indent=2)+'\n');text=(out/'test.log').read_text();print(text,flush=True)
assert result.returncode==1 and 'FAILED (failures=1)' in text and 'Issuer reply has the wrong outcome' in text
