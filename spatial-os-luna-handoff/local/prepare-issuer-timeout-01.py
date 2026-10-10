"""Freeze the diagnostic increment and retain all locked acceptance hashes."""
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
base = json.loads((root / 'evidence/operator-native-2026-10-10/candidate-01/sb16/m02-operator-native-01/manifest.json').read_bytes())
data = {name:(root / name).read_bytes().replace(b'\r\n', b'\n') for name in base['files']}
sha = lambda value:hashlib.sha256(value).hexdigest()
assert all(sha(data[name]) == digest for name,digest in base['locked_files'].items())
for name,value in data.items():
    if name.endswith('.py'): ast.parse(value, filename=name)
changed = sorted(name for name,value in data.items() if sha(value) != base['files'][name])
assert set(changed) == {'src/identity/issuer_client.cpp', 'tests/core/test_issuer_client.py',
    'tests/core/test_identity_participant_runtime.py'}
manifest = dict(files={name:sha(value) for name,value in data.items()}, locked_files=base['locked_files'],
    base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(), changed=changed,
    selected_suites=['issuer-client','identity-participant-runtime','authority-approval-interruption','authority-supervisor'])
encoded = (json.dumps(manifest,indent=2)+'\n').encode()
archive = root / 'local/issuer-timeout-source-01.tar'
with tarfile.open(archive,'x') as tar:
    for name,value in [('manifest.json',encoded)]+[('source/'+name,value) for name,value in data.items()]:
        entry=tarfile.TarInfo(name);entry.size=len(value);entry.mode=0o644;tar.addfile(entry,io.BytesIO(value))
print(json.dumps(dict(files=len(data),archive_sha256=sha(archive.read_bytes()),changed=changed)))
