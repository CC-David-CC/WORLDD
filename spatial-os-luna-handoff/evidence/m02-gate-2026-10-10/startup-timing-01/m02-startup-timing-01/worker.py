import json,os,subprocess,sys,time
from pathlib import Path
out=Path(sys.argv[1]);source=Path(sys.argv[2]);build=Path(sys.argv[3])
deadline=time.monotonic()+25
while not (out/'trace-ready').exists():
 if time.monotonic()>deadline:raise RuntimeError('Tracer not ready')
 time.sleep(.01)
records=[]
for t in json.loads((out/'tests.json').read_bytes()):
 env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_SECURITY_EVIDENCE=str(out/'native'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
 env.pop('LSAN_OPTIONS',None)
 for prop in t['properties']:
  if prop['name']=='ENVIRONMENT':
   for setting in prop['value']:
    k,v=setting.split('=',1);env[k]=v
 entry=dict(suite=t['name'],started_ns=time.monotonic_ns(),argv=t['command'])
 with (out/(t['name']+'.log')).open('xb') as log:
  p=subprocess.run(t['command'],cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=70)
 entry.update(ended_ns=time.monotonic_ns(),exit_code=p.returncode);records.append(entry)
 (out/'results.json').write_text(json.dumps(records,indent=2)+'\n')
 print(t['name'],p.returncode,flush=True)
 if p.returncode:break
raise SystemExit(any(r['exit_code'] for r in records))
