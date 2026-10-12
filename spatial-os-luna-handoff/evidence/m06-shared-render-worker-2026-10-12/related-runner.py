import hashlib,json,os,socket,subprocess,time
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop-m06-20261012/spatial-os-luna-handoff')
out=r/'local/m06-shared-render-worker-06';out.mkdir(exist_ok=False)
record=dict(host=socket.gethostname(),pid=os.getpid(),status='running',commands=[])
def save():(out/'run.json').write_text(json.dumps(record,indent=2)+'\n')
def run(name,cmd,env):
 with (out/(name+'.log')).open('wb') as log:p=subprocess.run(cmd,cwd=r,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600)
 record['commands'].append(dict(name=name,command=cmd,exit_code=p.returncode));save();print(name,p.returncode,flush=True)
 if p.returncode:raise RuntimeError(name)
save()
try:
 for profile in ('Debug','ASan-UBSan'):
  build=r/'local/m06-build'/profile
  env=dict(os.environ,SPATIAL_TEST_XSERVER=str(r/'local/m06-presentation-input-03'/profile/'server-launch/Xvfb'),SPATIAL_SECURITY_EVIDENCE=str(out/profile/'logs'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
  run(profile+'-tests',['ctest','--test-dir',str(build),'-V','-R','^(presentation-input-bindings|native-compositor_identity)$','--output-junit',str(out/(profile+'-results.xml'))],env)
 record['status']='passed_needs_review'
except BaseException as e:record.update(status='failed',error=repr(e));raise
finally:save()
