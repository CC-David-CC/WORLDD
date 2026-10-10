"""Package canonical source on the controller. No runtime or tests run here."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
previous = json.loads((root / 'local/connected-recovery-source-04.json').read_bytes())
files = {name: (root / name).read_bytes().replace(b'\r\n', b'\n') for name in previous['files']}
sha = lambda data: hashlib.sha256(data).hexdigest()
locked = {name: sha((root / name).read_bytes().replace(b'\r\n', b'\n')) for name in previous['locked_files']}
assert locked == previous['locked_files']
manifest = dict(files={name: sha(data) for name, data in files.items()}, locked_files=locked,
    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    purpose='Admit a checked actor TLS listener through real SessionCoordinator ownership. Authenticate a signed actor and reject private endpoint substitutions.',
    full_gate='M02 qualification remains pending on both hosts.')
data = (json.dumps(manifest, indent=2) + '\n').encode()
(root / 'local/connected-actors-source-01.json').write_bytes(data)
with tarfile.open(root / 'local/connected-actors-source-01.tar', 'x') as archive:
    for name, content in [('manifest.json', data)] + [('source/' + n, v) for n, v in files.items()]:
        item = tarfile.TarInfo(name); item.size = len(content); item.mode = 0o644
        archive.addfile(item, io.BytesIO(content))
print(json.dumps(dict(source_files=len(files), locked_files=len(locked), sha256=sha((root / 'local/connected-actors-source-01.tar').read_bytes()))))
