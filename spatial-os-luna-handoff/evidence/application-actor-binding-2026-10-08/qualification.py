import os,json,subprocess,pathlib,socket
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
out=p/'local/m04-actor-binding-native-sanitize-03'
out.mkdir(exist_ok=False)
binary=json.loads((p/'local/m04-actor-binding-sanitize-build-03/run.json').read_text())['binaries']['Xvfb']
env=dict(os.environ,SPATIAL_TEST_XSERVER=binary['path'],PYTHONPATH=str(p/'build/python')+':'+str(p),SPATIAL_SECURITY_EVIDENCE=str(out/'server'),SPATIAL_SECURITY_TEST_CORE_ONLY='1',ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
results=[]
for name in ['actor_binding','application_input','device_administration','global_isolation','selection_isolation','extension_isolation','resource_isolation','security_identity']:
 cmd=['python3','tests/native/test_'+name+'.py',str(p/'build/spatial-security-auth')]
 with (out/(name+'.log')).open('w') as log:
  r=subprocess.run(cmd,cwd=p,env=env,stdout=log,stderr=subprocess.STDOUT)
 results.append({'name':name,'command':cmd,'exit_code':r.returncode})
 (out/'run.json').write_text(json.dumps({'host':socket.gethostname(),'results':results,'binary':binary,'ASAN_OPTIONS':env['ASAN_OPTIONS'],'UBSAN_OPTIONS':env['UBSAN_OPTIONS']},indent=2)+'\n')
 print(name,r.returncode,flush=True)
 if r.returncode: print((out/(name+'.log')).read_text()[-4000:]); raise SystemExit(1)
subprocess.run(['python3','tools/build_xserver.py','--evidence-dir','local/m04-actor-binding-build-03'],cwd=p,check=True)
import os,json,subprocess,pathlib,socket
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
binary=json.loads((p/'local/m04-actor-binding-build-03/run.json').read_text())['binaries']['Xvfb']
for build,sanitize,label in [('build','OFF','debug'),('build-sanitize','ON','runtime-sanitize')]:
 out=p/('local/m04-actor-binding-'+label+'-03')
 out.mkdir(exist_ok=False)
 env=dict(os.environ,SPATIAL_TEST_XSERVER=binary['path'],PYTHONPATH=str(p/build/'python')+':'+str(p),SPATIAL_SECURITY_EVIDENCE=str(out/'server'),SPATIAL_EVIDENCE_DIR=str(out/'cases'),SPATIAL_SUT=json.dumps([str(p/build/'spatial-test-host')]),SPATIAL_XPROBE=str(p/build/'spatial-xprobe'))
 commands=[('configure',['cmake','-S','.','-B',build,'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_SANITIZERS='+sanitize,'-DSPATIAL_INPUT_TESTS=ON']),('build',['cmake','--build',build,'--parallel','2']),('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),('core',['ctest','--test-dir',build,'-V']),('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(out/'P10.json')])]
 results=[]
 for name,cmd in commands:
  with (out/(name+'.log')).open('w') as log:
   r=subprocess.run(cmd,cwd=p,env=env,stdout=log,stderr=subprocess.STDOUT)
  results.append({'name':name,'command':cmd,'exit_code':r.returncode})
  (out/'run.json').write_text(json.dumps({'host':socket.gethostname(),'results':results,'binary':binary},indent=2)+'\n')
  print(label,name,r.returncode,flush=True)
  if r.returncode: print((out/(name+'.log')).read_text()[-6000:]); raise SystemExit(1)

