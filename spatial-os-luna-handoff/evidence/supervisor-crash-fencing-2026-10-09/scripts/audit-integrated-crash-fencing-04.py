"""Audit Git's index against the exact candidate and all four raw qualifications."""
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess

r = Path(__file__).resolve().parents[1]
repo = r.parent
prefix = 'spatial-os-luna-handoff/'
e = 'evidence/supervisor-crash-fencing-2026-10-09/'

def git(*args):
    return subprocess.check_output(['git', *args], cwd=repo)

staged = list(filter(None, git('diff', '--cached', '--name-only', '-z').decode().split('\0')))
# Enumerate committed/index paths. Read evidence from the index, never an unstaged copy.
index_record = json.loads(git('show', ':' + prefix + e + 'integration-applied-04.json'))
tracked = list(filter(None, git('ls-files', '-z', prefix + e).decode().split('\0')))
names = sorted(set(staged) | set(tracked) |
    {prefix + name for name in index_record['files']} |
    {prefix + 'evidence/supervisor-broker-handoff-2026-10-09/integration-applied-02.json'})
stream = io.BytesIO(subprocess.check_output(
    ['git', 'cat-file', '--batch'], cwd=repo,
    input=''.join(':' + name + '\n' for name in names).encode()))
blobs = {}
for name in names:
    header = stream.readline().split()
    assert len(header) == 3 and header[1] == b'blob', name
    blobs[name] = stream.read(int(header[2]))
    assert stream.read(1) == b'\n'

def raw(name):
    return blobs[prefix + name]

def read(name):
    return json.loads(raw(name))

def sha(value):
    return hashlib.sha256(value).hexdigest()

record = read(e + 'integration-applied-04.json')
manifest = record['files']
assert record['integrated'] is True and len(manifest) == 294
assert record['source_delta'].startswith('none:')
base = read('evidence/supervisor-broker-handoff-2026-10-09/integration-applied-02.json')['files']
assert len(base) == 283 and set(base) <= set(manifest)
changed = sorted(n for n in manifest if base.get(n) != manifest[n])
assert len(changed) == 37 and changed == record['changed']
locked = read('acceptance.lock.json')['files']
assert len(locked) == 41
for files in (manifest, locked):
    for name, digest in files.items():
        assert sha(raw(name)) == digest, name
        assert manifest[name] == digest, name
review = read(e + 'integration-primary-review-04.json')
assert review['reviewer'] == 'primary agent' and review['integration_ready'] is True
assert review['source_files'] == manifest and review['reviewed_files'] == changed
assert review['unresolved_static_defects'] == []
assert review['summary_hashes'] == record['summary_hashes']
assert review['diagnosed_failures'] == 'evidence/world-bootstrap-runtime-failure-2026-10-09/issuer-sync-review-09.json'
static = read(e + 'combined-static-review-04.json')
assert static['source_files'] == manifest and static['reviewed_files'] == changed
assert static['unresolved_static_defects'] == []

for host in ('sb16', 'sb16-2'):
    folder = e + 'serial-02/' + host + '/'
    assert sha(raw(folder + 'summary-final.json')) == review['summary_hashes'][host]
    summary = read(folder + 'summary-final.json')
    run = read(folder + 'run-final.json')
    assert summary['host'] == run['host'] == host and summary['mode'] == 'final'
    assert summary['source_files'] == 294 and summary['locked_files'] == 41
    assert summary['source_manifest'] == run['source_manifest'] == manifest
    assert read(folder + 'source-manifest.json')['files'] == manifest
    assert run['qualification'] == 'passed' and run['compiler_overlap'] == []
    monitor = [json.loads(line) for line in raw(folder + 'compiler-monitor-final.jsonl').splitlines()]
    assert monitor and not any(row['compilers'] for row in monitor)
    for profile in ('debug', 'runtime-sanitize'):
        result = summary['profiles'][profile]
        assert tuple(result[k] for k in ('core_checks', 'core_suites', 'harness_checks', 'P10_cases', 'failed', 'skipped')) == (1453, 99, 51, 151, 0, 0)
        execution = run['profiles'][profile]
        commands = execution['results']
        assert [x['name'] for x in commands] == ['harness', 'fencing', 'focused', 'core', 'P10']
        assert all(x['exit_code'] == 0 for x in commands)
        assert result['commands'] == commands and result['binaries'] == execution['binaries']
        assert result['server'] == execution['server']
        assert monitor[0]['time'] <= commands[0]['start']
        assert monitor[-1]['time'] >= commands[-1]['end'] - 2
        suites = []
        total = 0
        for part in ('fencing', 'focused', 'core'):
            text = raw(folder + profile + '/' + part + '.log').decode()
            counts = list(map(int, re.findall(r'^\d+: Ran (\d+) tests? in ', text, re.M)))
            names = re.findall(r'^\s*Start\s+\d+:\s+(\S+)', text, re.M)
            assert counts and len(counts) == len(names)
            assert f'100% tests passed, 0 tests failed out of {len(counts)}' in text
            assert '... skipped' not in text and 'skipped=' not in text
            assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL', text)
            assert result['parts'][part] == dict(zip(names, counts))
            if part == 'fencing':
                assert counts == [10]
            suites.extend(names)
            total += sum(counts)
        assert total == 1453 and len(suites) == len(set(suites)) == 99
        inventory = read(folder + profile + '/ctest-inventory.json')
        assert set(suites) == {x['name'] for x in inventory['tests']}
        harness = raw(folder + profile + '/harness.log').decode()
        assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
        assert '... skipped' not in harness and 'skipped=' not in harness
        p10 = read(folder + profile + '/P10.json')
        assert p10['status'] == 'passed' and p10['executed'] == len(p10['tests']) == 151
        assert all(x['status'] == 'passed' for x in p10['tests'])

for name in staged:
    assert not name.startswith('defect blockers/'), name
    assert Path(name).suffix not in ('.db', '.sqlite', '.sqlite3', '.pem', '.key', '.tar', '.gz'), name
    value = blobs[name]
    if name.endswith('.json'):
        json.loads(value)
    for pattern in (rb'-----BEGIN (?:[A-Z0-9 ]*PRIVATE KEY)-----', rb'Bearer\s+[A-Za-z0-9_-]{20,}', rb'fleet-[a-z0-9]+-[a-f0-9]{20,}'):
        assert not re.search(pattern, value), name
print(json.dumps(dict(source_files=294, locked_files=41, reviewed_files=37,
    staged_files=len(staged), full_both_host_profiles='passed', credential_exclusions='passed')))
