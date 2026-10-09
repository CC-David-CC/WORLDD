"""Audit the staged integration against the exact reviewed and qualified candidate."""
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess

r = Path(__file__).resolve().parents[1]
repo = r.parent
e = r / 'evidence/supervisor-broker-handoff-2026-10-09'
record = json.loads((e / 'integration-applied-02.json').read_text(encoding='utf-8'))
manifest = record['files']
assert record['integrated'] and len(manifest) == 283
review = json.loads((e / 'integration-primary-review-02.json').read_text(encoding='utf-8'))
assert review['source_files'] == manifest and review['reviewed_files'] == record['changed']
assert review['reviewer'] == 'primary agent' and review['unresolved_static_defects'] == []
locked = json.loads((r / 'acceptance.lock.json').read_text(encoding='utf-8'))['files']
assert len(locked) == 41
staged = list(filter(None, subprocess.check_output(
    ['git', 'diff', '--cached', '--name-only', '-z'], cwd=repo).decode().split('\0')))
names = sorted(set(staged) | {'spatial-os-luna-handoff/' + n for n in manifest} |
               {'spatial-os-luna-handoff/' + n for n in locked})
stream = io.BytesIO(subprocess.check_output(['git', 'cat-file', '--batch'], cwd=repo,
    input=''.join(':' + n + '\n' for n in names).encode()))
blobs = {}
for n in names:
    header = stream.readline().split()
    assert len(header) == 3 and header[1] == b'blob', n
    blobs[n] = stream.read(int(header[2]))
    assert stream.read(1) == b'\n'
for files in (manifest, locked):
    for n, digest in files.items():
        assert hashlib.sha256(blobs['spatial-os-luna-handoff/' + n]).hexdigest() == digest, n
for host in ('sb16', 'sb16-2'):
    folder = e / 'full-candidate-02' / host
    assert json.loads((folder / 'source-manifest.json').read_text())['files'] == manifest
    summary = json.loads((folder / 'summary-final.json').read_text())
    assert summary['host'] == host and summary['candidate'] == 2 and summary['source_files'] == 283
    for profile in ('debug', 'runtime-sanitize'):
        result = summary[profile]
        assert tuple(result[k] for k in ('core_checks', 'core_suites', 'harness_checks', 'P10_cases', 'failed', 'skipped')) == (1393, 93, 51, 151, 0, 0)
        run = json.loads((folder / profile / 'run-full.json').read_text())
        assert [v['name'] for v in run['results']] == ['configure', 'build', 'harness', 'handoff', 'regressions', 'focused', 'core', 'P10']
        assert all(v['exit_code'] == 0 for v in run['results'])
        suites = []
        for part, number, count in (('handoff', 1, 1), ('regressions', 1, 9), ('focused', 9, 102), ('core', 82, 1281)):
            text = (folder / profile / (part + '.log')).read_text(encoding='utf-8')
            counts = list(map(int, re.findall(r'^\d+: Ran (\d+) tests? in ', text, re.M)))
            assert len(counts) == number and sum(counts) == count
            assert f'100% tests passed, 0 tests failed out of {number}' in text
            assert '... skipped' not in text and 'skipped=' not in text
            suites.extend(re.findall(r'^\s*Start\s+\d+:\s+(\S+)', text, re.M))
        assert len(suites) == len(set(suites)) == 93
        harness = (folder / profile / 'harness.log').read_text(encoding='utf-8')
        assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
        report = json.loads((folder / profile / 'P10.json').read_text())
        assert report['status'] == 'passed' and report['executed'] == len(report['tests']) == 151
        assert all(t['status'] == 'passed' for t in report['tests'])
for n in staged:
    assert not n.startswith('defect blockers/'), n
    assert Path(n).suffix not in ('.db', '.sqlite', '.sqlite3', '.pem', '.key', '.tar', '.gz'), n
    data = blobs[n]
    if n.endswith('.json'):
        json.loads(data)
    for pattern in (rb'-----BEGIN (?:[A-Z0-9 ]*PRIVATE KEY)-----', rb'Bearer\s+[A-Za-z0-9_-]{20,}', rb'fleet-[a-z0-9]+-[a-f0-9]{20,}'):
        assert not re.search(pattern, data), n
print(json.dumps(dict(source_files=283, locked_files=41, staged_files=len(staged),
    full_both_host_profiles='passed', credential_exclusions='passed')))
