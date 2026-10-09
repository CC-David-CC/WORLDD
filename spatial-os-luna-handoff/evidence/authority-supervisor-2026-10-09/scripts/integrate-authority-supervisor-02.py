"""Prepare integration; apply only after exact cumulative qualification and a primary review."""
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
evidence = r / 'evidence/authority-supervisor-2026-10-09'
draft = r / 'local/authority-supervisor-draft-02'
base = json.loads((r / 'evidence/process-termination-2026-10-09/integration-applied-02.json').read_text(encoding='utf-8'))['files']
manifest = json.loads((r / 'local/authority-supervisor-candidate-02.json').read_text(encoding='utf-8'))['files']
assert len(base) == 242 and len(manifest) == 281 and set(base) <= set(manifest)
changed = sorted(name for name in manifest if base.get(name) != manifest[name])
assert len(changed) == 77
data = {}
for tree, files in ((r, base), (draft, manifest)):
    for name, digest in files.items():
        relative = Path(name)
        assert not relative.is_absolute() and '..' not in relative.parts
        value = (tree / relative).read_bytes().replace(b'\r\n', b'\n')
        assert hashlib.sha256(value).hexdigest() == digest, name
        if tree == draft:
            data[name] = value
for name in set(manifest) - set(base):
    assert not (r / name).exists(), 'Untracked integration destination already exists: ' + name
locked = json.loads((r / 'acceptance.lock.json').read_text(encoding='utf-8'))['files']
assert len(locked) == 41
for name, digest in locked.items():
    assert hashlib.sha256((draft / name).read_bytes()).hexdigest() == digest, name
audit = json.loads((evidence / 'integration-test-audit-02.json').read_text(encoding='utf-8'))
assert audit['changed_files'] == changed and audit['existing_test_methods'] == 1378
assert audit['identical_assertion_lists'] == 1372 and len(audit['reviewed_assertion_changes']) == 6

patch = []
for name in changed:
    before = (r / name).read_text(encoding='utf-8').splitlines(True) if name in base else []
    after = data[name].decode('utf-8').splitlines(True)
    patch.extend(difflib.unified_diff(before, after,
        fromfile='a/' + name if name in base else '/dev/null', tofile='b/' + name))
(evidence / 'integration-candidate-02.patch').write_text(''.join(patch), encoding='utf-8', newline='\n')
record = dict(files=manifest, changed=changed, base_files=242, source_files=281, locked_files=41,
    required_hosts=['sb16', 'sb16-2'], required_profiles=['debug', 'runtime-sanitize'],
    expected_core_checks=1383, expected_core_suites=91, expected_harness_checks=51, expected_P10_cases=151,
    integrated=False, production_review='Required before --apply. This preparation does not attest code review.')
(evidence / 'integration-prepared-02.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8', newline='\n')
if not args.apply:
    print('Prepared 77-file integration diff. Verified all 281 candidate hashes and 41 locks. No production file changed.')
    raise SystemExit(0)

review = json.loads((evidence / 'integration-primary-review-02.json').read_text(encoding='utf-8'))
assert review['source_files'] == manifest and review['reviewed_files'] == changed
assert review['unresolved_defects'] == [] and review['reviewer'] == 'primary agent'
for host in ('sb16', 'sb16-2'):
    folder = evidence / 'full-candidate-02' / host
    summary = json.loads((folder / 'summary-final.json').read_text(encoding='utf-8'))
    assert summary['candidate'] == 2 and summary['source_files'] == 281 and summary['locked_files'] == 41
    assert json.loads((folder / 'source-manifest.json').read_text(encoding='utf-8'))['files'] == manifest
    for profile in ('debug', 'runtime-sanitize'):
        result = summary[profile]
        assert tuple(result[key] for key in ('core_checks', 'core_suites', 'harness_checks', 'P10_cases', 'failed', 'skipped')) == (1383, 91, 51, 151, 0, 0)
        run = json.loads((folder / profile / 'run-full.json').read_text(encoding='utf-8'))
        commands = ['configure', 'build', 'harness', 'ownership', 'supervisor', 'focused', 'core', 'P10']
        assert [item['name'] for item in run['results']] == commands and all(item['exit_code'] == 0 for item in run['results'])
        names = []
        for part, suites, checks in (('ownership', 1, 3), ('supervisor', 1, 14), ('focused', 23, 366), ('core', 66, 1000)):
            log = (folder / profile / (part + '.log')).read_text(encoding='utf-8')
            counts = list(map(int, re.findall(r'^\d+: Ran (\d+) tests? in ', log, re.M)))
            assert len(counts) == suites and sum(counts) == checks
            assert f'100% tests passed, 0 tests failed out of {suites}' in log
            assert '... skipped' not in log and 'skipped=' not in log
            listed = re.findall(r'^\s*Start\s+\d+:\s+(\S+)', log, re.M)
            assert len(listed) == suites
            names.extend(listed)
        assert len(names) == len(set(names)) == 91
        harness = (folder / profile / 'harness.log').read_text(encoding='utf-8')
        assert 'Ran 51 tests' in harness and harness.rstrip().endswith('OK')
        report = json.loads((folder / profile / 'P10.json').read_text(encoding='utf-8'))
        assert report['status'] == 'passed' and report['executed'] == len(report['tests']) == 151
        assert all(item['status'] == 'passed' for item in report['tests'])
for name in changed:
    path = r / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data[name])
for name, digest in manifest.items():
    assert hashlib.sha256((r / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == digest, name
record.update(integrated=True, production_review='Primary review and complete four-profile qualification verified before applying.')
(evidence / 'integration-applied-02.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8', newline='\n')
print('Integrated the reviewed 77-file increment. All 281 candidate hashes match the qualified source.')
