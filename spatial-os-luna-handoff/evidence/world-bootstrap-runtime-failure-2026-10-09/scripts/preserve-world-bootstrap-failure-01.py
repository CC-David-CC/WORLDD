import hashlib
import json
from pathlib import Path
import socket
import tarfile

r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host=='sb16-2'
out=r/'local/m02-supervisor-world-handoff-02'
manifest=json.loads((r/'local/supervisor-world-handoff-candidate-02.json').read_text())['files']
assert all(hashlib.sha256((out/'source'/n).read_bytes()).hexdigest()==v for n,v in manifest.items())
run=json.loads((out/'runtime-sanitize/run-full.json').read_text())
assert run['results'][-1]['name']=='core' and run['results'][-1]['exit_code']!=0
files=[r/'local/world-handoff-driver-02/driver.log',r/'local/supervisor-world-handoff-candidate-02.json']
files += [out/'runtime-sanitize'/n for n in ('run-full.json','core.log','harness.log','handoff.log','regressions.log','focused.log')]
files += list((out/'runtime-sanitize/server').rglob('*test_public_entry_reports_unbound_template_without_action_authority*'))
files += [out/'runtime-sanitize/build/Testing/Temporary/LastTest.log']
files=[p for p in files if p.is_file()]
summary=dict(host=host,candidate=2,source_files=290,profile='runtime-sanitize',
    core_checks=1407,failed=1,passed=1406,P10='unrun_after_core_failure',integrated=False,
    failure='WorldBootstrapTests.test_public_entry_reports_unbound_template_without_action_authority: UNAVAILABLE World identity service is disabled',
    preserved_files=[str(p.relative_to(r)) for p in files])
target=r/'local/world-bootstrap-failure-summary-01.json';target.write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(r/'local/world-bootstrap-failure-sb16-2-01.tar.gz','w:gz') as archive:
    archive.add(target,arcname='failure-01/sb16-2/summary.json')
    for p in files:archive.add(p,arcname='failure-01/sb16-2/'+str(p.relative_to(r/'local')))
print(json.dumps(summary))
for p in files:
    if 'test_public_entry_reports_unbound_template_without_action_authority' in p.name:
        print(str(p.relative_to(r)),p.read_text(errors='replace')[-8000:])
