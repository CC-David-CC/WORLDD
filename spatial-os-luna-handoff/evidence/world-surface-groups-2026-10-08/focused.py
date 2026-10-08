import pathlib,os,json,subprocess,tarfile
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
with tarfile.open(p/'local/surface-groups-source.tar') as t:t.extractall(p,filter='data')
out=p/'local/m04-surface-groups-focused-01';out.mkdir(exist_ok=False)
binary=json.loads((p/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
env=dict(os.environ,SPATIAL_TEST_XSERVER=binary['path'],PYTHONPATH=str(p/'build/python')+':'+str(p),SPATIAL_SECURITY_EVIDENCE=str(out/'server'))
results=[]
for name,cmd in [('configure',['cmake','-S','.','-B','build','-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_SANITIZERS=OFF','-DSPATIAL_INPUT_TESTS=ON']),('build',['cmake','--build','build','--parallel','2']),('focused',['python3','tests/native/test_world_surface_groups.py',str(p/'build/spatial-security-auth')])]:
 with (out/(name+'.log')).open('w') as f:r=subprocess.run(cmd,cwd=p,env=env,stdout=f,stderr=subprocess.STDOUT)
 results.append(dict(name=name,command=cmd,exit_code=r.returncode))
 (out/'run.json').write_text(json.dumps(dict(results=results,binary=binary),indent=2)+'\n')
 print(name,r.returncode,flush=True)
 if r.returncode:print((out/(name+'.log')).read_text()[-5000:]);raise SystemExit(1)
