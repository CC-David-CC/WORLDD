"""Check staged world integration against reviewed source and four raw test profiles."""
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess

r = Path(__file__).resolve().parents[1]
repo = r.parent
e = r / 'evidence/supervisor-world-handoff-2026-10-09'
def read(path):
    return json.loads(path.read_text(encoding='utf-8'))
record = read(e / 'integration-applied-03.json')
manifest, qualified = record['files'], record['qualified_files']
assert record['integrated'] and len(manifest) == len(qualified) == 290
comment_name = 'proto/spatial/supervisor/v1/launch.proto'
assert [n for n in manifest if manifest[n] != qualified[n]] == [comment_name]
review = read(e / 'integration-primary-review-03.json')
assert review['source_files'] == manifest and review['qualified_source_files'] == qualified
assert review['reviewed_files'] == record['changed'] and len(record['changed']) == 16
assert review['reviewer'] == 'primary agent' and not review['unresolved_static_defects']
locked = read(r / 'acceptance.lock.json')['files']
assert len(locked) == 41
staged = list(filter(None,subprocess.check_output(['git','diff','--cached','--name-only','-z'],cwd=repo).decode().split('\0')))
names = sorted(set(staged) | {'spatial-os-luna-handoff/'+n for n in manifest} | {'spatial-os-luna-handoff/'+n for n in locked})
stream = io.BytesIO(subprocess.check_output(['git','cat-file','--batch'],cwd=repo,
    input=''.join(':'+n+'\n' for n in names).encode()))
blobs = {}
for n in names:
    header = stream.readline().split()
    assert len(header) == 3 and header[1] == b'blob', n
    blobs[n] = stream.read(int(header[2]))
    assert stream.read(1) == b'\n'
for files in (manifest,locked):
    for n,digest in files.items():
        assert hashlib.sha256(blobs['spatial-os-luna-handoff/'+n]).hexdigest() == digest,n
for host in ('sb16','sb16-2'):
    delta = e / 'comment-equivalence-03' / host
    equivalence = read(delta / 'result.json')
    assert equivalence['before_sha256'] == qualified[comment_name] and equivalence['after_sha256'] == manifest[comment_name]
    assert equivalence['comments_only'] and equivalence['descriptor_equal']
    assert (delta / 'before.pb').read_bytes() == (delta / 'after.pb').read_bytes()
    assert hashlib.sha256((delta / 'before.pb').read_bytes()).hexdigest() == equivalence['descriptor_sha256']
    folder = e / 'candidate-02' / host
    assert read(folder / 'source-manifest.json')['files'] == qualified
    summary = read(folder / 'summary-final.json')
    assert summary['host'] == host and summary['candidate'] == 2 and summary['source_files'] == 290
    for profile in ('debug','runtime-sanitize'):
        result = summary[profile]
        assert tuple(result[k] for k in ('core_checks','core_suites','harness_checks','P10_cases','failed','skipped')) == (1407,95,51,151,0,0)
        run = read(folder / profile / 'run-full.json')
        assert run['host'] == host and run['source_files'] == 290
        assert [v['name'] for v in run['results']] == ['configure','build','harness','handoff','regressions','focused','core','P10']
        assert all(v['exit_code'] == 0 for v in run['results'])
        suites = []
        for part,number,count in (('handoff',1,1),('regressions',1,13),('focused',11,112),('core',82,1281)):
            log = (folder / profile / (part+'.log')).read_text(encoding='utf-8')
            counts = list(map(int,re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)))
            assert len(counts) == number and sum(counts) == count
            assert f'100% tests passed, 0 tests failed out of {number}' in log
            assert '... skipped' not in log and 'skipped=' not in log
            assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL',log)
            suites.extend(re.findall(r'^\s*Start\s+\d+:\s+(\S+)',log,re.M))
        assert len(suites) == len(set(suites)) == 95
        harness = (folder / profile / 'harness.log').read_text(encoding='utf-8')
        assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
        p10 = read(folder / profile / 'P10.json')
        assert p10['status'] == 'passed' and p10['executed'] == len(p10['tests']) == 151
        assert all(x['status'] == 'passed' for x in p10['tests'])
for n in staged:
    assert not n.startswith('defect blockers/'),n
    assert Path(n).suffix not in ('.db','.sqlite','.sqlite3','.pem','.key','.tar','.gz'),n
    data = blobs[n]
    if n.endswith('.json'):
        json.loads(data)
    for pattern in (rb'-----BEGIN (?:[A-Z0-9 ]*PRIVATE KEY)-----',rb'Bearer\s+[A-Za-z0-9_-]{20,}',rb'fleet-[a-z0-9]+-[a-f0-9]{20,}'):
        assert not re.search(pattern,data),n
print(json.dumps(dict(source_files=290,locked_files=41,staged_files=len(staged),
    full_both_host_profiles='passed',comment_delta='verified',credential_exclusions='passed')))
