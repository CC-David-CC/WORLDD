"""Compare staged source and preserved logs against the reviewed native candidate."""
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
evidence = root / 'evidence/control-recovery-2026-10-09'
review = json.loads((evidence / 'participant-diagnostics-02/sb16/primary-review.json').read_bytes())
expected = {'spatial-os-luna-handoff/' + name: digest for name, digest in review['integrated_source'].items()}
for tag in ('01', '02'):
    target = evidence / ('participant-diagnostics-' + tag + '/sb16')
    collected = json.loads((target / 'collected-review.json').read_bytes())
    run = json.loads((target / 'checks/review.json').read_bytes())
    for name, digest in collected['attachments'].items():
        expected[(target / name).relative_to(root.parent).as_posix()] = digest
    if tag == '01':
        expected[(target / 'test_identity_participant_runtime.py').relative_to(root.parent).as_posix()] = run['source']['tests/core/test_identity_participant_runtime.py']
expected['spatial-os-luna-handoff/local/repeat-participant-startup-02.py'] = run['profiles']['debug']['repeat_script_sha256']
requests = ''.join(':' + name + '\n' for name in expected).encode()
result = subprocess.run(['git', 'cat-file', '--batch'], input=requests, cwd=root.parent, capture_output=True, check=True).stdout
offset = 0
for name, digest in expected.items():
    end = result.index(b'\n', offset); header = result[offset:end].split()
    assert len(header) == 3 and header[1] == b'blob', (name, header)
    length = int(header[2]); offset = end + 1
    data = result[offset:offset + length]; offset += length
    assert result[offset:offset + 1] == b'\n'; offset += 1
    assert hashlib.sha256(data).hexdigest() == digest, name
assert offset == len(result)
staged = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '-z'], cwd=root.parent).split(b'\0')
assert not any(name.startswith(b'defect blockers/') or name.endswith(
    (b'driver-state.json', b'.db', b'.tar', b'.tar.gz')) for name in staged if name)
print(json.dumps(dict(verified_staged_blobs=len(expected), source_files=len(review['integrated_source']),
    status='exact_tested_source_and_preserved_evidence')))
