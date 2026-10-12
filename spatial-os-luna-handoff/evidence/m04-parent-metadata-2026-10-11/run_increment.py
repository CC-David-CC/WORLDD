"""Build and check one frozen parent-metadata candidate on an allowed host."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path('/home/dflanag3/src/spacial_desktop-m04-20261011/spatial-os-luna-handoff')
EVIDENCE = ROOT / 'evidence/m04-parent-metadata-2026-10-11/corrected-01'
EVIDENCE.mkdir(parents=True, exist_ok=False)
previous = json.loads((ROOT / 'evidence/m04-glx-validation-2026-10-11/candidate-02/run.json').read_text())
env = {**os.environ, **previous['environment']}
inputs = ['CMakeLists.txt', 'server/base.lock.json', 'server/xlibre/spatialpolicy.c',
          'server/patches/0010-private-parent-metadata.patch',
          'tests/native/test_parent_metadata.py', 'tools/qualify_atom_increment.py']
before = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in inputs}
record = {'phase_complete': False, 'inputs_sha256': before,
          'environment': previous['environment'], 'commands': []}


def run(name, command):
    with (EVIDENCE / (name + '.log')).open('wb') as output:
        result = subprocess.run(command, env=env, stdout=output,
                                stderr=subprocess.STDOUT, timeout=1200)
    record['commands'].append({'name': name, 'command': command, 'exit_code': result.returncode})
    (EVIDENCE / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
    print(name, result.returncode, flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)


for profile in ('debug', 'sanitize'):
    build = EVIDENCE / (profile + '-build')
    command = ['python3', str(ROOT / 'tools/build_xserver.py'), '--evidence-dir', str(build)]
    if profile == 'sanitize':
        command.append('--sanitize')
    run(profile + '-build', command)
    runtime = ROOT / ('local/selection-runtime-' + profile)
    run(profile + '-runtime-configure', ['cmake', '-S', str(ROOT), '-B', str(runtime)])
    run(profile + '-helper-build', ['cmake', '--build', str(runtime), '-j2',
                                    '--target', 'spatial-security-auth', 'spatial-protobuf'])
    run(profile + '-selected', ['python3', str(ROOT / 'tools/qualify_atom_increment.py'),
        '--server-build', str(build / 'run.json'), '--runtime-build', str(runtime),
        '--evidence', str(EVIDENCE / (profile + '-selected'))])
record['source_unchanged'] = all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
                               for name, expected in before.items())
record['status'] = 'passed' if record['source_unchanged'] else 'invalidated'
(EVIDENCE / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
raise SystemExit(0 if record['status'] == 'passed' else 1)
