"""Focused regressions for the Git cache parent and memory throttle change."""
import hashlib,json,os,socket,subprocess,sys,time
from pathlib import Path
assert socket.gethostname().split('.')[0] in ('sb16','sb16-2')
root=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local/m03-git-storage-20261011')
build=root/'sanitize';source=root/'source';output=root/(sys.argv[1] if len(sys.argv)>1 else 'regressions')
output.mkdir(exist_ok=False)
names=sys.argv[2:] or ['workspace-git','broker-resources','process-scope-resources','process-termination','workspace-context','worldd-lifecycle']
env=dict(os.environ,ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',
         SPATIAL_SCOPE_EVIDENCE=str(output/'scopes'))
files=[source/'CMakeLists.txt',source/'acceptance.lock.json']
for name in ('src','proto','config','harness','tests','native','tools'):
    files.extend(p for p in (source/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
record=dict(status='running',host=socket.gethostname(),started=time.time(),results=[],
            source_hashes={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)})
def save():(output/'review.json').write_text(json.dumps(record,indent=2)+'\n')
save()
for name in names:
    command=['ctest','--test-dir',str(build),'-R','^'+name+'$','--output-on-failure','-V']
    start=time.monotonic()
    with (output/(name+'.log')).open('wb') as log:
        result=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=220)
    record['results'].append(dict(name=name,command=command,exit_code=result.returncode,seconds=time.monotonic()-start));save()
    print(json.dumps(record['results'][-1]),flush=True)
record['status']='passed' if all(r['exit_code']==0 for r in record['results']) else 'failed'
record['elapsed']=time.time()-record['started'];save()
raise SystemExit(0 if record['status']=='passed' else 1)
