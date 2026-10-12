"""Reconcile the dedicated M04 source checkout with the tested gate and corrections."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
from reviewed_local_gate import require_reviewed_gate

root = Path('/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff')
adopted = Path('/home/dflanag3/src/spacial_desktop-m04-qualified-20261011/spatial-os-luna-handoff')
gate = require_reviewed_gate(root)
candidate = '04e10296943013da3a29035f00b520907b97f3ca'
assert subprocess.check_output(['git', '-C', str(adopted), 'rev-parse', 'HEAD'], text=True).strip() == candidate
assert not subprocess.check_output(['git', '-C', str(adopted), 'status', '--porcelain'], text=True).strip()

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

fixture = Path('/home/dflanag3/src/spacial_desktop-m04-fixture-20261011/spatial-os-luna-handoff')
storage = json.loads((fixture / 'local/m04-storage-fixture-correction-01/run.json').read_text())
deadlines = json.loads((root / 'local/m04-setup-deadline-correction-01/run.json').read_text())
corrections = {'tests/native/storage_session_volume.py': storage['corrected_helper_sha256']}
corrections.update({'tests/core/' + name: value for name, value in deadlines['draft_sha256'].items()})
names = ['CMakeLists.txt', 'acceptance.lock.json', 'docs/CONTRACT.md']
for directory in ('src', 'proto', 'tests', 'tools', 'config', 'server', 'harness', 'native'):
    names.extend(str(p.relative_to(adopted)) for p in (adopted / directory).rglob('*')
                 if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
assert set(names) == set(gate['source_sha256']), 'Input file inventory changed'
inputs = {name: digest(adopted / name) for name in sorted(set(names))}
changed = {name: value for name, value in inputs.items() if value != gate['source_sha256'][name]}
assert changed == corrections, changed
locked = json.loads((adopted / 'acceptance.lock.json').read_text())['files']
for name, expected in locked.items():
    assert digest(adopted / name) == expected, name
for name, expected in gate['installed_helpers'].items():
    assert digest(Path(name)) == expected, name
for profile, row in gate['profiles'].items():
    build = root / 'local/m04-gate-m04-full-gate-01' / profile
    for name, expected in row['binary_sha256'].items():
        assert digest(build / name) == expected, name
    assert digest(Path(row['server']['path'])) == row['server']['sha256']
    assert digest(Path(row['launcher'])) == row['launcher_sha256']
vendor = Path(json.loads(Path(gate['graphics']['__EGL_VENDOR_LIBRARY_FILENAMES']).read_text())['ICD']['library_path'])
assert digest(vendor) == gate['vendor_sha256']
report = dict(status='source_reconciled_pending_milestone_review', host=socket.gethostname().split('.')[0],
              phase_complete=False, adopted_candidate=candidate, runtime_candidate=gate['candidate'],
              adopted_path=str(adopted), gate_sha256=digest(root / 'local/m04-full-gate-01/run.json'),
              source_sha256=inputs, corrected_files=corrections, locked_files_verified=len(locked),
              rebuilt=False, runtime_note='Retain the gate binaries. Production inputs are unchanged; only three tested fixture files differ.')
(adopted / 'local').mkdir(exist_ok=True)
(adopted / 'local/m04-source-adoption.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(dict(host=report['host'], status=report['status'], inputs=len(inputs), corrected=len(changed), locked=len(locked))))
