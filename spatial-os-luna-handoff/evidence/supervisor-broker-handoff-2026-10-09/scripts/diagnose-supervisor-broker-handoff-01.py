"""Observe the missing handoff against unchanged, fully qualified supervisor binaries."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import uuid

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
host = socket.gethostname().split('.')[0]
assert host in ('sb16', 'sb16-2')
source = r / 'local/m02-authority-supervisor-02/source'
manifest = json.loads((r / 'local/authority-supervisor-candidate-02.json').read_text())['files']
qualified = json.loads((r / ('local/authority-supervisor-full-summary-final-' + host + '-02.json')).read_text())
assert qualified['source_files'] == 281 and qualified['integrated'] is False
for profile in ('debug', 'runtime-sanitize'):
    result = qualified[profile]
    assert (result['core_checks'], result['core_suites'], result['harness_checks'], result['P10_cases'],
            result['failed'], result['skipped']) == (1383, 91, 51, 151, 0, 0)
for entries in (manifest, json.loads((source / 'acceptance.lock.json').read_text())['files']):
    for name, digest in entries.items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
for path in Path('/proc').iterdir():
    if not path.name.isdigit():
        continue
    try:
        if path.stat().st_uid != os.geteuid():
            continue
        args = (path / 'cmdline').read_bytes().split(b'\0')
        forbidden = [str(r / 'local' / name).encode() for name in
            ('qualify-authority-supervisor-02.py', 'finish-authority-supervisor-full-02.py')]
        assert len(args) < 2 or args[1] not in forbidden, 'Qualification is still active'
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        continue
out = r / 'local/supervisor-broker-handoff-baseline-01'
out.mkdir()
summary = dict(host=host, source_files=281, production_changed=False, acceptance_pass=False, profiles={})
for profile in ('debug', 'runtime-sanitize'):
    target = out / profile
    target.mkdir()
    build = source.parent / profile / 'build'
    listed = json.loads(subprocess.check_output(['ctest', '--test-dir', str(build), '--show-only=json-v1']))
    command = list(next(test['command'] for test in listed['tests'] if test['name'] == 'authority-supervisor'))
    index = command.index(str(source / 'tests/native/test_authority_supervisor.py'))
    command[index:index+1] = [str(r / 'local/test_supervisor_broker_handoff.py'), str(source)]
    for i, part in enumerate(command):
        if part.startswith('--unit='):
            command[i] = '--unit=spatial-broker-handoff-' + uuid.uuid4().hex
        elif part.startswith('--setenv=SPATIAL_SECURITY_EVIDENCE='):
            command[i] = '--setenv=SPATIAL_SECURITY_EVIDENCE=' + str(target / 'server')
    binary_hashes = {name: hashlib.sha256((build / name).read_bytes()).hexdigest()
        for name in ('spatial-authority-supervisor', 'workspaced', 'identityd')}
    for name, digest in binary_hashes.items():
        assert qualified[profile]['binaries'][name] == digest, name
    with (target / 'test.log').open('wb') as log:
        run = subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    result = dict(command=command, exit_code=run.returncode, binaries=binary_hashes)
    (target / 'run.json').write_text(json.dumps(result, indent=2) + '\n')
    log = (target / 'test.log').read_text()
    assert run.returncode != 0 and 'Ran 1 test' in log and 'FAILED (failures=1)' in log, log[-10000:]
    assert 'has no field named "broker_native_target_fd"' in log, log[-10000:]
    assert 'Supervised broker native handoff failed:' in log
    assert not any(marker in log for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:'))
    summary['profiles'][profile] = dict(expected_missing_handoff_failures=1, acceptance_pass=False)
    print(host, profile, 'missing broker native handoff confirmed', flush=True)
(out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
with tarfile.open(r / ('local/supervisor-broker-handoff-baseline-' + host + '-01.tar.gz'), 'w:gz') as archive:
    for path in sorted(out.rglob('*')):
        if path.is_file() and path.suffix in ('.log', '.json'):
            archive.add(path, arcname='baseline-01/' + host + '/' + path.relative_to(out).as_posix())
print(json.dumps(summary), flush=True)
