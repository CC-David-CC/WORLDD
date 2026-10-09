"""Exercise new integration witnesses against unchanged qualified broker binaries."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tarfile
import uuid

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
source = r / 'local/m02-supervisor-broker-handoff-02/source'
manifest = json.loads((r / 'local/supervisor-broker-handoff-candidate-02.json').read_text())['files']
qualified = json.loads((r / ('local/supervisor-broker-full-summary-final-' + host + '-02.json')).read_text())
for profile in ('debug', 'runtime-sanitize'):
    result = qualified[profile]
    assert tuple(result[k] for k in ('core_checks', 'core_suites', 'harness_checks', 'P10_cases', 'failed', 'skipped')) == (1393, 93, 51, 151, 0, 0)
for files in (manifest, json.loads((source / 'acceptance.lock.json').read_text())['files']):
    for name, digest in files.items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
test = r / 'local/test_supervised_broker_participation.py'
review = json.loads((r / 'local/supervised-broker-participation-review-01.json').read_text())
assert hashlib.sha256(test.read_bytes()).hexdigest() == review['test_sha256']
out = r / 'local/supervised-broker-participation-baseline-01'
out.mkdir()
summary = dict(host=host, source_files=283, production_changed=False,
    test_sha256=review['test_sha256'], profiles={}, complete_M02=False)
for profile in ('debug', 'runtime-sanitize'):
    target = out / profile
    target.mkdir()
    build = source.parent / profile / 'build'
    for name, digest in qualified[profile]['binaries'].items():
        assert hashlib.sha256((build / name).read_bytes()).hexdigest() == digest, name
    listed = json.loads(subprocess.check_output(['ctest', '--test-dir', str(build), '--show-only=json-v1']))
    command = list(next(item['command'] for item in listed['tests'] if item['name'] == 'authority-broker-handoff-regressions'))
    command.insert(1, '--property=RuntimeMaxSec=150')
    index = command.index(str(source / 'tests/native/test_supervisor_broker_handoff_regressions.py'))
    command[index] = str(test)
    for index, value in enumerate(command):
        if value.startswith('--unit='):
            command[index] = '--unit=spatial-supervised-broker-' + uuid.uuid4().hex
        elif value.startswith('--setenv=SPATIAL_SECURITY_EVIDENCE='):
            command[index] = '--setenv=SPATIAL_SECURITY_EVIDENCE=' + str(target / 'server')
    with (target / 'test.log').open('xb') as log:
        result = subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    text = (target / 'test.log').read_text()
    counts = re.findall(r'^Ran (\d+) tests? in ', text, re.M)
    entry = dict(command=command, exit_code=result.returncode, ran=int(counts[0]) if len(counts) == 1 else None,
        passed=result.returncode == 0 and counts == ['4'] and '\nOK\n' in text,
        sanitizer_marker=any(marker in text for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:')))
    (target / 'run.json').write_text(json.dumps(entry, indent=2) + '\n')
    summary['profiles'][profile] = entry
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(host, profile, 'supervised broker integration', entry['passed'], flush=True)
    # Preserve each failure and still observe the other already-qualified profile.
with tarfile.open(r / ('local/supervised-broker-participation-baseline-' + host + '-01.tar.gz'), 'w:gz') as archive:
    for path in sorted(out.rglob('*')):
        if path.is_file() and path.suffix in ('.log', '.json'):
            archive.add(path, arcname='baseline-01/' + host + '/' + path.relative_to(out).as_posix())
print(json.dumps(summary), flush=True)
raise SystemExit(0 if all(v['passed'] and not v['sanitizer_marker'] for v in summary['profiles'].values()) else 1)
