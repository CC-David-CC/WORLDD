import os,json,pathlib,subprocess,socket
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
out=p/'local/m04-application-access-prerequisite-01';out.mkdir(exist_ok=False)
binary=json.loads((p/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
env=dict(os.environ,SPATIAL_TEST_XSERVER=binary['path'],PYTHONPATH=str(p/'build/python')+':'+str(p),SPATIAL_SUT=json.dumps([str(p/'build/spatial-test-host')]),SPATIAL_XPROBE=str(p/'build/spatial-xprobe'),SPATIAL_EVIDENCE_DIR=str(out/'cases'))
results=[]
for name,cmd in [('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(out/'P10.json')])]:
 with (out/(name+'.log')).open('w') as f:r=subprocess.run(cmd,cwd=p,env=env,stdout=f,stderr=subprocess.STDOUT)
 results.append(dict(name=name,command=cmd,exit_code=r.returncode))
 (out/'run.json').write_text(json.dumps(dict(host=socket.gethostname(),results=results,binary=binary),indent=2)+'\n')
 print(name,r.returncode,flush=True)
 if r.returncode:print((out/(name+'.log')).read_text()[-4000:]);raise SystemExit(r.returncode)
