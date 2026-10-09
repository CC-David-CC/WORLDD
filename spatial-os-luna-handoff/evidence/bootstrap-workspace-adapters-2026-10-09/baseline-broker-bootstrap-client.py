import json,os,pathlib,subprocess
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
candidate=root/'local/m01-workspace-bootstrap-draft-01/source'
old=root/'local/m01-world-bootstrap-draft-01/source'
build=root/'local/m01-world-bootstrap-focused-debug-01/build'
out=root/'local/m01-broker-bootstrap-baseline-01';out.mkdir();generated=out/'python';generated.mkdir()
test=out/'test_broker_bootstrap_client.py';test.write_bytes((candidate/'tests/core'/test.name).read_bytes())
command=['protoc','--python_out='+str(generated)]
command+=['--proto_path='+str(candidate/'proto/spatial'/part/'v1') for part in ('workspace','core','authority','identity','security')]
command+=[str(candidate/'proto/spatial/workspace/v1/broker.proto')]
with (out/'generate.log').open('wb') as log:subprocess.run(command,check=True,stdout=log,stderr=subprocess.STDOUT)
env=dict(os.environ,PYTHONPATH=str(generated)+':'+str(build/'python')+':'+str(old)+':'+str(old/'tests/core'))
command=['python3',str(test),str(build/'spatial-broker-control-client-probe')]
with (out/'tests.log').open('wb') as log:
    result=subprocess.run(command,cwd=old,env=env,stdout=log,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps({'scope':'baseline new checked-client tests against previous production client',
    'command':command,'exit_code':result.returncode},indent=2)+'\n')
print('baseline test exit',result.returncode,flush=True)
print((out/'tests.log').read_text()[-1500:],flush=True)
