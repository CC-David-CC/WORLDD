import hashlib,json,os,pathlib,socket,subprocess,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
# Avoid overlapping builds with the timed relay matrix. Do not restart that matrix here.
for profile in ('debug', 'runtime-sanitize'):
    completed=json.loads((r/'local/m02-participant-relay-01'/profile/'run-full.json').read_text())
    assert len(completed['results'])==6 and all(x['exit_code']==0 for x in completed['results'])
out=r/'local/m02-authority-supervisor-01';source=out/'source';source.mkdir(parents=True)
with tarfile.open(r/'local/authority-supervisor-candidate-01.tar') as a:a.extractall(source,filter='data')
manifest=json.loads((r/'local/authority-supervisor-candidate-01.json').read_text())['files']
def audit():
    for files in [manifest,json.loads((source/'acceptance.lock.json').read_text())['files']]:
        for n,h in files.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
audit()
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest()==server['sha256']
bindings=['python-protobuf','security-python-protobuf','issuer-python-protobuf','key-python-protobuf','grants-python-protobuf']
pattern='^(identity-(launch-authorization|supervisor-registry|keys|issuer|enrollment|bootstrap|revocation|participants|participant-(client|incarnation|channel|runtime|barrier|barrier-client|session|session-client))|issuer-client|authority-(participant-relay|owned-participant|launch-coordinator|prepared-launch|process-scope|native-scope))$'
for profile,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    target=out/profile;target.mkdir();build=target/'build'
    env=dict(os.environ,SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),SPATIAL_SECURITY_EVIDENCE=str(target/'server'),PYTHONPATH=str(build/'python')+':'+str(source),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+sanitize]),
        ('build',['cmake','--build',str(build),'--parallel','2']),
        ('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),
        ('supervisor',['ctest','--test-dir',str(build),'-V','-R','^authority-supervisor$']),
        ('focused',['ctest','--test-dir',str(build),'-V','-R',pattern])]
    results=[]
    for name,command in commands:
        with (target/(name+'.log')).open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=run.returncode));(target/'run.json').write_text(json.dumps(dict(host=host,results=results,source_files=len(manifest),build=str(build)),indent=2)+'\n')
        print(host,profile,name,run.returncode,flush=True)
        if run.returncode:print((target/(name+'.log')).read_text()[-16000:],flush=True);raise SystemExit(run.returncode)
    audit()
print(host,'production authority supervisor focused qualification complete',flush=True)
