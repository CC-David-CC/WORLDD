"""Review frozen cross-host results; do not execute scenarios."""
import hashlib
import json
import re
import socket
import sys
from pathlib import Path

root = Path('/home/dflanag3/src/spacial_desktop-m05-gate-20261012/spatial-os-luna-handoff')
out = root / 'local/m05-crosshost-gate-01'
record = json.loads((out / 'run.json').read_text())
assert record['status'] == 'commands_passed_needs_review'
assert record['host'] == socket.gethostname().split('.')[0]
assert record['candidate'] == '6b9619e5b00be3011068b11c2c7d8ed48d5a0c47'
assert record['prerequisites'] == record['final_prerequisites']
expected = [(p, s, n) for p in ('Debug', 'ASan-UBSan') for s, n in
            [('world_remote_keys', 5), ('world_topology', 1), ('session_crosshost_recovery', 2)]]
assert [(r['profile'], r['suite'], r['cases']) for r in record['results']] == expected
assert all(r['exit_code'] == 0 for r in record['results'])
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
files = {str(p.relative_to(out)): p for p in out.rglob('*') if p.is_file() and p != out / 'run.json'}
assert set(files) == set(record['artifacts'])
for name, path in files.items():
    assert sha(path) == record['artifacts'][name], name
for profile, suite, count in expected:
    text = (out / profile / suite / 'output.log').read_text(errors='replace')
    assert re.search(r'^Ran ' + str(count) + r' tests? in .*\n\nOK$', text, re.M), (profile, suite)
    assert not re.search(r'\bskipped\b', text), (profile, suite)
    if suite == 'session_crosshost_recovery':
        receipts = [json.loads(line) for line in text.splitlines() if line.startswith('{')]
        cleanup = [r for r in receipts if 'scope_removed' in r]
        units = [r for r in receipts if 'unit_after' in r]
        assert len(cleanup) == len(units) == 1
        assert cleanup[0]['scope_removed'] and cleanup[0]['exit_code'] == 0
        assert units[0]['query_exit'] == 0 and 'ActiveState=inactive' in units[0]['unit_after']
logs = [p for p in files.values() if p.suffix == '.log']
for p in logs:
    text = p.read_text(errors='replace')
    assert not re.search(r'ERROR: (AddressSanitizer|LeakSanitizer)|SUMMARY: (AddressSanitizer|UndefinedBehaviorSanitizer)|runtime error:', text), str(p)
    assert not re.search(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----', text), str(p)
keys = [p for p in logs if p.name.endswith('-remote-actor-key.log')]
assert len(keys) == 12
for p in keys:
    text = p.read_text()
    assert text.count(' generated ') == text.count(' removed temporary actor key') == 1, str(p)
    assert ' verified-signature-purpose ' in text, str(p)
    assert all(line.startswith(record['peer'] + ' ') for line in text.splitlines()), str(p)
sys.path.insert(0, str(root / 'local/m05-gate-m05-full-gate-01/Debug/python'))
import world_topology_pb2
endpoints = []
for profile in ('Debug', 'ASan-UBSan'):
    for generation in (1, 2):
        p = out / profile / 'world_topology/services' / f'topology-endpoint-{generation}.pb'
        receipt = world_topology_pb2.WorldEndpoint.FromString(p.read_bytes())
        assert receipt.protocol_version == 1 and receipt.host == record['peer']
        assert receipt.address == {'sb16': '10.0.7.49', 'sb16-2': '10.0.7.94'}[record['peer']]
        assert receipt.process_id > 1 and 0 < receipt.port < 65536
        endpoints.append(dict(profile=profile, generation=generation, host=receipt.host, pid=receipt.process_id))
leftovers = [str(p) for prefix in ('spatial-m05-key-', 'spatial-m05-topology-', 'spatial-m05-recovery-')
             for p in Path('/tmp').glob(prefix + '*')]
assert not leftovers, leftovers
report = dict(host=record['host'], peer=record['peer'], candidate=record['candidate'],
              status='crosshost_artifacts_verified', phase_complete=False, cases=16, failed=0, skipped=0,
              files=len(files), logs=len(logs), remote_key_receipts=len(keys), endpoints=endpoints,
              leftover_fixture_directories=leftovers, run_sha256=sha(out / 'run.json'),
              auditor_sha256=sha(Path(__file__)))
(root / 'local/m05-crosshost-artifact-audit.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
