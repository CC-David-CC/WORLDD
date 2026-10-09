import hashlib
import json
from pathlib import Path
import socket
import tarfile
r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16','sb16-2')
out = r/'local/m02-supervisor-world-handoff-01'
manifest = json.loads((r/'local/supervisor-world-handoff-candidate-01.json').read_text())
for name,digest in manifest['files'].items():
    assert hashlib.sha256((out/'source'/name).read_bytes()).hexdigest() == digest
run = json.loads((out/'debug/run.json').read_text())
assert [x['name'] for x in run['results']] == ['configure','build','harness','handoff','regressions']
assert [x['exit_code'] for x in run['results'][:4]] == [0,0,0,0]
assert run['results'][4]['exit_code'] != 0
log = (out/'debug/regressions.log').read_text()
assert 'Ran 11 tests' in log and 'FAILED (failures=2)' in log
assert log.count('... ok') == 9 and log.count('... FAIL') == 2
assert 'Native request returned error/event' in log
harness = (out/'debug/harness.log').read_text()
assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
assert '100% tests passed, 0 tests failed out of 1' in (out/'debug/handoff.log').read_text()
summary = dict(host=host,candidate=1,source_files=290,integrated=False,
    debug=dict(harness_passed=51,startup_passed=1,regressions_passed=9,regressions_failed=2,skipped=0),
    diagnosis='Post-cleanup independent identity reader encounters packet type34 before its reply. Root cause review pending.',
    runtime_sanitize='unrun',full_cumulative='unrun')
p=out/'summary-debug.json';p.write_text(json.dumps(summary,indent=2)+'\n')
with tarfile.open(r/('local/world-handoff-debug-'+host+'-01.tar.gz'),'w:gz') as archive:
    for path in sorted((out/'debug').rglob('*')):
        if path.is_file() and 'build' not in path.relative_to(out/'debug').parts and path.suffix in ('.log','.json'):
            archive.add(path,arcname='candidate-01/'+host+'/debug/'+path.relative_to(out/'debug').as_posix())
    archive.add(p,arcname='candidate-01/'+host+'/summary-debug.json')
print(json.dumps(summary))
