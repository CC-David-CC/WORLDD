"""Exercise new integration witnesses against unchanged focused-qualified world binaries."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tarfile
import uuid
import time

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
# Wait for the exact cumulative qualifier and corrected recovery diagnostic.
for folder, marker in (('crash-fencing-serial-driver-02', b'qualify-crash-fencing-serial-02.py'),
                       ('control-recovery-baseline-driver-02', b'run-control-recovery-baseline-02.py')):
    launch = json.loads((r / 'local' / folder / 'launch.json').read_text())
    while True:
        try:
            command = (Path('/proc') / str(launch['pid']) / 'cmdline').read_bytes()
        except FileNotFoundError:
            break
        if marker not in command:
            break
        time.sleep(2)
run = json.loads((r / 'local/crash-fencing-serial-02/summary.json').read_text())
assert run['qualification'] == 'passed' and not run['compiler_overlap']
assert json.loads((r / 'local/control-recovery-baseline-02/run.json').read_text())['state'] == 'diagnostic_passed'
source = r / 'local/m02-supervisor-crash-fencing-01/source'
manifest = run['source_manifest']; assert len(manifest) == 294
qualified = run['profiles']
for profile in ('debug', 'runtime-sanitize'):
    assert [x['name'] for x in qualified[profile]['results']] == ['harness', 'fencing', 'focused', 'core', 'P10']
    assert all(x['exit_code'] == 0 for x in qualified[profile]['results'])
for files in (manifest, json.loads((source / 'acceptance.lock.json').read_text())['files']):
    for name, digest in files.items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
test = r / 'local/test_supervised_world_participation.py'
review = json.loads((r / 'local/supervised-world-participation-review-01.json').read_text())
assert hashlib.sha256(test.read_bytes()).hexdigest() == review['test_sha256']
out = r / 'local/supervised-world-participation-combined-02'
out.mkdir()
summary = dict(host=host, source_files=294, production_changed=False,
    test_sha256=review['test_sha256'], profiles={}, complete_M02=False)
for profile in ('debug', 'runtime-sanitize'):
    target = out / profile
    target.mkdir()
    build = source.parent / profile / 'build'
    for name, digest in qualified[profile]['binaries'].items():
        assert hashlib.sha256((build / name).read_bytes()).hexdigest() == digest, name
    listed = json.loads(subprocess.check_output(['ctest', '--test-dir', str(build), '--show-only=json-v1']))
    command = list(next(item['command'] for item in listed['tests'] if item['name'] == 'authority-world-handoff-regressions'))
    command.insert(1, '--property=RuntimeMaxSec=150')
    index = command.index(str(source / 'tests/native/test_supervisor_world_handoff_regressions.py'))
    command[index] = str(test)
    for index, value in enumerate(command):
        if value.startswith('--unit='):
            command[index] = '--unit=spatial-supervised-world-' + uuid.uuid4().hex
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
    print(host, profile, 'supervised world integration', entry['passed'], flush=True)
    # Preserve each failure and still observe the other already-qualified profile.
with tarfile.open(r / ('local/supervised-world-participation-combined-' + host + '-02.tar.gz'), 'w:gz') as archive:
    for path in sorted(out.rglob('*')):
        if path.is_file() and path.suffix in ('.log', '.json'):
            archive.add(path, arcname='combined-02/' + host + '/' + path.relative_to(out).as_posix())
print(json.dumps(summary), flush=True)
raise SystemExit(0 if all(v['passed'] and not v['sanitizer_marker'] for v in summary['profiles'].values()) else 1)
