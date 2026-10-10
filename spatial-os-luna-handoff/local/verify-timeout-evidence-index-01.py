"""Verify evidence-only staging while retaining the failed candidate as unintegrated work."""
import hashlib
import json
from pathlib import Path
import subprocess

project=Path(__file__).resolve().parents[1];root=project.parent
sha=lambda data:hashlib.sha256(data).hexdigest()
names=subprocess.check_output(['git','diff','--cached','--name-only','-z'],cwd=root).decode().split('\0')[:-1]
candidate={'spatial-os-luna-handoff/src/identity/issuer_client.cpp',
    'spatial-os-luna-handoff/tests/core/test_issuer_client.py',
    'spatial-os-luna-handoff/tests/core/test_identity_participant_runtime.py'}
assert not candidate.intersection(names)
assert all(name=='M02_REVOCATION_BARRIER_DEBUG.txt' or name.startswith('spatial-os-luna-handoff/') for name in names)
assert not any(name.startswith('defect blockers/') for name in names)
data=subprocess.check_output(['git','cat-file','--batch'],cwd=root,
    input=''.join(':'+name+'\n' for name in names).encode())
offset=0
for name in names:
    end=data.index(b'\n',offset);parts=data[offset:end].split()
    assert len(parts)==3 and parts[1]==b'blob'
    size=int(parts[2]);offset=end+1;blob=data[offset:offset+size];offset+=size
    assert data[offset:offset+1]==b'\n';offset+=1
    assert blob==(root/name).read_bytes(),name
assert offset==len(data)
raw=0
for directory,prefix in [('revocation-sequence-01',''),('participant-reply-timing-01',''),
    ('issuer-timeout-diagnostics-failure01','sb16')]:
    evidence=project/'evidence/control-recovery-2026-10-09'/directory
    review=json.loads((evidence/'primary-review.json').read_bytes())
    for name,digest in review['attachments'].items():
        path=evidence/prefix/name
        assert sha(path.read_bytes())==digest,name
        assert path.relative_to(root).as_posix() in names,name
        raw+=1
manifest=json.loads((project/'evidence/control-recovery-2026-10-09/issuer-timeout-diagnostics-failure01/sb16/m02-issuer-timeout-01/manifest.json').read_bytes())
assert all(sha((project/name).read_bytes().replace(b'\r\n',b'\n'))==digest for name,digest in manifest['files'].items())
assert all(manifest['files'][name]==digest for name,digest in manifest['locked_files'].items())
unstaged=set(subprocess.check_output(['git','diff','--name-only','-z'],cwd=root).decode().split('\0')[:-1])
assert unstaged==candidate,unstaged
print(json.dumps(dict(staged_files=len(names),staged_blobs_verified=len(names),raw_attachments=raw,
    candidate_source_files=len(manifest['files']),locked_files_unchanged=len(manifest['locked_files']),
    candidate_files_left_unintegrated=len(candidate))))
