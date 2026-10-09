import hashlib,json,os,socket,subprocess,tarfile
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
root=r/'local/m02-world-freshness-02';source=root/'source';source.mkdir(parents=True)
with tarfile.open(r/'local/participant-fixtures-candidate-08.tar') as a:a.extractall(source,filter='data')
manifest=json.loads((r/'local/participant-fixtures-candidate-08.json').read_text())['files']
def audit():
    for entries in [manifest,json.loads((source/'acceptance.lock.json').read_text())['files']]:
        for n,h in entries.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
audit()
server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
selection='^(world-revision-interleave|workspace-bootstrap-owner-idle|world-bootstrap(-races)?|workspace-bootstrap(-races)?|world-authority|world-identity|identity-participant-runtime|world-revocation-receipts)$'
for profile,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    out=root/profile;out.mkdir();build=out/'build'
    env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),SPATIAL_SUT=json.dumps([str(build/'spatial-test-host')]),SPATIAL_SECURITY_EVIDENCE=str(out/'server'),SPATIAL_EVIDENCE_DIR=str(out/'cases'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+sanitize]),('build',['cmake','--build',str(build),'--parallel','2']),('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),('freshness',['ctest','--test-dir',str(build),'-V','-R',selection])]
    results=[]
    for name,command in commands:
        with (out/(name+'.log')).open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=run.returncode));(out/'run.json').write_text(json.dumps(dict(host=host,candidate=8,source_files=len(manifest),results=results),indent=2)+'\n');print(host,profile,name,run.returncode,flush=True)
        if run.returncode:print((out/(name+'.log')).read_text()[-18000:],flush=True);raise SystemExit(run.returncode)
    audit()
print(host,'world freshness focused profiles complete',flush=True)
