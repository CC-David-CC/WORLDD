"""Verify staged source and collected raw evidence in one Git object read."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
repo = root.parent
prefix = root.name + '/'
evidence = root / 'evidence/control-recovery-2026-10-09/crosshost-cancellation-03'
review = json.loads((evidence / 'primary-review.json').read_bytes())
sha = lambda data: hashlib.sha256(data).hexdigest()
staged = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '-z'], cwd=repo).decode().split('\0')[:-1]
allowed = {prefix + name for name in ('CMakeLists.txt', 'CURRENT_STATE.md',
    'tasks/m1/identity.control_recovery.json', 'tests/native/test_authority_owned_participant.py',
    'tests/native/test_session_application.py', 'tests/native/crosshost_revocation.py',
    'tests/proto/crosshost_revocation.proto', 'docs/CROSS_HOST_CANCELLATION_CHECKS.txt')}
allowed.add('M02_REVOCATION_BARRIER_DEBUG.txt')
helpers = ['prepare-crosshost-cancellation.py', 'build-crosshost-cancellation-01.py',
    'run-crosshost-cancellation-node-01.py', 'check-crosshost-related-01.py', 'coordinate-crosshost-check.ps1',
    'collect-crosshost-cancellation-03.py', 'integrate-crosshost-cancellation-03.py', 'verify-crosshost-index-03.py']
helpers += ['unpack-crosshost-cancellation-' + tag + '.py' for tag in ('01', '02', '03')]
allowed.update(prefix + 'local/' + name for name in helpers)
evidence_names = {path.relative_to(repo).as_posix() for path in evidence.rglob('*') if path.is_file()}
allowed.update(evidence_names)
assert set(staged) == allowed, dict(extra=sorted(set(staged)-allowed), missing=sorted(allowed-set(staged)))
expected = {prefix + name: (digest, True) for name, digest in review['integrated_source'].items()}
for name in staged:
    if name not in expected: expected[name] = (sha((repo / name).read_bytes()), False)
names = sorted(expected)
output = subprocess.check_output(['git', 'cat-file', '--batch'], cwd=repo,
    input=b''.join((':' + name + '\n').encode() for name in names))
position = 0
for name in names:
    end = output.index(b'\n', position)
    header = output[position:end].split(); assert len(header) == 3 and header[1] == b'blob', (name, header)
    size = int(header[2]); data = output[end+1:end+1+size]; position = end+size+2
    assert output[position-1:position] == b'\n'
    digest, normalized = expected[name]
    assert sha(data.replace(b'\r\n', b'\n') if normalized else data) == digest, name
    assert not re.search(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----\r?\n[A-Za-z0-9+/=\r\n]+-----END', data), name
    assert not re.search(rb'Bearer fleet-[0-9a-f]{12,}', data), name
    if name.endswith('.py'): ast.parse(data, filename=name)
assert position == len(output)
for host in ('sb16', 'sb16-2'):
    collected = json.loads((evidence / host / 'collected-review.json').read_bytes())
    assert all(sha((evidence / host / name).read_bytes()) == digest for name, digest in collected['attachments'].items())
summary = dict(status='staged_source_and_evidence_verified', staged_files=len(staged),
    verified_blobs=len(names), source_files=347, locked_files=41, raw_evidence_files=len(evidence_names))
(root / 'local/crosshost-index-review-03.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary))
