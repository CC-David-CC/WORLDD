import hashlib,json,os,socket,subprocess,tarfile
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=r/'local/m02-authority-native-scope-baseline-01';source=out/'source';source.mkdir(parents=True)
with tarfile.open(r/'local/authority-native-scope-baseline-01.tar') as a:a.extractall(source,filter='data')
manifest=json.loads((r/'local/authority-native-scope-baseline-01.json').read_text())['files']
for n,h in manifest.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
build=out/'build';server=json.loads((r/'local/m04-surface-identity-build-01/run.json').read_text())['binaries']['Xvfb'];assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest()==server['sha256']
env=dict(os.environ,PYTHONPATH=str(build/'python')+':'+str(source),SPATIAL_TEST_XSERVER=server['path'],SPATIAL_SECURITY_EVIDENCE=str(out/'server'))
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug']),('build',['cmake','--build',str(build),'--target','spatial-native-scope-probe','spatial-security-auth','--parallel','2']),('baseline',['python3',str(source/'tests/native/test_authority_native_scope.py'),str(build/'spatial-native-scope-probe'),str(build/'spatial-security-auth'),'NativeScopeTests.test_broker_stop_closes_descendants_preserves_independent_application','NativeScopeTests.test_compositor_stop_closes_only_owned_connections'])]
results=[]
for name,command in commands:
    with (out/(name+'.log')).open('wb') as log:run=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(name=name,command=command,exit_code=run.returncode));(out/'run.json').write_text(json.dumps(dict(host=host,results=results),indent=2)+'\n');print(host,name,run.returncode,flush=True)
    if run.returncode != (1 if name=='baseline' else 0): print((out/(name+'.log')).read_text()[-10000:],flush=True);raise SystemExit(2)
assert (out/'baseline.log').read_text().count('Native authority ownership is not implemented')==2
print(host,'two expected unsupported-boundary failures preserved',flush=True)
