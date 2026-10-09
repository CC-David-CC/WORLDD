"""Prepare the reviewed five-file integration; require complete evidence before apply."""
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
e = r / 'evidence/supervisor-broker-handoff-2026-10-09'
base = json.loads((r / 'evidence/authority-supervisor-2026-10-09/integration-applied-02.json').read_text())['files']
manifest = json.loads((r / 'local/supervisor-broker-handoff-candidate-02.json').read_text())['files']
draft = r / 'local/supervisor-broker-handoff-draft-02'
assert len(base) == 281 and len(manifest) == 283 and set(base) <= set(manifest)
changed = sorted(name for name in manifest if base.get(name) != manifest[name])
assert changed == ['CMakeLists.txt', 'proto/spatial/supervisor/v1/launch.proto', 'src/supervisor/main.cpp',
    'tests/native/test_supervisor_broker_handoff.py', 'tests/native/test_supervisor_broker_handoff_regressions.py']
for tree, files in ((r, base), (draft, manifest)):
    for name, digest in files.items():
        data = (tree / name).read_bytes().replace(b'\r\n', b'\n')
        assert hashlib.sha256(data).hexdigest() == digest, name
for name in set(manifest) - set(base):
    assert not (r / name).exists(), 'New production destination is already present'
locked = json.loads((r / 'acceptance.lock.json').read_text())['files']
assert len(locked) == 41
for name, digest in locked.items():
    assert manifest[name] == digest
assert all(manifest[name] == digest for name, digest in base.items() if name.startswith('tests/'))
review = json.loads((e / 'integration-primary-review-02.json').read_text())
assert review['reviewer'] == 'primary agent' and review['source_files'] == manifest
assert review['reviewed_files'] == changed and not review['unresolved_static_defects']
patch = []
for name in changed:
    before = (r / name).read_text().splitlines(True) if name in base else []
    after = (draft / name).read_text().splitlines(True)
    patch.extend(difflib.unified_diff(before, after, fromfile='a/' + name if name in base else '/dev/null', tofile='b/' + name))
(e / 'integration-candidate-02.patch').write_text(''.join(patch), newline='\n')
record = dict(files=manifest, changed=changed, base_files=281, source_files=283, locked_files=41,
    expected_core_checks=1393, expected_core_suites=93, expected_harness_checks=51,
    expected_P10_cases=151, integrated=False)
(e / 'integration-prepared-02.json').write_text(json.dumps(record, indent=2) + '\n')
if not args.apply:
    print('Prepared reviewed five-file broker integration. Production remains unchanged; full gates required.')
    raise SystemExit(0)
for host in ('sb16', 'sb16-2'):
    focused = json.loads((e / 'candidate-02' / host / 'summary-final.json').read_text())
    assert focused['schema']['existing_descriptors_unchanged'] and focused['schema']['field_number'] == 11
    folder = e / 'full-candidate-02' / host
    summary = json.loads((folder / 'summary-final.json').read_text())
    assert summary['candidate'] == 2 and summary['source_files'] == 283 and summary['locked_files'] == 41
    assert json.loads((folder / 'source-manifest.json').read_text())['files'] == manifest
    for profile in ('debug', 'runtime-sanitize'):
        result = summary[profile]
        assert tuple(result[k] for k in ('core_checks', 'core_suites', 'harness_checks', 'P10_cases', 'failed', 'skipped')) == (1393, 93, 51, 151, 0, 0)
        run = json.loads((folder / profile / 'run-full.json').read_text())
        assert [x['name'] for x in run['results']] == ['configure', 'build', 'harness', 'handoff', 'regressions', 'focused', 'core', 'P10']
        assert all(x['exit_code'] == 0 for x in run['results'])
        names = []
        for part, suites, count in (('handoff', 1, 1), ('regressions', 1, 9), ('focused', 9, 102), ('core', 82, 1281)):
            text = (folder / profile / (part + '.log')).read_text()
            counts = [int(value) for value in re.findall(r'^\d+: Ran (\d+) tests? in ', text, re.M)]
            assert len(counts) == suites and sum(counts) == count
            assert f'100% tests passed, 0 tests failed out of {suites}' in text
            assert 'skipped=' not in text and '... skipped' not in text
            listed = re.findall(r'^\s*Start\s+\d+:\s+(\S+)', text, re.M)
            assert len(listed) == suites
            names.extend(listed)
        assert len(names) == len(set(names)) == 93
        harness = (folder / profile / 'harness.log').read_text()
        assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
        p10 = json.loads((folder / profile / 'P10.json').read_text())
        assert p10['status'] == 'passed' and p10['executed'] == len(p10['tests']) == 151
        assert all(item['status'] == 'passed' for item in p10['tests'])
for name in changed:
    (r / name).write_bytes((draft / name).read_bytes())
for name, digest in manifest.items():
    assert hashlib.sha256((r / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == digest
record['integrated'] = True
(e / 'integration-applied-02.json').write_text(json.dumps(record, indent=2) + '\n')
print('Integrated reviewed broker handoff after all four full qualification profiles passed.')
