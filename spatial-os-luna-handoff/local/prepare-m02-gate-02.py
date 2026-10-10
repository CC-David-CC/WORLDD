"""Freeze the reviewed source for both-host M02 qualification."""
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

project = Path(__file__).resolve().parents[1]
prior = json.loads((project / 'evidence/m02-gate-2026-10-10/failure01/sb16/m02-phase-gate-01/manifest.json').read_bytes())
sha = lambda data: hashlib.sha256(data).hexdigest()
data = {name: (project / name).read_bytes() for name in prior['files']}
changed = [name for name, value in data.items() if sha(value) != prior['files'][name]]
assert changed == ['tests/core/test_identity_enrollment.py']
tracked = subprocess.check_output(['git', 'ls-files', 'src', 'proto', 'tests', 'tools', 'harness', 'native', 'config', 'CMakeLists.txt', 'acceptance.lock.json'], cwd=project).decode().splitlines()
assert not set(tracked) - set(data)
for name, value in data.items():
    if name.endswith('.py'):
        ast.parse(value, filename=name)
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=project, text=True).strip()
assert head == '1f5088e026573d86be8388cf1f41dfce20189db9'
manifest = dict(candidate='m02-phase-gate-02', commit=head, files={name: sha(value) for name,value in data.items()},
    changed_from_original=changed,
    locked_files=prior['locked_files'], hosts=['sb16', 'sb16-2'],
    profiles=['debug', 'runtime-sanitize'],
    selection='All registered CTest suites, all harness cases, and locked P00-P10 acceptance.',
    additional_required='Reassess cross-host, native, recovery, and mutation evidence separately. Historical failures remain open.')
assert len(manifest['files']) == 351 and len(manifest['locked_files']) == 41
assert all(sha(data[name]) == digest for name, digest in manifest['locked_files'].items())
diagnostic = {name: (project/'local'/name).read_bytes() for name in ('trace-qualification-03.py', 'qualification-syscalls-03.bt')}
manifest['diagnostic_tools'] = {name: sha(value) for name,value in diagnostic.items()}
archive = project / 'local/m02-phase-gate-source-02.tar'
with tarfile.open(archive, 'x') as tar:
    for name, value in [('manifest.json', (json.dumps(manifest, indent=2)+'\n').encode())] + [('source/'+name, value) for name, value in data.items()] + [('instrumentation/'+name, value) for name,value in diagnostic.items()]:
        entry = tarfile.TarInfo(name)
        entry.size = len(value)
        entry.mode = 0o644
        tar.addfile(entry, io.BytesIO(value))
print(json.dumps(dict(archive=str(archive), sha256=sha(archive.read_bytes()), source_files=len(data), commit=head)))
