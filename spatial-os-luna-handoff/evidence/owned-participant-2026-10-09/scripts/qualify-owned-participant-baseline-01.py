import hashlib,json,os,pathlib,socket,subprocess,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
out=r/'local/m02-owned-participant-baseline-01';source=out/'source';source.mkdir(parents=True)
with tarfile.open(r/'local/owned-participant-baseline-01.tar') as a:a.extractall(source,filter='data')
manifest=json.loads((r/'local/owned-participant-baseline-01.json').read_text())['files']
def audit():
    for files in [manifest,json.loads((source/'acceptance.lock.json').read_text())['files']]:
        for n,h in files.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
audit()
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest()==server['sha256']
target=out/'debug';target.mkdir();build=target/'build'
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_SECURITY_EVIDENCE=str(target/'server'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_SANITIZERS=OFF']),
    ('build',['cmake','--build',str(build),'--target','spatial-authority-launch-probe','spatial-security-auth','identityd','spatial-issuer-client','spatial-key','--parallel','2']),
    ('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),
    ('baseline',['ctest','--test-dir',str(build),'-V','-R','^authority-owned-participant$'])]
results=[]
for name,command in commands:
    with (target/(name+'.log')).open('wb') as log:p=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(name=name,command=command,exit_code=p.returncode));(target/'run.json').write_text(json.dumps(dict(host=host,results=results,source_files=len(manifest),server=server),indent=2)+'\n')
    print(host,name,p.returncode,flush=True)
    if p.returncode:
        print((target/(name+'.log')).read_text()[-12000:],flush=True)
        if name!='baseline':raise SystemExit(p.returncode)
        break
audit()
