"""Freeze the operator command increment without changing locked acceptance files."""
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
base = json.loads((root / 'evidence/control-recovery-2026-10-09/deferred-fence-01/sb16/manifest.json').read_bytes())
names = list(base['files']) + ['src/identity/revoke_main.cpp', 'tests/core/test_revocation_command.py']
data = {name: (root / name).read_bytes().replace(b'\r\n', b'\n') for name in names}
sha = lambda value: hashlib.sha256(value).hexdigest()
assert all(sha(data[name]) == digest for name, digest in base['locked_files'].items())
for name, value in data.items():
    if name.endswith('.py'): ast.parse(value, filename=name)
changed = sorted(name for name in data if sha(data[name]) != base['files'].get(name))
assert set(changed) == {'src/identity/revoke_main.cpp', 'tests/core/test_revocation_command.py',
    'proto/spatial/identity/v1/issuer.proto', 'CMakeLists.txt'}
manifest = dict(files={name: sha(value) for name, value in data.items()}, locked_files=base['locked_files'],
    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    changed=changed, selected_suites=['identity-revocation-command', 'identity-revocation',
        'identity-participant-barrier', 'identity-participant-barrier-client', 'issuer-client'])
encoded = (json.dumps(manifest, indent=2) + '\n').encode()
archive = root / 'local/revocation-command-source-02.tar'
with tarfile.open(archive, 'x') as tar:
    for name, value in [('manifest.json', encoded)] + [('source/' + name, value) for name, value in data.items()]:
        entry = tarfile.TarInfo(name); entry.size = len(value); entry.mode = 0o644
        tar.addfile(entry, io.BytesIO(value))
print(json.dumps(dict(files=len(data), archive_sha256=sha(archive.read_bytes()), changed=changed)))
