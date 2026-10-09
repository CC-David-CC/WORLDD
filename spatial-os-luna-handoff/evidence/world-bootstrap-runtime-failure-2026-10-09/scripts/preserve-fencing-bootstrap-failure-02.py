import hashlib
import json
from pathlib import Path
import re
import socket
import tarfile
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host=='sb16'
out=r/'local/m02-authority-fencing-01'
manifest=json.loads((r/'local/authority-fencing-candidate-01.json').read_text())['files']
assert len(manifest)==292
assert all(hashlib.sha256((out/'source'/n).read_bytes()).hexdigest()==v for n,v in manifest.items())
profile=out/'runtime-sanitize'
run=json.loads((profile/'run-full.json').read_text())
assert run['results'][-1]['name']=='core' and run['results'][-1]['exit_code']!=0
total=0
for name in ('fencing','identity','core'):
    value=(profile/(name+'.log')).read_text()
    total+=sum(map(int,re.findall(r'^\d+: Ran (\d+) tests? in ',value,re.M)))
assert total==1433
files=[r/'local/authority-fencing-driver-01/driver.log',r/'local/authority-fencing-candidate-01.json']
files += [profile/n for n in ('run-full.json','core.log','harness.log','fencing.log','identity.log','baseline.log')]
files += list((profile/'server').glob('*BootstrapOwnerIdle*'))
files += [profile/'build/Testing/Temporary/LastTest.log']
files=[p for p in files if p.is_file()]
value=(profile/'core.log').read_text()
assert 'FAILED (failures=1)' in value and '99% tests passed, 1 tests failed out of 77' in value
assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL',value)
summary=dict(host=host,source_files=292,profile='runtime-sanitize',core_checks=1433,failed=1,passed=1432,
    P10='unrun_after_core_failure',integrated=False,
    failure='BootstrapOwnerIdleTests.test_idle_owner_does_not_block_workspace_bootstrap: UNAVAILABLE World identity service is disabled',
    service_evidence='Both world and broker recorded participant code26/request0; underlying exception is absent.',
    preserved_files=[str(p.relative_to(r)) for p in files])
p=r/'local/fencing-bootstrap-failure-summary-02.json';p.write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(r/'local/fencing-bootstrap-failure-sb16-02.tar.gz','w:gz') as archive:
    archive.add(p,arcname='failure-02/sb16/summary.json')
    for p in files:archive.add(p,arcname='failure-02/sb16/'+p.relative_to(r/'local').as_posix())
print(json.dumps(summary))
