import hashlib,json,os,pathlib,socket,subprocess,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
source=r/'local/m02-supervisor-registry-01/source';out=r/'local/m02-supervisor-registry-02';out.mkdir()
manifest=json.loads((r/'local/supervisor-registry-candidate-01.json').read_text())['files']
def audit():
    for files in [manifest,json.loads((source/'acceptance.lock.json').read_text())['files']]:
        for n,h in files.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
audit()
prior=json.loads((source.parent/'debug/run.json').read_text());log=(source.parent/'debug/focused.log').read_text()
assert [x['exit_code'] for x in prior['results']]==[0,0,0,8]
assert 'FileNotFoundError' in log and 'spatial-issuer-client-probe' in log and 'FAILED (errors=1)' in log
bindings=['python-protobuf','security-python-protobuf','issuer-python-protobuf','key-python-protobuf','grants-python-protobuf']
pattern='^(identity-(supervisor-registry|issuer|enrollment|bootstrap|revocation|participants|participant-(client|incarnation|runtime|barrier|barrier-client|session|session-client))|issuer-client)$'
for profile,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    target=out/profile;target.mkdir();build=source.parent/'debug/build' if profile=='debug' else target/'build'
    env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_SANITIZERS='+sanitize]),
        ('build',['cmake','--build',str(build),'--target','identityd','spatial-issuer-client','spatial-participant-runtime-probe','spatial-issuer-client-probe',*bindings,'--parallel','2']),
        ('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),
        ('focused',['ctest','--test-dir',str(build),'-V','-R',pattern])]
    results=[]
    for name,command in commands:
        with (target/(name+'.log')).open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=run.returncode));(target/'run.json').write_text(json.dumps(dict(host=host,results=results,source_files=len(manifest),build=str(build)),indent=2)+'\n')
        print(host,profile,name,run.returncode,flush=True)
        if run.returncode:print((target/(name+'.log')).read_text()[-16000:],flush=True);raise SystemExit(run.returncode)
    audit()
print(host,'supervisor registry focused qualification complete',flush=True)
