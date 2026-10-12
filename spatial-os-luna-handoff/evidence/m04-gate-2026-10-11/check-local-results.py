"""Check completed gate artifacts. Human/primary review remains a separate step."""
import hashlib
import json
from pathlib import Path
import re
import socket
import xml.etree.ElementTree as ET

root = Path('/home/dflanag3/src/spacial_desktop-m04-gate-20261011/spatial-os-luna-handoff')
out = root / 'local/m04-full-gate-01'
gate_path = out / 'run.json'
gate = json.loads(gate_path.read_text())
assert socket.gethostname().split('.')[0] in ('sb16', 'sb16-2')
assert gate['status'] != 'running', 'Gate is still running; keep observing the existing job'
record = dict(host=gate['host'], candidate=gate['candidate'], phase_complete=False,
              gate_sha256=hashlib.sha256(gate_path.read_bytes()).hexdigest(), profiles={})
errors = []

def check(condition, message):
    if not condition:
        errors.append(message)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

check(gate['status'] == 'commands_passed_needs_review', 'Gate reports failure')
check(set(gate['profiles']) == {'Debug', 'ASan-UBSan'}, 'Both profiles must complete')
for name, expected in gate['source_sha256'].items():
    check(digest(root / name) == expected, 'Source changed: ' + name)
for name, expected in gate['installed_helpers'].items():
    check(digest(Path(name)) == expected, 'Installed helper changed: ' + name)
vendor_config = Path(gate['graphics']['__EGL_VENDOR_LIBRARY_FILENAMES'])
vendor = Path(json.loads(vendor_config.read_text())['ICD']['library_path'])
check(digest(vendor) == gate['vendor_sha256'], 'Qualified graphics vendor changed')
expected_commands = {'configure', 'build', 'inventory', 'harness', 'core', 'P10', 'bounded-storage-git'}
for profile, row in gate['profiles'].items():
    target = out / profile
    results = row['results']
    check(len(results) == len(expected_commands) and {r['name'] for r in results} == expected_commands,
          profile + ': missing or duplicate gate stage')
    check(all(r['exit_code'] == 0 for r in results), profile + ': nonzero command exit')
    build = root / 'local/m04-gate-m04-full-gate-01' / profile
    for name, expected in row.get('binary_sha256', {}).items():
        check(digest(build / name) == expected, profile + ': binary changed: ' + name)
    check(bool(row.get('binary_sha256')), profile + ': missing binary pins')
    check(digest(Path(row['server']['path'])) == row['server']['sha256'], profile + ': server changed')
    check(digest(Path(row['launcher'])) == row['launcher_sha256'], profile + ': launcher changed')
    inventory = json.loads((target / 'inventory.log').read_text())
    expected = [t['name'] for t in inventory['tests']]
    check(len(expected) == 141 and len(set(expected)) == 141, profile + ': incomplete or duplicate suite inventory')
    junit = ET.parse(target / 'core.xml')
    cases = list(junit.iter('testcase'))
    actual = [case.attrib['name'] for case in cases]
    check(sorted(actual) == sorted(expected), profile + ': executed suites differ from inventory')
    failed = [case.attrib['name'] for case in cases if case.find('failure') is not None
              or case.find('error') is not None or case.find('skipped') is not None
              or case.attrib.get('status') != 'run']
    check(not failed, profile + ': failed, skipped, or unrun suites: ' + repr(failed))
    harness = (target / 'harness.log').read_text()
    check(bool(re.search(r'Ran 51 tests .*\n\s*OK\s*$', harness)), profile + ': harness did not pass all 51 cases')
    acceptance = json.loads((target / 'P10.json').read_text())
    check(acceptance['status'] == 'passed' and acceptance['through'] == 'P10'
          and not acceptance['diagnostic_only'] and acceptance['not_selected'] == 0
          and acceptance['selected'] == acceptance['executed'] == acceptance['total_baseline'] == 151
          and len(acceptance['tests']) == 151 and len({t['test'] for t in acceptance['tests']}) == 151
          and all(t['status'] == 'passed' for t in acceptance['tests']), profile + ': incomplete P10 acceptance')
    storage = json.loads((target / 'bounded-storage-git/review.json').read_text())
    wanted = {'large_import_retirement', 'application_and_actor_quota', 'read_only', 'sibling_survival', 'restart'}
    check(storage['status'] == 'passed' and set(storage['results']) == wanted
          and all(result['passed'] for result in storage['results'].values()), profile + ': incomplete storage scenarios')
    record['profiles'][profile] = dict(suites=len(cases), harness=51,
                                       acceptance=acceptance['executed'], storage=len(storage['results']),
                                       failed_or_skipped=failed)

findings = []
pattern = re.compile(r'ERROR: (?:AddressSanitizer|LeakSanitizer)|runtime error:')
for path in out.rglob('*'):
    if path.is_file() and path.suffix in ('.log', '.stderr'):
        for number, line in enumerate(path.read_text(errors='replace').splitlines(), 1):
            if pattern.search(line):
                findings.append(dict(path=str(path.relative_to(out)), line=number, text=line[:300]))
check(not findings, 'Sanitizer markers require investigation')
record.update(status='ready_for_primary_review' if not errors else 'failed_checks',
              errors=errors, sanitizer_findings=findings)
(out / 'artifact-check.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
raise SystemExit(0 if not errors else 1)
