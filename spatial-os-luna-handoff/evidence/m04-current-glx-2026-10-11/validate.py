"""Reassess current native security with the previously verified private EGL candidate."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path('/home/dflanag3/src/spacial_desktop-m04-hooks/spatial-os-luna-handoff')
OLD = Path('/home/dflanag3/src/spacial_desktop-m04-20261011/spatial-os-luna-handoff')
OUTPUT = ROOT / 'evidence/m04-current-glx-20261011/run-01'
host = socket.gethostname().split('.')[0]
if host not in ('sb16', 'sb16-2'):
    raise SystemExit('Only project build hosts are allowed')

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

previous = json.loads((OLD / 'evidence/m04-glx-validation-2026-10-11/candidate-02/run.json').read_text())
environment = previous['environment']
vendor = Path(environment['__EGL_VENDOR_LIBRARY_FILENAMES'])
library = Path(json.loads(vendor.read_text())['ICD']['library_path'])
assert digest(library) == previous['vendor_sha256'], 'Candidate library changed'
fixture = OLD / 'local/mesa-dril-diagnosis-20261011'
for name, expected in previous['inputs_sha256'].items():
    assert digest(fixture / name) == expected, 'Fixture changed: ' + name
probe = fixture / 'glx_lifecycle_probe'
assert digest(probe) == previous['probe_sha256'], 'GLX witness changed'
receipt_path = ROOT / 'evidence/m04-nested-pixels-20261011/build-sanitize-01/run.json'
receipt = json.loads(receipt_path.read_text())
assert receipt['status'] == 'passed' and receipt['sanitizers'] and receipt['host'] == host
server = Path(receipt['binaries']['Xvfb']['path'])
assert digest(server) == receipt['binaries']['Xvfb']['sha256']
for name, expected in receipt['extension_sources'].items():
    assert digest(ROOT / name) == expected, 'Server source changed: ' + name

OUTPUT.mkdir(parents=True, exist_ok=False)
runtime = OLD / 'local/selection-runtime-sanitize'
env = {**os.environ, **environment,
       'SPATIAL_TEST_XSERVER': str(server),
       'PYTHONPATH': str(ROOT / 'tests/native') + ':' + str(runtime / 'python') + ':' + str(ROOT),
       'ASAN_OPTIONS': 'detect_leaks=1:abort_on_error=1',
       'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=1'}
env.pop('SPATIAL_SECURITY_TEST_CORE_ONLY', None)
record = {'host': host, 'status': 'running', 'phase_complete': False,
          'server': receipt['binaries']['Xvfb'], 'vendor_path': str(library),
          'vendor_sha256': digest(library), 'environment': environment,
          'fixture_hashes': previous['inputs_sha256'], 'commands': []}

def run(name, command, loader=False):
    folder = OUTPUT / name
    folder.mkdir()
    child = {**env, 'SPATIAL_SECURITY_EVIDENCE': str(folder)}
    if loader:
        child['LD_DEBUG'] = 'files'
    with (folder / 'command.log').open('wb') as log:
        result = subprocess.run(command, env=child, stdout=log,
                                stderr=subprocess.STDOUT, timeout=300)
    record['commands'].append({'name': name, 'argv': command, 'exit_code': result.returncode})
    print(name, result.returncode, flush=True)
    if result.returncode:
        raise RuntimeError(name + ' failed')

try:
    auth = str(runtime / 'spatial-security-auth')
    run('render', ['python3', str(fixture / 'test_glx_lifecycle.py'), auth, str(probe)], True)
    logs = list((OUTPUT / 'render').glob('*server-*.log'))
    loaded = bool(logs) and all('calling init: ' + str(library) in p.read_text()
                               and 'calling fini: ' + str(library) in p.read_text() for p in logs)
    record['vendor_initialized_and_unloaded'] = loaded
    assert loaded, 'Missing candidate load/unload evidence'
    run('shutdown', ['python3', str(fixture / 'repeat_shutdown.py'), auth])
    run('selected', ['python3', str(ROOT / 'tools/qualify_atom_increment.py'),
                    '--server-build', str(receipt_path), '--runtime-build', str(runtime),
                    '--evidence', str(OUTPUT / 'selected-results')])
    record['status'] = 'passed'
except Exception as error:
    record.update(status='failed', error=str(error))
finally:
    record['evidence_sha256'] = {str(p.relative_to(OUTPUT)): digest(p)
                                for p in OUTPUT.rglob('*') if p.is_file()}
    (OUTPUT / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
print(record['status'], flush=True)
sys.exit(0 if record['status'] == 'passed' else 1)
