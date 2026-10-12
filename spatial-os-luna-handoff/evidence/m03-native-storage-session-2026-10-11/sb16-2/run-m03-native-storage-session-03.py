import hashlib,json,os,re,socket,subprocess,time
from pathlib import Path
assert socket.gethostname().split('.')[0] in ('sb16','sb16-2')
base=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root=base/'m03-git-storage-20261011';source=root/'source';build=root/'sanitize'
output=root/'native-storage-session-03';output.mkdir(exist_ok=False)
old=(base/'m02-phase-gate-02/runtime-sanitize/build/CTestTestfile.cmake').read_text()
server=Path(re.search(r'--setenv=SPATIAL_TEST_XSERVER=([^"\n]+)',old).group(1));assert server.is_file()
env=dict(os.environ,SPATIAL_TEST_XSERVER=str(server),SPATIAL_SECURITY_EVIDENCE=str(output/'native'),
 SPATIAL_SCOPE_EVIDENCE=str(output/'scopes'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
 UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
files=[source/'CMakeLists.txt',source/'acceptance.lock.json']
for name in ('src','proto','config','harness','tests','native','tools'):
 files.extend(p for p in (source/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
record=dict(status='running',host=socket.gethostname(),results=[],started=time.time(),
 source_hashes={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)},
 xserver=str(server),xserver_sha256=hashlib.sha256(server.read_bytes()).hexdigest())
def save():(output/'review.json').write_text(json.dumps(record,indent=2)+'\n')
save()
def run(label,command,timeout):
 with (output/(label+'.log')).open('wb') as log:reply=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
 record['results'].append(dict(name=label,command=command,exit_code=reply.returncode));save()
 print(json.dumps(record['results'][-1]),flush=True)
 if reply.returncode:
  record.update(status='failed',failed_suite=label,elapsed=time.time()-record['started']);save()
  raise SystemExit(reply.returncode)
run('configure',['cmake','-S',str(source),'-B',str(build)],60)
run('build-native',['cmake','--build',str(build),'--parallel','2','--target','spatial-participant-runtime-probe','spatial-revoke','workspaced','spatial-authority-supervisor','spatial-session-application-probe'],120)
for name in ['session-storage-owner']:
 run(name,['ctest','--test-dir',str(build),'-R','^'+name+'$','--output-on-failure','-V'],280)
for name,sha in record['source_hashes'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==sha,name
record.update(status='passed',elapsed=time.time()-record['started']);save()
