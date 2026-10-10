"""Freeze reviewed source for focused Linux recovery checks."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
previous = json.loads((root / 'evidence/control-recovery-2026-10-09/connected-applications-03/sb16/checks/review.json').read_bytes())
sha = lambda data: hashlib.sha256(data).hexdigest()
files = {name: (root / name).read_bytes().replace(b'\r\n', b'\n') for name in previous['source']}
assert all(sha(files[name]) == digest for name, digest in previous['locked'].items())
manifest = dict(files={name: sha(data) for name, data in files.items()}, locked_files=previous['locked'],
    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    purpose='Selective release after retained checked world cleanup. Preserve applications, unrelated owners, fences, and exact retry receipts.',
    full_gate='M02 qualification remains pending on both hosts.')
data = (json.dumps(manifest, indent=2) + '\n').encode()
with (root / 'local/selective-recovery-source-01.json').open('xb') as stream: stream.write(data)
with tarfile.open(root / 'local/selective-recovery-source-01.tar', 'x') as archive:
    for name, content in [('manifest.json', data)] + [('source/' + n, v) for n, v in files.items()]:
        item = tarfile.TarInfo(name); item.size = len(content); item.mode = 0o644
        archive.addfile(item, io.BytesIO(content))
print(json.dumps(dict(source_files=len(files), locked_files=len(previous['locked']),
    sha256=sha((root / 'local/selective-recovery-source-01.tar').read_bytes()))))
