import os,json,subprocess,pathlib,socket,hashlib
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
binary=json.loads((p/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
for build,sanitize,label in [('build','OFF','debug'),('build-sanitize','ON','runtime-sanitize')]:
 out=p/('local/m04-broker-control-client-'+label+'-01');out.mkdir(exist_ok=False)
 env=dict(os.environ,SPATIAL_TEST_XSERVER=binary['path'],PYTHONPATH=str(p/build/'python')+':'+str(p),SPATIAL_SECURITY_EVIDENCE=str(out/'server'),SPATIAL_EVIDENCE_DIR=str(out/'cases'),SPATIAL_SUT=json.dumps([str(p/build/'spatial-test-host')]),SPATIAL_XPROBE=str(p/build/'spatial-xprobe'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
 commands=[('configure',['cmake','-S','.','-B',build,'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_SANITIZERS='+sanitize,'-DSPATIAL_INPUT_TESTS=ON']),('build',['cmake','--build',build,'--parallel','2']),('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),('core',['ctest','--test-dir',build,'-V']),('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(out/'P10.json')])]
 results=[]
 for name,cmd in commands:
  with (out/(name+'.log')).open('w') as log:r=subprocess.run(cmd,cwd=p,env=env,stdout=log,stderr=subprocess.STDOUT)
  results.append({'name':name,'command':cmd,'exit_code':r.returncode})
  (out/'run.json').write_text(json.dumps({'host':socket.gethostname(),'results':results,'binary':binary,'broker_sha256':hashlib.sha256((p/build/'workspaced').read_bytes()).hexdigest(),'ASAN_OPTIONS':env['ASAN_OPTIONS'],'UBSAN_OPTIONS':env['UBSAN_OPTIONS']},indent=2)+'\n')
  print(label,name,r.returncode,flush=True)
  if r.returncode:print((out/(name+'.log')).read_text()[-7000:]);raise SystemExit(1)

out=p/'local/m04-broker-control-client-native-sanitize-01';out.mkdir(exist_ok=False)
binary=json.loads((p/'local/m04-surface-identity-sanitize-build-01/run.json').read_text())['binaries']['Xvfb']
env=dict(os.environ,SPATIAL_TEST_XSERVER=binary['path'],PYTHONPATH=str(p/'build/python')+':'+str(p),SPATIAL_SECURITY_EVIDENCE=str(out/'server'),SPATIAL_SECURITY_TEST_CORE_ONLY='1',ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
cmd=['python3','tests/core/test_broker_control_client_live.py',*[str(p/'build'/name) for name in ['identityd','spatial-issuer-client','worldd','spatial-security-auth']]]
with (out/'test.log').open('w') as f:r=subprocess.run(cmd,cwd=p,env=env,stdout=f,stderr=subprocess.STDOUT)
(out/'run.json').write_text(json.dumps(dict(command=cmd,binary=binary,exit_code=r.returncode,ASAN_OPTIONS=env['ASAN_OPTIONS'],UBSAN_OPTIONS=env['UBSAN_OPTIONS'],GLX=False),indent=2)+'\n')
print('native-sanitize',r.returncode,flush=True)
if r.returncode:print((out/'test.log').read_text()[-7000:]);raise SystemExit(1)

