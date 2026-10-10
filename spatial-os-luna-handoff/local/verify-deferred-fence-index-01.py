"""Verify exact staged source, selected documentation, and untouched raw evidence."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]; repo = root.parent; prefix = root.name + '/'
evidence = root / 'evidence/control-recovery-2026-10-09/deferred-fence-01'
baseline = root / 'evidence/control-recovery-2026-10-09/issuer-loss-baseline-03'
review = json.loads((evidence / 'primary-review.json').read_bytes())
sha = lambda value: hashlib.sha256(value).hexdigest()
manifest = json.loads((evidence / 'sb16/manifest.json').read_bytes())
allowed = {prefix + name for name in manifest['changed_from_qualified']}
allowed.update(prefix + name for name in ['CURRENT_STATE.md', 'tasks/m1/identity.control_recovery.json',
    'tasks/m1/identity.supervisor_recovery.json', 'docs/DEFERRED_RECOVERY_FENCE.txt',
    'docs/ISSUER_LOSS_RECOVERY_CHECKS.txt', 'docs/AUTHORITY_RETIREMENT.txt',
    'docs/AUTHORITY_SUPERVISOR_RECOVERY.txt', 'docs/MILESTONES.md'])
allowed.add('M02_REVOCATION_BARRIER_DEBUG.txt')
helpers = ['prepare-deferred-fence-01.py', 'unpack-deferred-fence-01.py', 'build-deferred-fence-01.py',
    'check-deferred-fence-01.py', 'check-deferred-fence-02.py', 'collect-deferred-fence-01.py',
    'integrate-deferred-fence-01.py', 'verify-deferred-fence-index-01.py',
    'collect-issuer-loss-baseline-03.py', 'preserve-issuer-loss-baseline-03.py']
allowed.update(prefix + 'local/' + name for name in helpers)
allowed.update(path.relative_to(repo).as_posix() for directory in (evidence, baseline) for path in directory.rglob('*') if path.is_file())
staged = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '-z'], cwd=repo).decode().split('\0')[:-1]
assert set(staged) == allowed, dict(extra=sorted(set(staged)-allowed), missing=sorted(allowed-set(staged)))
expected = {prefix + name: (digest, True) for name,digest in review['integrated_source'].items()}
for name in staged:
    if name not in expected: expected[name] = (sha((repo / name).read_bytes()), False)
names = sorted(expected)
output = subprocess.check_output(['git', 'cat-file', '--batch'], cwd=repo,
    input=b''.join((':' + name + '\n').encode() for name in names))
position = 0
for name in names:
    end = output.index(b'\n', position); header = output[position:end].split()
    assert len(header) == 3 and header[1] == b'blob', (name, header)
    size = int(header[2]); data = output[end+1:end+1+size]; position = end+size+2
    assert output[position-1:position] == b'\n'
    digest, normalized = expected[name]
    assert sha(data.replace(b'\r\n', b'\n') if normalized else data) == digest, name
    assert not re.search(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----\r?\n[A-Za-z0-9+/=\r\n]+-----END', data), name
    assert not re.search(rb'Bearer fleet-[0-9a-f]{12,}', data), name
    if name.endswith('.py'): ast.parse(data, filename=name)
assert position == len(output)
for directory, filename in [(evidence / 'sb16', 'collected-review.json'),
    (baseline / 'sb16', 'issuer-loss-baseline-03-review.json')]:
    collected = json.loads((directory / filename).read_bytes())
    assert all(sha((directory / name).read_bytes()) == digest for name,digest in collected['attachments'].items())
result = dict(status='staged_source_and_evidence_verified', staged_files=len(staged),
    verified_blobs=len(names), source_files=348, locked_files=41)
(root / 'local/deferred-fence-index-review-01.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
