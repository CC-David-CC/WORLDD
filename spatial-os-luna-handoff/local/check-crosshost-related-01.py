"""Check the existing fixture callers without rerunning the full phase matrix."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

host = socket.gethostname().split('.')[0]; assert host in ('sb16', 'sb16-2')
profile = sys.argv[1]; assert profile in ('debug', 'runtime-sanitize')
base = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff/local')
root = base / 'm02-crosshost-cancellation-01'; source_root = base / 'm02-crosshost-cancellation-02'
build = root / profile / 'build'
record = json.loads((root / 'checks' / profile / 'build-review.json').read_bytes())
manifest = json.loads((source_root / 'manifest.json').read_bytes())
assert record['status'] == 'built_tests_unrun'
assert {name for name in record['source'] if record['source'][name] != manifest['files'][name]} == {'tests/native/crosshost_revocation.py'}
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert all(sha(root / 'source' / name) == value for name, value in record['source'].items())
assert all(sha(source_root / 'source' / name) == value for name, value in manifest['files'].items())
assert all(sha(build / name) == value for name, value in record['binaries'].items())
for path in Path('/proc').iterdir():
    if not path.name.isdigit(): continue
    try: name = (path / 'comm').read_text().strip()
    except (FileNotFoundError, ProcessLookupError, PermissionError): continue
    assert name not in ('ctest', 'cc1', 'cc1plus', 'clang', 'clang++', 'ninja'), (path, name)
out = source_root / 'related' / profile; out.mkdir(parents=True, exist_ok=False)
env = dict(os.environ, SPATIAL_TEST_XSERVER=record['server']['path'], SPATIAL_SECURITY_EVIDENCE=str(out / 'native'),
    ASAN_OPTIONS='detect_leaks=1:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
env.pop('LSAN_OPTIONS', None)
selection = '^(authority-owned-participant|session-application)$'
tests = json.loads(subprocess.check_output(['ctest', '--test-dir', str(build), '--show-only=json-v1', '-R', selection]))['tests']
assert {test['name'] for test in tests} == {'authority-owned-participant', 'session-application'}
review = dict(status='testing', host=host, profile=profile, started=time.time(), selection=selection,
    purpose='Verify default fixture behavior and native application callers after shared-issuer fixture hooks.',
    build_review_sha256=sha(root / 'checks' / profile / 'build-review.json'), tests=[])
def save(): (out / 'review.json').write_bytes((json.dumps(review, indent=2) + '\n').encode())
save()
try:
    for test in tests:
        command = [arg for arg in test['command'] if not arg.startswith('--setenv=SPATIAL_SECURITY_EVIDENCE=')]
        command.insert(command.index('/usr/bin/python3'), '--setenv=SPATIAL_SECURITY_EVIDENCE=' + str(out / 'native'))
        item = dict(name=test['name'], command=command, started=time.time()); review['tests'].append(item); save()
        path = out / (test['name'] + '.log')
        with path.open('xb') as log:
            result = subprocess.run(command, cwd=root / 'source', env=env, stdout=log, stderr=subprocess.STDOUT, timeout=200)
        item.update(exit_code=result.returncode, ended=time.time(), log_sha256=sha(path)); save()
        print(host, profile, test['name'], result.returncode, flush=True)
        if result.returncode:
            print(path.read_text()[-6000:], flush=True); raise RuntimeError('Related fixture check failed')
    assert all(sha(build / name) == value for name, value in record['binaries'].items())
    review['status'] = 'passed'
except BaseException:
    review['status'] = 'failed'; raise
finally:
    review['ended'] = time.time(); save()
