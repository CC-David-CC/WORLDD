import hashlib,json,os,pathlib,socket,subprocess,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
source=r/'local/m02-workspace-participant-focus-02/source';source.mkdir(parents=True)
with tarfile.open(r/'local/workspace-participant-candidate-02.tar') as archive:archive.extractall(source,filter='data')
manifest=json.loads((r/'local/workspace-participant-candidate-02.json').read_text())['files']
def audit():
    for name,digest in manifest.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
audit()
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb'];assert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest()==server['sha256']
for profile,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    out=source.parent/profile;out.mkdir();build=out/'build'
    env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),SPATIAL_SECURITY_EVIDENCE=str(out/'server'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+sanitize]),('build',['cmake','--build',str(build),'--parallel','1']),('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),('focused',['ctest','--test-dir',str(build),'-V','-R','^(workspace-participant(-native)?|world-participant(-native)?|identity-participant-runtime|process-termination)$'])]
    results=[]
    for name,command in commands:
        with (out/(name+'.log')).open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=run.returncode));(out/'run.json').write_text(json.dumps(dict(host=host,results=results,server=server,source_files=len(manifest)),indent=2)+'\n');print(host,profile,name,run.returncode,flush=True)
        if run.returncode:print((out/(name+'.log')).read_text()[-16000:],flush=True);raise SystemExit(run.returncode)
    audit()
print(host,'both-profile focused participation complete',flush=True)
