"""Check preserved cross-host receipts without invoking runtime services."""
import hashlib
import json
from pathlib import Path
import re

root = Path(__file__).resolve().parent
manifest = root / 'evidence-files.sha256'
entries = []
for line in manifest.read_text().splitlines():
    digest, name = line.split('  ', 1)
    assert re.fullmatch('[0-9a-f]{64}', digest)
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    entries.append(name)
assert len(entries) == len(set(entries)) == 83
host_hashes = []
for host in ('sb16', 'sb16-2'):
    lines = (root / 'source/host-hash-receipts' / (host + '.sha256.txt')).read_text().splitlines()
    assert lines[0] == host
    matches = [line.split()[0] for line in lines if line.endswith('/run03-bundle.tar.gz')]
    assert len(matches) == 1 and re.fullmatch('[0-9a-f]{64}', matches[0])
    host_hashes.append(matches[0])
assert host_hashes[0] == host_hashes[1]
assert host_hashes[0] in (root / 'README.md').read_text()
result = dict(source_commit='c93e02b9695fe6716bf8c32054d06cc7caf084e9',
              archive_sha256=host_hashes[0], verified_evidence_files=len(entries), profiles={})
for profile in ('debug-corrected', 'asan-corrected'):
    directory = root / 'runtime' / profile
    for filename in ('runner.exit', 'harness.exit'):
        assert (directory / filename).read_text().strip() == '0'
    probes = list(directory.glob('probe-*.exit'))
    assert len(probes) == 6
    assert all(path.read_text().strip() == '0' for path in probes)
    cleanup = json.loads((directory / 'cleanup.json').read_text())
    assert cleanup == dict(complete=True, errors=[])
    output = (directory / 'harness.stdout').read_text()
    rows = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
    cases = [row for row in rows if row.get('kind') == 'case_result']
    assert len(cases) == 6
    assert {(row['source_host'], row['scenario']) for row in cases} == {
        (host, scenario) for host in ('sb16', 'sb16-2') for scenario in (1, 2, 3)}
    assert all(row['source_host'] != row['destination_host'] for row in cases)
    assert 'cross-host actor transition protobuf plan passed' in output
    authorities = [row for row in rows if row.get('kind') == 'authority']
    assert {row['hostname'] for row in authorities} == {'sb16', 'sb16-2'}
    assert len({row['machine_id'] for row in authorities}) == 2
    assert all(row['listener_inodes'] and row['owners'] for row in authorities)
    assert 'PASS' in (directory / 'cleanup-transport-regression.txt').read_text()
    for path in directory.rglob('*'):
        if path.is_file():
            assert not re.search(r'ERROR: AddressSanitizer|ERROR: LeakSanitizer|runtime error:', path.read_text(errors='replace'))
    result['profiles'][profile] = dict(cases_passed=6, slow_stop_passed=1, cleanup_complete=True)
injection = root / 'runtime/issuer-record-injection'
assert (injection / 'runner.exit').read_text().strip() == '1'
assert (injection / 'issuer.exit').read_text().strip() == '0'
assert json.loads((injection / 'cleanup.json').read_text()) == dict(complete=True, errors=[])
assert 'PASS' in (injection / 'issuer-record-failure-regression.txt').read_text()
initial = root / 'runtime/debug-initial-cleanup-failed'
assert (initial / 'runner.exit').read_text().strip() != '0'
for name in ('recovery-stop.exit', 'recovery-remove.exit', 'sb16-final-state.exit'):
    assert (initial / name).read_text().strip() == '0'
result.update(status='focused_pass', coordinator_cases_passed=12, slow_stop_passed=2,
              startup_failure_recovery_passed=1, preserved_initial_failure='FAIL_CLEANUP',
              native_presentation='UNRUN', phase_complete=False)
(root / 'primary-review.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8', newline='\n')
print(json.dumps(result, indent=2))
