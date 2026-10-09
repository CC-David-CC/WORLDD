"""Finish only the unrun P10 step after recorded successful core qualification."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess

r = Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
assert socket.gethostname().split('.')[0] == 'sb16-2'
for pid in (701256, 708801, 716356, 785544):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        continue
    raise AssertionError('Original worker PID remains present: ' + str(pid))
source = r / 'local/m02-authority-supervisor-02/source'
out = source.parent / 'runtime-sanitize'
build = out / 'build'
manifest = json.loads((r / 'local/authority-supervisor-candidate-02.json').read_text())['files']
for entries in (manifest, json.loads((source / 'acceptance.lock.json').read_text())['files']):
    for name, digest in entries.items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
path = out / 'run-full.json'
run = json.loads(path.read_text())
assert [x['name'] for x in run['results']] == ['configure', 'build', 'harness', 'ownership', 'supervisor', 'focused', 'core']
assert all(x['exit_code'] == 0 for x in run['results'])
text = (out / 'core.log').read_text()
counts = [int(x) for x in re.findall(r'^\d+: Ran (\d+) tests? in ', text, re.M)]
assert len(counts) == 66 and sum(counts) == 1000
assert '100% tests passed, 0 tests failed out of 66' in text
assert '... skipped' not in text and 'skipped=' not in text
assert not (out / 'P10.log').exists() and not (out / 'P10.json').exists()
server = run['server']
assert hashlib.sha256(Path(server['path']).read_bytes()).hexdigest() == server['sha256']
env = dict(os.environ, PYTHONPATH=str(build / 'python') + ':' + str(source),
    SPATIAL_TEST_XSERVER=server['path'], SPATIAL_XPROBE=str(build / 'spatial-xprobe'),
    SPATIAL_SUT=json.dumps([str(build / 'spatial-test-host')]), SPATIAL_SECURITY_EVIDENCE=str(out / 'server'),
    SPATIAL_EVIDENCE_DIR=str(out / 'cases'), ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
    UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
command = ['python3', 'tools/with_test_display.py', '--', 'python3', 'tools/gate.py', '--through', 'P10', '--report', str(out / 'P10.json')]
with (out / 'P10.log').open('xb') as log:
    result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
run['results'].append(dict(name='P10', command=command, exit_code=result.returncode))
path.write_text(json.dumps(run, indent=2) + '\n')
print('sb16-2 remaining supervisor P10', result.returncode, flush=True)
raise SystemExit(result.returncode)
