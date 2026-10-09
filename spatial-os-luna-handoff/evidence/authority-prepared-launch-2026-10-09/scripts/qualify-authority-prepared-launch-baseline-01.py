import hashlib,json,socket,subprocess,tarfile
from pathlib import Path
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff');host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
out=r/'local/m02-authority-prepared-launch-baseline-01';source=out/'source';source.mkdir(parents=True)
with tarfile.open(r/'local/authority-prepared-launch-baseline-01.tar') as a:a.extractall(source,filter='data')
manifest=json.loads((r/'local/authority-prepared-launch-baseline-01.json').read_text())['files']
for n,h in manifest.items():assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
build=out/'build'
commands=[('configure',['cmake','-S',str(source),'-B',str(build),'-G','Ninja','-DCMAKE_BUILD_TYPE=Debug']),('build',['cmake','--build',str(build),'--target','spatial-process-scope-probe','--parallel','2']),('baseline',['systemd-run','--user','--wait','--pipe','--collect','--unit=spatial-prepared-baseline-01','--property=Delegate=yes','--setenv=PYTHONPATH='+str(build/'python')+':'+str(source),'/usr/bin/python3',str(source/'tests/core/test_authority_prepared_launch.py'),str(build/'spatial-process-scope-probe'),'PreparedLaunchTests.test_prepared_identity_and_membership_precede_child_execution','PreparedLaunchTests.test_stopped_prepared_scope_cannot_execute'])]
results=[]
for name,command in commands:
    with (out/(name+'.log')).open('wb') as log:run=subprocess.run(command,cwd=source,stdout=log,stderr=subprocess.STDOUT)
    results.append(dict(name=name,command=command,exit_code=run.returncode));(out/'run.json').write_text(json.dumps(dict(host=host,results=results),indent=2)+'\n');print(host,name,run.returncode,flush=True)
    if run.returncode!=(1 if name=='baseline' else 0):print((out/(name+'.log')).read_text()[-10000:],flush=True);raise SystemExit(2)
text=(out/'baseline.log').read_text();assert 'Ran 2 tests' in text and 'FAILED (failures=2)' in text and 'INVALID_STATE' in text
print(host,'two expected prepared-boundary failures preserved',flush=True)
