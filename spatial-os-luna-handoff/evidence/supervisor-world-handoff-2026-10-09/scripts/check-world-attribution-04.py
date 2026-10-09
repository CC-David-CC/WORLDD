import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import uuid
r=Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host=socket.gethostname().split('.')[0];assert host in ('sb16','sb16-2')
root=r/'local/m02-supervisor-world-handoff-01';source=root/'source';build=root/'debug/build'
manifest=json.loads((r/'local/supervisor-world-handoff-candidate-01.json').read_text())['files']
for name,digest in manifest.items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest
original=(root/'debug/regressions.log').read_text();assert 'FAILED (failures=2)' in original
out=r/'local/world-native-attribution-04';out.mkdir()
test=r/'local/test_supervisor_world_handoff_regressions_04.py'
listed=json.loads(subprocess.check_output(['ctest','--test-dir',str(build),'--show-only=json-v1']))
command=list(next(t['command'] for t in listed['tests'] if t['name']=='authority-world-handoff-regressions'))
command[command.index(str(source/'tests/native/test_supervisor_world_handoff_regressions.py'))]=str(test)
command.insert(1,'--property=RuntimeMaxSec=150')
for i,v in enumerate(command):
    if v.startswith('--unit='):command[i]='--unit=spatial-world-reader-'+uuid.uuid4().hex
    elif v.startswith('--setenv=SPATIAL_SECURITY_EVIDENCE='):command[i]='--setenv=SPATIAL_SECURITY_EVIDENCE='+str(out/'server')
binaries={n:hashlib.sha256((build/n).read_bytes()).hexdigest() for n in ('worldd','spatialwm','spatial-authority-supervisor','identityd')}
with (out/'test.log').open('xb') as log:result=subprocess.run(command,cwd=source,stdout=log,stderr=subprocess.STDOUT,timeout=180)
text=(out/'test.log').read_text()
passed=result.returncode==0 and 'Ran 13 tests' in text and '\nOK\n' in text
for name,digest in binaries.items():assert hashlib.sha256((build/name).read_bytes()).hexdigest()==digest
summary=dict(host=host,source_files=290,production_changed=False,test_sha256=hashlib.sha256(test.read_bytes()).hexdigest(),
    binaries=binaries,command=command,exit_code=result.returncode,passed=passed,expected_checks=13,
    sanitizer_marker=any(v in text for v in ('AddressSanitizer','LeakSanitizer','runtime error:')))
(out/'run.json').write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(r/('local/world-native-attribution-'+host+'-04.tar.gz'),'w:gz') as archive:
    for path in sorted(out.rglob('*')):
        if path.is_file() and path.suffix in ('.log','.json'):archive.add(path,arcname='native-attribution-04/'+host+'/'+path.relative_to(out).as_posix())
print(host,'world reader correction',passed,flush=True)
if not passed:print(text[-18000:],flush=True)
raise SystemExit(0 if passed and not summary['sanitizer_marker'] else 1)
