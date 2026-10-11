import hashlib,json,os,socket,subprocess,sys,time
from pathlib import Path
assert socket.gethostname().split('.')[0] in ('sb16','sb16-2')
root=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/m03-git-storage-20261011')
source=root/'source';build=root/'sanitize';output=root/sys.argv[1]
output.mkdir(exist_ok=False)
command=['python3',str(source/'tools/with_resource_delegate.py'),'--','python3',
 str(source/'tests/core/test_broker_resources.py'),str(build/'spatial-process-scope-probe'),
 str(build/'workspaced'),*sys.argv[2:],'-v']
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),
 ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',
 SPATIAL_SCOPE_EVIDENCE=str(output/'scopes'))
record=dict(host=socket.gethostname(),command=command,started=time.time(),
 source_hashes={name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in
 ['src/workspace/resources.cpp','src/workspace/store.cpp','tests/core/test_broker_resources.py']})
with (output/'transcript.log').open('wb') as log:
 result=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=115)
record.update(exit_code=result.returncode,elapsed=time.time()-record['started'])
(output/'review.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record))
print((output/'transcript.log').read_text()[-3000:])
raise SystemExit(result.returncode)
