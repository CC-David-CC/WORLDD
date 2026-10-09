"""Prepare world integration; apply only with complete four-profile evidence."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re

parser = argparse.ArgumentParser()
parser.add_argument('--apply', action='store_true')
args = parser.parse_args()
r = Path(__file__).resolve().parents[1]
e = r / 'evidence/supervisor-world-handoff-2026-10-09'
def read(path):
    return json.loads(path.read_text(encoding='utf-8'))
def digest(data):
    return hashlib.sha256(data).hexdigest()
def normalized(path):
    return path.read_bytes().replace(b'\r\n', b'\n')
base = read(r / 'evidence/supervisor-broker-handoff-2026-10-09/integration-applied-02.json')['files']
qualified = read(r / 'local/supervisor-world-handoff-candidate-02.json')['files']
draft = r / 'local/supervisor-world-handoff-draft-02'
assert len(base) == 283 and len(qualified) == 290 and set(base) <= set(qualified)
for name, value in base.items():
    assert digest(normalized(r / name)) == value, name
contents = {name: normalized(draft / name) for name in qualified}
assert {n: digest(v) for n, v in contents.items()} == qualified
review = read(e / 'candidate-prepared-review-02.json')
assert review['reviewer'] == 'primary agent' and review['source_files'] == qualified
assert not review['unresolved_static_defects']
comment = read(e / 'schema-comment-review-03.json')
name = comment['file']
assert digest(contents[name]) == comment['before_sha256']
replacement = normalized(r / 'local/authority-fencing-draft-01' / name)
assert digest(replacement) == comment['after_sha256']
def uncomment(data):
    return b'\n'.join(line.split(b'//', 1)[0].rstrip() for line in data.splitlines())
assert uncomment(contents[name]) == uncomment(replacement)
for host in ('sb16', 'sb16-2'):
    folder = e / 'comment-equivalence-03' / host
    result = read(folder / 'result.json')
    assert result['host'] == host and result['file'] == name
    assert result['before_sha256'] == comment['before_sha256']
    assert result['after_sha256'] == comment['after_sha256']
    assert result['comments_only'] and result['descriptor_equal'] and not result['frozen_candidate_changed']
    descriptor = (folder / 'before.pb').read_bytes()
    assert descriptor == (folder / 'after.pb').read_bytes()
    assert digest(descriptor) == result['descriptor_sha256']
contents[name] = replacement
manifest = {n: digest(v) for n, v in contents.items()}
assert [n for n in manifest if manifest[n] != qualified[n]] == [name]
changed = sorted(n for n in manifest if manifest[n] != base.get(n))
assert changed == review['reviewed_files'] and len(changed) == 16
for n in set(manifest) - set(base):
    assert not (r / n).exists(), n
locked = read(r / 'acceptance.lock.json')['files']
assert len(locked) == 41 and all(manifest[n] == v for n, v in locked.items())
assert all(manifest[n] == v for n, v in base.items() if n.startswith('tests/'))
patch = []
for n in changed:
    before = normalized(r / n).decode('utf-8').splitlines(True) if n in base else []
    patch.extend(difflib.unified_diff(before, contents[n].decode('utf-8').splitlines(True),
        fromfile='a/' + n if n in base else '/dev/null', tofile='b/' + n))
(e / 'integration-candidate-03.patch').write_bytes(''.join(patch).encode('utf-8'))
record = dict(files=manifest, qualified_files=qualified, changed=changed, base_files=283,
    source_files=290, locked_files=41, integrated=False,
    qualified_candidate=2, integration_revision=3,
    source_delta=dict(file=name, before_sha256=qualified[name], after_sha256=manifest[name],
                      comments_only=True, descriptor_equivalence='comment-equivalence-03'),
    expected_core_checks=1407, expected_core_suites=95, expected_harness_checks=51, expected_P10_cases=151)
(e / 'integration-prepared-03.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
if not args.apply:
    print('Prepared 16-file world integration with verified comment-only delta. Production unchanged.')
    raise SystemExit(0)

# Check raw evidence before any production file changes.
for host in ('sb16', 'sb16-2'):
    folder = e / 'candidate-02' / host
    summary = read(folder / 'summary-final.json')
    assert summary['host'] == host and summary['candidate'] == 2 and summary['source_files'] == 290
    assert summary['locked_files'] == 41 and read(folder / 'source-manifest.json')['files'] == qualified
    schema = summary['schema']
    assert schema['existing_files'] == 12 and schema['existing_descriptors_unchanged'] and schema['field_number'] == 12
    for profile in ('debug', 'runtime-sanitize'):
        result = summary[profile]
        assert tuple(result[k] for k in ('core_checks', 'core_suites', 'harness_checks', 'P10_cases', 'failed', 'skipped')) == (1407,95,51,151,0,0)
        run = read(folder / profile / 'run-full.json')
        assert run['host'] == host and run['source_files'] == 290
        assert [x['name'] for x in run['results']] == ['configure','build','harness','handoff','regressions','focused','core','P10']
        assert all(x['exit_code'] == 0 for x in run['results'])
        names = []
        for part, suites, count in (('handoff',1,1),('regressions',1,13),('focused',11,112),('core',82,1281)):
            log = (folder / profile / (part + '.log')).read_text(encoding='utf-8')
            counts = list(map(int,re.findall(r'^\d+: Ran (\d+) tests? in ',log,re.M)))
            listed = re.findall(r'^\s*Start\s+\d+:\s+(\S+)',log,re.M)
            assert len(counts) == len(listed) == suites and sum(counts) == count
            assert f'100% tests passed, 0 tests failed out of {suites}' in log
            assert 'skipped=' not in log and '... skipped' not in log
            assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|AddressSanitizer:DEADLYSIGNAL',log)
            names.extend(listed)
        assert len(names) == len(set(names)) == 95
        harness = (folder / profile / 'harness.log').read_text(encoding='utf-8')
        assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
        assert 'skipped=' not in harness and '... skipped' not in harness
        p10 = read(folder / profile / 'P10.json')
        assert p10['status'] == 'passed' and p10['executed'] == len(p10['tests']) == 151
        assert all(x['status'] == 'passed' for x in p10['tests'])
final_review = read(e / 'integration-primary-review-03.json')
assert final_review['reviewer'] == 'primary agent' and not final_review['unresolved_static_defects']
assert final_review['source_files'] == manifest and final_review['reviewed_files'] == changed
assert final_review['qualified_source_files'] == qualified
for n in changed:
    (r / n).parent.mkdir(parents=True, exist_ok=True)
    (r / n).write_bytes(contents[n])
assert all(digest(normalized(r / n)) == value for n, value in manifest.items())
record['integrated'] = True
(e / 'integration-applied-03.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
print('Integrated world handoff after all four full qualification profiles passed.')
