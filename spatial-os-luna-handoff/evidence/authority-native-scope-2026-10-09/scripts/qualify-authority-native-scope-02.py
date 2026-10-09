import hashlib,json,os,socket,subprocess,tarfile
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=r/'local/m02-authority-native-scope-02';source=out/'source';source.mkdir(parents=True)
with tarfile.open(r/'local/authority-native-scope-candidate-02.tar') as a:a.extractall(source,filter='data')
manifest=json.loads((r/'local/authority-native-scope-candidate-02.json').read_text())['files']
def audit():
    for n,h in manifest.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
    for n,h in json.loads((source/'acceptance.lock.json').read_text())['files'].items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
audit();server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb'];assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
for profile,sanitize in [('debug','OFF'),('runtime-sanitize','ON')]:
    target=out/profile;target.mkdir();build=target/'build'
    env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),SPATIAL_SECURITY_EVIDENCE=str(target/'server'),ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug','-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+sanitize]),('build',['cmake','--build',str(build),'--parallel','2']),('harness',['python3','-m','unittest','discover','-s','tests/harness','-t','.','-q']),('scope',['ctest','--test-dir',str(build),'-V','-R','^(authority-native-scope|native-application-launch|native-security-identity)$'])]
    results=[]
    for name,command in commands:
        with (target/(name+'.log')).open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
        results.append(dict(name=name,command=command,exit_code=run.returncode));(target/'run.json').write_text(json.dumps(dict(host=host,server=server,source_files=len(manifest),results=results),indent=2)+'\n');print(host,profile,name,run.returncode,flush=True)
        if run.returncode:print((target/(name+'.log')).read_text()[-16000:],flush=True);raise SystemExit(run.returncode)
    audit()
print(host,'native authority scope focused profiles complete',flush=True)
