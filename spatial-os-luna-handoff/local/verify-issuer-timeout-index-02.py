"""Compare staged source and raw evidence bytes with the reviewed candidate."""
import hashlib
import json
from pathlib import Path
import subprocess

project = Path(__file__).resolve().parents[1]
root = project.parent
evidence = project / 'evidence/control-recovery-2026-10-09/issuer-timeout-diagnostics-02'
manifest = json.loads((evidence / 'sb16/m02-issuer-timeout-02/manifest.json').read_bytes())
review = json.loads((evidence / 'primary-review.json').read_bytes())
names = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '-z'], cwd=root).decode().split('\0')[:-1]
assert not any(name.startswith('defect blockers/') for name in names)
for name in names:
    assert name == 'M02_REVOCATION_BARRIER_DEBUG.txt' or name.startswith('spatial-os-luna-handoff/')
source = {'spatial-os-luna-handoff/' + name: digest for name, digest in manifest['files'].items()}
all_names = sorted(set(names) | set(source))
data = subprocess.check_output(['git', 'cat-file', '--batch'], cwd=root,
    input=''.join(':' + name + '\n' for name in all_names).encode())
offset = 0
for name in all_names:
    end = data.index(b'\n', offset)
    parts = data[offset:end].split()
    assert len(parts) == 3 and parts[1] == b'blob'
    size = int(parts[2]); offset = end + 1
    blob = data[offset:offset + size]; offset += size
    assert data[offset:offset + 1] == b'\n'; offset += 1
    assert blob == (root / name).read_bytes(), name
    if name in source:
        assert hashlib.sha256(blob.replace(b'\r\n', b'\n')).hexdigest() == source[name], name
assert offset == len(data)
for name, digest in review['attachments'].items():
    assert hashlib.sha256((evidence / 'sb16' / name).read_bytes()).hexdigest() == digest, name
trace = project / 'evidence/control-recovery-2026-10-09/participant-tls-progress-01'
trace_review = json.loads((trace / 'primary-review.json').read_bytes())
for name, digest in trace_review['attachments'].items():
    assert hashlib.sha256((trace / name).read_bytes()).hexdigest() == digest, name
assert len(manifest['locked_files']) == 41
assert all(manifest['files'][name] == digest for name, digest in manifest['locked_files'].items())
print(json.dumps(dict(staged_files=len(names), checked_blobs=len(all_names),
    source_files=len(source), raw_attachments=len(review['attachments']) + len(trace_review['attachments']), locked_files_unchanged=41)))
