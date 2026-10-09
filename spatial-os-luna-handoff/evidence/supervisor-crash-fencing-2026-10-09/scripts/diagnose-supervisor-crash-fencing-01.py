"""Observe automatic crash fencing against unchanged supervisor293 binaries."""
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import tarfile
import uuid

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
source = r/'local/m02-supervisor-fencing-01/source'
manifest = json.loads((r/'local/supervisor-fencing-candidate-01.json').read_text())['files']
qualified = json.loads((r/('local/supervisor-fencing-summary-new-final-'+host+'-01.json')).read_text())
for files in (manifest, json.loads((source/'acceptance.lock.json').read_text())['files']):
    for name, digest in files.items():
        assert hashlib.sha256((source/name).read_bytes()).hexdigest() == digest, name
test = r/'local/test_supervisor_crash_fencing_baseline.py'
out = r/'local/supervisor-crash-fencing-baseline-01'
out.mkdir()
summary = dict(host=host, source_files=293, production_changed=False, qualification=False,
    test_sha256=hashlib.sha256(test.read_bytes()).hexdigest(), profiles={})
for profile in ('debug', 'runtime-sanitize'):
    target = out/profile
    target.mkdir()
    build = source.parent/profile/'build'
    assert qualified[profile]['core_checks'] == 10 and qualified[profile]['failed'] == 0
    for name, digest in qualified[profile]['binaries'].items():
        assert hashlib.sha256((build/name).read_bytes()).hexdigest() == digest, name
    listed = json.loads(subprocess.check_output(['ctest', '--test-dir', str(build), '--show-only=json-v1']))
    command = list(next(t['command'] for t in listed['tests'] if t['name'] == 'authority-supervisor-fencing'))
    command.insert(1, '--property=RuntimeMaxSec=150')
    index = command.index(str(source/'tests/native/test_supervisor_fencing.py'))
    command[index] = str(test)
    for index, value in enumerate(command):
        if value.startswith('--unit='):
            command[index] = '--unit=spatial-crash-fencing-'+uuid.uuid4().hex
        elif value.startswith('--setenv=SPATIAL_SECURITY_EVIDENCE='):
            command[index] = '--setenv=SPATIAL_SECURITY_EVIDENCE='+str(target/'server')
    with (target/'test.log').open('xb') as log:
        result = subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    text = (target/'test.log').read_text()
    expected = (result.returncode != 0 and re.findall(r'^Ran (\d+) tests? in ', text, re.M) == ['2']
        and 'FAILED (failures=2)' in text
        and text.count('AssertionError: False is not true : Missing automatic supervisor crash fence') == 2)
    entry = dict(command=command, exit_code=result.returncode, expected_missing_feature=expected,
        sanitizer_marker=any(marker in text for marker in ('ERROR: AddressSanitizer', 'ERROR: LeakSanitizer', 'runtime error:', 'AddressSanitizer:DEADLYSIGNAL')))
    (target/'run.json').write_text(json.dumps(entry, indent=2)+'\n')
    summary['profiles'][profile] = entry
    (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(host, profile, 'expected missing automatic crash fence', expected, flush=True)
with tarfile.open(r/('local/supervisor-crash-fencing-baseline-'+host+'-01.tar.gz'), 'w:gz') as archive:
    for path in sorted(out.rglob('*')):
        if path.is_file() and path.suffix in ('.log', '.json'):
            archive.add(path, arcname='baseline-01/'+host+'/'+path.relative_to(out).as_posix())
raise SystemExit(0 if all(v['expected_missing_feature'] and not v['sanitizer_marker']
    for v in summary['profiles'].values()) else 1)
