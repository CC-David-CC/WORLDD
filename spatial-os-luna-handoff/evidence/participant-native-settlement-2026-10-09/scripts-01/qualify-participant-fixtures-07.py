import hashlib,json,os,pathlib,socket,subprocess,tarfile
r=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
source=r/'local/m02-participant-fixtures-07/source';source.mkdir(parents=True)
with tarfile.open(r/'local/participant-fixtures-candidate-06.tar') as a:a.extractall(source,filter='data')
manifest=json.loads((r/'local/participant-fixtures-candidate-06.json').read_text())['files'];locked=json.loads((source/'acceptance.lock.json').read_text())['files']
def audit():
    for files in [manifest,locked]:
        for n,h in files.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
audit()
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb'];assert hashlib.sha256(pathlib.Path(server['path']).read_bytes()).hexdigest()==server['sha256']
for profile,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    out=source.parent/profile;out.mkdir();build=out/'build'
    env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]),SPATIAL_SECURITY_EVIDENCE=str(out/'server'),SPATIAL_EVIDENCE_DIR=str(out/'cases'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+sanitize]),('build',['cmake','--build',str(build),'--parallel','2']),('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),('focused',['ctest','--test-dir',str(build),'-V','-R','^(workspace-participant(-native|-races|-settlement)?|world-participant(-native)?|identity-participant-runtime|process-termination|workspace-authority|world-revocation-receipts|world-bootstrap)$']),('core',['ctest','--test-dir',str(build),'-V','-E','^(workspace-participant(-native|-races|-settlement)?|world-participant(-native)?|identity-participant-runtime|process-termination|workspace-authority|world-revocation-receipts|world-bootstrap)$']),('P10',['python3','tools/with_test_display.py','--','python3','tools/gate.py','--through','P10','--report',str(out/'P10.json')])]
    results=[]
    for name,command in commands:
        with (out/(name+'.log')).open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=run.returncode));(out/'run.json').write_text(json.dumps(dict(host=host,results=results,server=server,source_files=len(manifest)),indent=2)+'\n');print(host,profile,name,run.returncode,flush=True)
        if run.returncode:print((out/(name+'.log')).read_text()[-20000:],flush=True);raise SystemExit(run.returncode)
    audit()
print(host,'full combined participant qualification complete',flush=True)
