import pathlib,json,subprocess,os
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
out=p/'local/m04-application-control-lease-authority-fixture-01';out.mkdir(exist_ok=False)
results=[]
for build,label in [('build','debug'),('build-sanitize','runtime-sanitize')]:
 env=dict(os.environ,ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
 cmd=['ctest','--test-dir',build,'-V','-R','^workspace-authority$']
 with (out/(label+'.log')).open('w') as f:r=subprocess.run(cmd,cwd=p,env=env,stdout=f,stderr=subprocess.STDOUT)
 results.append(dict(profile=label,command=cmd,exit_code=r.returncode))
 (out/'run.json').write_text(json.dumps({'results':results},indent=2)+'\n')
 print(label,r.returncode,flush=True)
 if r.returncode:print((out/(label+'.log')).read_text()[-5000:]);raise SystemExit(1)
