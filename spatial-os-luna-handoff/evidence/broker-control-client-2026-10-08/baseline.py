import pathlib,subprocess,json,os
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
subprocess.run(['tar','-xf',str(p/'local/broker-control-client-inputs.tar'),'-C',str(p)],check=True)
out=p/'local/m04-broker-control-client-baseline-01';out.mkdir(exist_ok=False)
results=[]
for name,cmd in [('configure',['cmake','-S','.','-B','build','-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_SANITIZERS=OFF','-DSPATIAL_INPUT_TESTS=ON']),('build',['cmake','--build','build','--parallel','2','--target','spatial-broker-control-client-probe']),('baseline',['ctest','--test-dir','build','-V','-R','^broker-control-client$'])]:
 with (out/(name+'.log')).open('w') as f:r=subprocess.run(cmd,cwd=p,stdout=f,stderr=subprocess.STDOUT)
 results.append(dict(name=name,command=cmd,exit_code=r.returncode));print(name,r.returncode,flush=True)
 (out/'run.json').write_text(json.dumps({'results':results},indent=2)+'\n')
 if r.returncode and name!='baseline':print((out/(name+'.log')).read_text()[-5000:]);raise SystemExit(1)
