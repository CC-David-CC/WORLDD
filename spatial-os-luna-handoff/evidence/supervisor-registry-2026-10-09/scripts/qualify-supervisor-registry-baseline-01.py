import hashlib,json,os,pathlib,socket,subprocess,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2');out=r/'local/m02-supervisor-registry-baseline-01';source=out/'source';source.mkdir(parents=True)
with tarfile.open(r/'local/supervisor-registry-baseline-01.tar') as a:a.extractall(source,filter='data')
manifest=json.loads((r/'local/supervisor-registry-baseline-01.json').read_text())['files']
for n,h in manifest.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
driver=out/'baseline_driver.py';driver.write_text('import runpy,sys,unittest\nscope=runpy.run_path(sys.argv.pop(1),run_name="counterexample")\nsuite=unittest.TestSuite([scope["SupervisorRegistryTests"]("test_owner_binds_exact_registration_through_production_client")])\nraise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())\n')
build=out/'build';env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source)+':'+str(source/'tests/core'))
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug']),
    ('build',['cmake','--build',str(build),'--target','identityd','spatial-issuer-client','python-protobuf','security-python-protobuf','issuer-python-protobuf','key-python-protobuf','grants-python-protobuf','--parallel','2']),
    ('baseline',['python3',str(driver),str(source/'tests/core/test_supervisor_registry.py'),str(build/'identityd'),str(build/'spatial-issuer-client')])]
results=[]
for name,command in commands:
    with (out/(name+'.log')).open('wb') as log:p=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(name=name,command=command,exit_code=p.returncode));(out/'run.json').write_text(json.dumps(dict(host=host,results=results),indent=2)+'\n')
    print(host,name,p.returncode,flush=True)
    if name!='baseline' and p.returncode:print((out/(name+'.log')).read_text()[-12000:],flush=True);raise SystemExit(p.returncode)
text=(out/'baseline.log').read_text();print(text,flush=True)
assert results[-1]['exit_code']==1 and 'FAILED (failures=1)' in text and 'Issuer operation is required' in text
print(host,'missing supervisor registration boundary reproduced',flush=True)
