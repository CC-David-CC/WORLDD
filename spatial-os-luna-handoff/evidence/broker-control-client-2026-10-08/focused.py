import pathlib,subprocess,json,os
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
subprocess.run(['tar','-xf',str(p/'local/broker-control-client-inputs.tar'),'-C',str(p)],check=True)
out=p/'local/m04-broker-control-client-focused-01';out.mkdir(exist_ok=False)
binary=json.loads((p/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']['path']
env=dict(os.environ,SPATIAL_TEST_XSERVER=binary,SPATIAL_SECURITY_EVIDENCE=str(out/'server'))
results=[]
for name,cmd in [('configure',['cmake','-S','.','-B','build','-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_SANITIZERS=OFF','-DSPATIAL_INPUT_TESTS=ON']),('build',['cmake','--build','build','--parallel','2']),('focused',['ctest','--test-dir','build','-V','-R','^broker-control-client'])]:
 with (out/(name+'.log')).open('w') as f:r=subprocess.run(cmd,cwd=p,env=env,stdout=f,stderr=subprocess.STDOUT)
 results.append(dict(name=name,command=cmd,exit_code=r.returncode));print(name,r.returncode,flush=True)
 (out/'run.json').write_text(json.dumps({'results':results},indent=2)+'\n')
 if r.returncode:print((out/(name+'.log')).read_text()[-6000:]);raise SystemExit(1)
