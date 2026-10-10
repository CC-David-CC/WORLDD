"""Verify the exact staged source and recorded raw evidence before committing."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

repo = Path(__file__).resolve().parents[2]
prefix = 'spatial-os-luna-handoff/'
evidence = prefix + 'evidence/control-recovery-2026-10-09/'
read = lambda name: subprocess.check_output(['git', 'show', ':' + name], cwd=repo)
sha = lambda data: hashlib.sha256(data).hexdigest()
value = lambda name: json.loads(read(name))
verified = set()

def exact(name, digest):
    assert sha(read(name)) == digest, name
    verified.add(name)

review = value(evidence + 'approval-interruption-03/sb16/primary-review.json')
assert review['status'] == 'reviewed_focused_coverage_phase_gate_pending'
assert review['selected_cases_per_profile'] == 4 and len(review['integrated_source']) == 345
assert review['selected_failed'] == review['selected_skipped'] == 0
for name, digest in review['integrated_source'].items():
    exact(prefix + name, digest)
for folder in ('approval-interruption-01', 'approval-interruption-failure02', 'approval-interruption-03'):
    path = evidence + folder + '/sb16/'
    collected = value(path + 'collected-review.json')
    run = value(path + 'checks/review.json')
    for name, digest in collected['attachments'].items(): exact(path + name, digest)
    exact(path + 'test_approval_interruption.py', run['source']['tests/native/test_approval_interruption.py'])
for folder, metadata, field in (
    ('approval-timeout-diagnosis-01', 'primary-review.json', 'attachments'),
    ('approval-timeout-timing-02', 'review.json', 'attachments'),
    ('approval-timeout-timing-failure01', 'terminal-review.json', 'final_attachments')):
    path = evidence + folder + '/'
    for name, digest in value(path + metadata)[field].items(): exact(path + name, digest)
paths = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '-z'], cwd=repo).decode().split('\0')
for name in filter(None, paths):
    assert not name.startswith('defect blockers/'), name
    assert not name.endswith(('.db', '.tar', '.tar.gz', '.zip', '/driver-state.json')), name
    data = read(name)
    assert not re.search(rb'Bearer fleet-[0-9a-f]{10,}', data), name
    assert not re.search(rb'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----\r?\n', data), name
    if name.endswith('.py'): ast.parse(data, filename=name)
print(json.dumps(dict(staged_source_and_evidence_blobs_verified=len(verified), selected_cases_per_profile=4,
    original_timeout_resolved=False, full_M02_gate='pending on both hosts')))
