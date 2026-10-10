"""Package canonical source on the controller. No runtime or tests run here."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
previous = json.loads((root / 'local/connected-applications-source-02.json').read_bytes())
previous['files'].update({name: '' for name in ('src/testing/session_probe.proto', 'src/testing/session_application_probe.cpp', 'tests/native/test_session_application.py')})
files = {name: (root / name).read_bytes().replace(b'\r\n', b'\n') for name in previous['files']}
sha = lambda data: hashlib.sha256(data).hexdigest()
locked = {name: sha((root / name).read_bytes().replace(b'\r\n', b'\n')) for name in previous['locked_files']}
assert locked == previous['locked_files']
manifest = dict(files={name: sha(data) for name, data in files.items()}, locked_files=locked,
    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    purpose='Connect real world and workspace authority with coordinator-created local actor channels and actor-launched confined native application. Remote application input remains denied. Verify native cancellation and application survival through actual coordinator ownership; retain the unresolved reservation evidence.',
    full_gate='M02 qualification remains pending on both hosts.')
data = (json.dumps(manifest, indent=2) + '\n').encode()
(root / 'local/connected-applications-source-03.json').write_bytes(data)
with tarfile.open(root / 'local/connected-applications-source-03.tar', 'x') as archive:
    for name, content in [('manifest.json', data)] + [('source/' + n, v) for n, v in files.items()]:
        item = tarfile.TarInfo(name); item.size = len(content); item.mode = 0o644
        archive.addfile(item, io.BytesIO(content))
print(json.dumps(dict(source_files=len(files), locked_files=len(locked), sha256=sha((root / 'local/connected-applications-source-03.tar').read_bytes()))))
