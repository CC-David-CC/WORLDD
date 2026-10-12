"""Audit completed frozen M05 profile artifacts without rerunning tests."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser()
parser.add_argument('profile', choices=('Debug', 'ASan-UBSan'))
args = parser.parse_args()
root = Path('/home/dflanag3/src/spacial_desktop-m05-gate-20261012/spatial-os-luna-handoff')
gate = root / 'local/m05-full-gate-01'
record = json.loads((gate / 'run.json').read_text())
assert record['candidate'] == '6b9619e5b00be3011068b11c2c7d8ed48d5a0c47'
assert record['host'] == socket.gethostname().split('.')[0]
row = record['profiles'][args.profile]
assert row['status'] == 'commands_passed_needs_review', row['status']
expected_commands = ['configure', 'build', 'inventory', 'harness', 'core', 'P10', 'bounded-storage-git']
assert [r['name'] for r in row['results']] == expected_commands
assert all(r['exit_code'] == 0 for r in row['results'])
target = gate / args.profile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


for name, expected in record['source_sha256'].items():
    assert sha(root / name) == expected, name
build = root / 'local/m05-gate-m05-full-gate-01' / args.profile
for name, expected in row['binary_sha256'].items():
    assert sha(build / name) == expected, name
assert sha(Path(row['server']['path'])) == row['server']['sha256']
assert sha(Path(row['launcher'])) == row['launcher_sha256']
for name, expected in record['installed_helpers'].items():
    assert sha(Path(name)) == expected, name
inventory = json.loads((target / 'inventory.log').read_text())['tests']
assert len(inventory) == 142
assert not [t['name'] for t in inventory for p in t.get('properties', [])
            if p['name'] == 'DISABLED' and p['value']]
cases = list(ET.parse(target / 'core.xml').getroot().iter('testcase'))
assert len(cases) == 142
assert len({c.attrib['name'] for c in cases}) == 142
assert {c.attrib['name'] for c in cases} == {t['name'] for t in inventory}
assert all(c.find('failure') is None and c.find('error') is None and c.find('skipped') is None for c in cases)
harness = (target / 'harness.log').read_text(errors='replace')
assert re.search(r'Ran 51 tests in .*\n\nOK', harness)
assert not re.search(r'\bskipped\b', harness)
acceptance = json.loads((target / 'P10.json').read_text())
assert acceptance['status'] == 'passed' and not acceptance['diagnostic_only']
assert acceptance['through'] == 'P10'
assert acceptance['selected'] == acceptance['executed'] == acceptance['total_baseline'] == len(acceptance['tests']) == 151
assert acceptance['not_selected'] == 0
assert all(t['status'] == 'passed' for t in acceptance['tests'])
storage = json.loads((target / 'bounded-storage-git/review.json').read_text())
assert storage['status'] == 'passed'
assert set(storage['results']) == {'large_import_retirement', 'application_and_actor_quota',
                                  'read_only', 'sibling_survival', 'restart'}
assert all(result['passed'] for result in storage['results'].values())
assert not storage['mounted']
assert all(storage[key]['exit_code'] == 0 for key in ('service_stop', 'unmount', 'detach'))
assert 'ActiveState=inactive' in storage['service_after']
assert len(storage['owner_unit_cleanup']) == 2
assert all('ActiveState=inactive' in status for status in storage['owner_unit_cleanup'].values())
assert not Path(storage['scope']).exists()
assert not os.path.ismount(target / 'bounded-storage-git/volume')
logs = list(target.rglob('*.log'))
findings = [str(p.relative_to(target)) for p in logs if re.search(
    r'ERROR: (AddressSanitizer|LeakSanitizer)|SUMMARY: (AddressSanitizer|UndefinedBehaviorSanitizer)|runtime error:',
    p.read_text(errors='replace'))]
assert not findings, findings
artifacts = ['core.xml', 'P10.json', 'harness.log', 'inventory.log', 'bounded-storage-git/review.json']
report = dict(host=record['host'], candidate=record['candidate'], profile=args.profile,
    status='profile_artifacts_verified', phase_complete=False, core_suites=142, harness_tests=51,
    acceptance_tests=151, storage_scenarios=5, failed=0, skipped=0, scanned_logs=len(logs),
    sanitizer_findings=findings, source_files_verified=len(record['source_sha256']),
    runtime_binaries_verified=len(row['binary_sha256']), audit_sha256=sha(Path(__file__)),
    artifacts={name: sha(target / name) for name in artifacts})
(gate / (args.profile + '-artifact-audit.json')).write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
