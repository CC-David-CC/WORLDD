"""Validate the private candidate without changing installed graphics libraries."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path('/home/dflanag3/src/spacial_desktop-m04-20261011/spatial-os-luna-handoff')
BUILD = ROOT / 'local/mesa-dril-diagnosis-20261011'
OUTPUT = ROOT / 'evidence/m04-glx-validation-2026-10-11/candidate-02'
OUTPUT.mkdir(parents=True, exist_ok=False)
if socket.gethostname() == 'sb16':
    previous = json.loads((ROOT / 'evidence/m04-glx-diagnosis-2026-10-11/patched-ab-02/run.json').read_text())
    environment = previous['environment']
    library = Path(previous['library']['path'])
elif socket.gethostname() == 'sb16-2':
    build = BUILD / 'build-egl-candidate'
    library = build / 'src/egl/libEGL_mesa.so.0.0.0'
    vendor = OUTPUT / 'vendor.json'
    vendor.write_text(json.dumps({'file_format_version': '1.0.0',
        'ICD': {'library_path': str(library)}}, indent=2))
    environment = {'__EGL_VENDOR_LIBRARY_FILENAMES': str(vendor),
        'LD_LIBRARY_PATH': ':'.join(str(build / p) for p in (
            'src/gallium/targets/dri', 'src/gbm', 'subprojects/expat-2.5.0'))}
else:
    raise SystemExit('Run only on a project build host')

receipt_path = ROOT / 'evidence/m04-event-metadata-2026-10-11/sanitize-build-02/run.json'
receipt = json.loads(receipt_path.read_text())
# The source pin is recorded by the server build receipt. Find its actual binary.
server = ROOT / 'local/xlibre-patched/b979f4660629da1eed4264eff720944a271301eb7fc998937d4e211f41d06532/source/build-sanitize/hw/vfb/Xvfb'
env = {**os.environ, **environment, 'SPATIAL_TEST_XSERVER': str(server),
    'PYTHONPATH': str(ROOT / 'tests/native') + ':' + str(ROOT / 'local/selection-runtime-sanitize/python')}
env.pop('SPATIAL_SECURITY_TEST_CORE_ONLY', None)
auth = str(ROOT / 'local/selection-runtime-sanitize/spatial-security-auth')
results = {}


def run(name, command, loader=False):
    folder = OUTPUT / name
    folder.mkdir()
    child = {**env, 'SPATIAL_SECURITY_EVIDENCE': str(folder)}
    if loader:
        child['LD_DEBUG'] = 'files'
    with (folder / 'command.log').open('wb') as log:
        result = subprocess.run(command, env=child, stdout=log,
                                stderr=subprocess.STDOUT, timeout=300)
    record = {'command': command, 'exit_code': result.returncode}
    (folder / 'command.json').write_text(json.dumps(record, indent=2))
    results[name] = record
    print(name, result.returncode, flush=True)


probe = BUILD / 'glx_lifecycle_probe'
run('compile', ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
    str(BUILD / 'glx_lifecycle_probe.c'), '-o', str(probe), '-lGL', '-lX11'])
if results['compile']['exit_code']:
    raise SystemExit(1)
run('render', ['python3', str(BUILD / 'test_glx_lifecycle.py'), auth, str(probe)], loader=True)
logs = list((OUTPUT / 'render').glob('*server-*.log'))
loaded = bool(logs) and all('calling init: ' + str(library) in p.read_text()
    and 'calling fini: ' + str(library) in p.read_text() for p in logs)
run('shutdown', ['python3', str(BUILD / 'repeat_shutdown.py'), auth])
full = OUTPUT / 'full-selected'
command = ['python3', str(ROOT / 'tools/qualify_atom_increment.py'),
    '--server-build', str(receipt_path), '--runtime-build',
    str(ROOT / 'local/selection-runtime-sanitize'), '--evidence', str(full)]
with (OUTPUT / 'full-selected.log').open('wb') as log:
    result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
results['full-selected'] = {'command': command, 'exit_code': result.returncode}
record = {'host': socket.gethostname(), 'results': results,
    'vendor_initialized_and_unloaded': loaded, 'environment': environment,
    'server_sha256': hashlib.sha256(server.read_bytes()).hexdigest(),
    'vendor_sha256': hashlib.sha256(library.read_bytes()).hexdigest(),
    'probe_sha256': hashlib.sha256(probe.read_bytes()).hexdigest(),
    'inputs_sha256': {name: hashlib.sha256((BUILD / name).read_bytes()).hexdigest()
        for name in ('glx_lifecycle_probe.c', 'test_glx_lifecycle.py', 'repeat_shutdown.py')},
    'phase_complete': False}
(OUTPUT / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps({'results': results, 'vendor_initialized_and_unloaded': loaded}))
raise SystemExit(0 if loaded and all(r['exit_code'] == 0 for r in results.values()) else 1)
