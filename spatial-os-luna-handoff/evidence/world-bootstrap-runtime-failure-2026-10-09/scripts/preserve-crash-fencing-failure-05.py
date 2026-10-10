"""Preserve terminal failed qualification without converting it into a pass."""
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import tarfile

r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
root=r/'local/m02-supervisor-crash-fencing-01';profile=root/'debug'
manifest_path=r/'local/supervisor-crash-fencing-candidate-01.json'
manifest=json.loads(manifest_path.read_text())['files'];assert len(manifest)==294
assert all(hashlib.sha256((root/'source'/n).read_bytes()).hexdigest()==v for n,v in manifest.items())
locks=json.loads((root/'source/acceptance.lock.json').read_text())['files']
assert len(locks)==41 and all(hashlib.sha256((root/'source'/n).read_bytes()).hexdigest()==v for n,v in locks.items())
run=json.loads((profile/'run-full.json').read_text())
assert run['results'][-1]['name']=='core' and run['results'][-1]['exit_code']==8
logs={n:(profile/(n+'.log')).read_text() for n in ('fencing','focused','core')}
total=sum(sum(map(int,re.findall(r'^\d+: Ran (\d+) tests? in ',text,re.M))) for text in logs.values())
assert total==1453
core=logs['core']
failures=re.findall(r'^\d+: (?:FAIL|ERROR): (\S+) \(([^\n]+)\)',core,re.M)
failed_methods=sorted(set(method for method,case in failures))
assert len(failed_methods)==1
assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL',core)
files=[manifest_path,r/'local/supervisor-crash-fencing-driver-01/driver.log']
files += [profile/n for n in ('run-full.json','core.log','fencing.log','focused.log','harness.log')]
files += [profile/'build/Testing/Temporary'/n for n in ('LastTest.log','LastTestsFailed.log')]
files += [p for p in (profile/'server').glob('*.log') if any(n in p.name for n in failed_methods)]
timeline={}
for name in ('m02-world-handoff-02','m02-authority-fencing-01','m02-supervisor-crash-fencing-01',
             'm02-supervisor-crash-fencing-01','bootstrap-diagnostic-01','transport-diagnostic-run-05'):
    folder=r/'local'/name
    if not folder.exists(): continue
    for path in folder.glob('**/build.log'):
        timeline[str(path.relative_to(r))]=subprocess.check_output(['stat','-c','%w|%y',str(path)],text=True).strip()
out=r/'local/crash-fencing-failure-05';out.mkdir()
for path in files:
    assert path.is_file(),path
    target=out/path.relative_to(r/'local');target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(path.read_bytes())
summary=dict(host=host,source_files=294,locked_files=41,profile='debug',
    core_checks=1453,failed_test_methods=len(failed_methods),passed_test_methods=1453-len(failed_methods),
    failed_suites=(profile/'build/Testing/Temporary/LastTestsFailed.log').read_text().splitlines(),
    failed_methods=failed_methods,reported_failure_or_error_records=len(failures),
    P10='unrun_after_debug_core_failure',runtime_full='unrun_after_debug_core_failure',harness_checks=51,integrated=False,cause='unresolved',
    compiler_log_birth_and_modify_times=timeline,
    files={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*') if p.is_file()})
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(r/('local/crash-fencing-failure-'+host+'-05.tar.gz'),'w:gz') as archive:
    for path in sorted(out.rglob('*')):
        if path.is_file():archive.add(path,arcname='failure-05/'+host+'/'+path.relative_to(out).as_posix())
print(json.dumps({k:v for k,v in summary.items() if k!='files'}))
