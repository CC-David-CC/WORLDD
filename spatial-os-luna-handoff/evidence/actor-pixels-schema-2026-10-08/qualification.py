import os,json,subprocess,pathlib,socket
p=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
out=p/'local/m10-actor-pixels-schema-qualification-01';out.mkdir(exist_ok=False)
native=json.loads((p/'evidence/world-application-control-2026-10-08'/socket.gethostname()/'m04-world-application-control-debug-02/run.json').read_text())['binary']
env=dict(os.environ,SPATIAL_TEST_XSERVER=native['path'],PYTHONPATH=str(p/'build/python')+':'+str(p),SPATIAL_SUT=json.dumps([str(p/'build/spatial-test-host')]),SPATIAL_XPROBE=str(p/'build/spatial-xprobe'),SPATIAL_EVIDENCE_DIR=str(out/'cases'))
commands=[('audit',['python3','local/actor-pixels-schema-audit.py']),('configure',['cmake','-S','.','-B','build','-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_SANITIZERS=OFF','-DSPATIAL_INPUT_TESTS=ON']),('build',['cmake','--build','build','--parallel','2']),('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),('core',['ctest','--test-dir','build','-V','-R','^(core-protocol|actor-observation|render-runtime)$']),('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(out/'P10.json')])]
results=[]
for name,cmd in commands:
 with (out/(name+'.log')).open('w') as log:r=subprocess.run(cmd,cwd=p,env=env,stdout=log,stderr=subprocess.STDOUT)
 results.append(dict(name=name,command=cmd,exit_code=r.returncode))
 (out/'run.json').write_text(json.dumps(dict(host=socket.gethostname(),base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=p,text=True).strip(),results=results,binary=native),indent=2)+'\n')
 print(name,r.returncode,flush=True)
 if r.returncode:print((out/(name+'.log')).read_text()[-5000:]);raise SystemExit(r.returncode)
