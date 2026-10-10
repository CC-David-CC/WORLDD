"""Verify frozen issuer retirement source and evidence before recording integration."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

assert len(sys.argv) == 2 and len(sys.argv[1]) == 64
root = Path(__file__).resolve().parents[1]
sha = lambda data: hashlib.sha256(data).hexdigest()
write = lambda path, value: path.write_bytes((json.dumps(value, indent=2) + '\n').encode())
archive = root / 'local/issuer-retirement-focused-01-sb16.tar.gz'
assert sha(archive.read_bytes()) == sys.argv[1]
evidence = root / 'evidence/control-recovery-2026-10-09'
target = evidence / 'issuer-retirement-01/sb16'
assert not target.exists()
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    assert len(members) == len({m.name for m in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert member.name.startswith('issuer-retirement-01/sb16/')
        path = evidence.joinpath(*name.parts)
        assert path.resolve().is_relative_to(evidence.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream: stream.write(tar.extractfile(member).read())
focused = json.loads((target / 'focused-review.json').read_bytes())
run = json.loads((target / 'checks/review.json').read_bytes())
assert run['status'] == 'passed' and focused['failed'] == focused['skipped'] == 0
assert focused['profiles']['debug'] == focused['profiles']['runtime-sanitize']
assert focused['profiles']['debug']['retirement'] == [21, 6]
assert len(focused['profiles']['debug']['focused']) == 12
assert all(sha((target / n).read_bytes()) == digest for n, digest in focused['attachments'].items())
integrated = {}
for name, digest in run['source'].items():
    integrated[name] = sha((root / name).read_bytes().replace(b'\r\n', b'\n'))
    assert integrated[name] == digest, name
assert len(integrated) == 336 and len(run['locked']) == 41
assert all(sha((root / n).read_bytes().replace(b'\r\n', b'\n')) == digest for n, digest in run['locked'].items())
baseline = json.loads((evidence / 'retirement-baseline01/sb16/baseline-review.json').read_bytes())
assert all(sha((evidence / 'retirement-baseline01/sb16' / n).read_bytes()) == digest
    for n, digest in baseline['attachments'].items())
count = focused['selected_cases_per_profile']
files = {
    'CMakeLists.txt': 'Adds two focused identity suites with real issuer and client binaries. Existing checks stay registered.',
    'proto/spatial/identity/v1/key.proto': 'Adds purpose 8 without changing earlier signature purposes.',
    'proto/spatial/identity/v1/issuer.proto': 'Adds retirement challenge, result, operations 32/33, reply 29, and explicit barrier retirement evidence. Existing fields stay unchanged.',
    'src/identity/issuer.hpp': 'Declares the two typed issuer operations with required connection context.',
    'src/identity/protocol.cpp': 'Routes retirement only with TLS context and advertises the new capability.',
    'src/identity/participants.hpp': 'Checks exact owner shape, fence and digest lengths, result registration, key separation, and bounded revision history.',
    'src/identity/issuer.cpp': 'Requires the exact consumed authorization and committed fence. Fresh supervisor proofs bind request, issuer incarnation, nonce, purpose, and TLS connection. One transaction inserts immutable retirement and deactivates the exact generation. Applied revisions and obligations remain unchanged. Exact retries preserve the result; changed receipts fail. Barrier counts and pages use retirement explicitly. Schema 10 requires explicit migration.',
    'src/identity/issuer_client.cpp': 'Checks challenge context and signature purpose, retains bounded expiring contexts, consumes completion context, validates the actual proof and exact result, and checks retirement against its barrier generation and applied history.',
    'tests/core/test_authority_retirement.py': 'Uses real TLS, issuer, production client, independent signatures, and database inspection. Covers 21 scenarios including unread replies, rollback, history, replacement, restart, no TLS, and explicit migration.',
    'tests/core/test_authority_retirement_client.py': 'Six scenarios use independent TLS fault peers for substituted challenges, results, proofs, connection context, unsolicited success, denial retries, and barrier history.',
    'docs/AUTHORITY_RETIREMENT.txt': 'Records the contract and separates authenticated issuer receipt checks from actual coordinator cleanup proof.',
    'controller helpers': 'Freeze and hash source, preserve the failing baseline, run selected profiles, and verify collected evidence. No normal service is migrated or restarted.'
}
for name in ('test_authority_fencing.py', 'test_identity_bootstrap.py', 'test_identity_participant_barrier.py',
        'test_identity_participants.py', 'test_identity_revocation.py', 'test_launch_authorization.py', 'test_supervisor_registry.py'):
    files['tests/core/' + name] = 'Historical migration fixture drops the new table before reconstructing its old schema. Successful migrations expect version 10. Existing identity/history preservation and failed-migration rollback assertions remain unchanged.'
review = dict(status='reviewed_focused_pass_phase_gate_pending', reviewer='primary agent', host='sb16',
    source_record='checks/review.json', integrated_source=integrated, selected_cases_per_profile=count,
    new_cases=27, failed=0, skipped=0, profiles=['Debug', 'runtime ASan/UBSan'], archive_sha256=sys.argv[1],
    attachments_verified=len(focused['attachments']), post_test_source_changes=0, file_review=files,
    limitations=focused['limitations'], baseline=focused['baseline'])
write(target / 'primary-review.json', review)
status = root / 'CURRENT_STATE.md'; text = status.read_text(encoding='utf-8')
heading = '# Implementation status\n'; assert text.startswith(heading)
update = f'''
## 2026-10-10: Authenticated issuer retirement passes focused checks

The issuer now retires an exact fenced generation after a fresh proof from its registered supervisor.
The proof binds the cleanup digest, fence, launch, generation, issuer incarnation, and TLS connection.
One transaction saves retirement and deactivates that generation. Applied revision history remains unchanged.
Barrier replies distinguish retirement from service acknowledgement. Other participants keep their obligations.
Old service connections remain fenced. Replacement needs fresh authorization and current synchronization.

All {count} selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These include 21 issuer retirement scenarios, six client-validation scenarios, and twelve related suites.
All 336 tested source files match. All 41 locked files remain unchanged.
The explicit issuer migration is version 9 to 10. No normal host database was migrated.
Read evidence/control-recovery-2026-10-09/issuer-retirement-01/sb16/primary-review.json.

These protocol tests supply fixture cleanup digests. They do not prove actual external cleanup.
Next: connect the trusted coordinator signer to its checked, durable dependent receipt.
Then resolve the earlier participant-approval startup failure and run the complete M02 phase gate on both hosts.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.

'''
status.write_bytes((heading + update + text[len(heading):]).encode())
contract = root / 'docs/AUTHORITY_RETIREMENT.txt'; text = contract.read_text(encoding='utf-8')
old = 'Status: Implementation contract. Production retirement remains unimplemented.'
assert text.count(old) == 1
contract.write_bytes(text.replace(old, 'Status: Issuer and client protocol implemented with focused checks. Trusted coordinator integration remains pending.').encode())
task_path = root / 'tasks/m1/identity.control_recovery.json'; task = json.loads(task_path.read_bytes())
task['current_step'] = 'connect_checked_cleanup_to_issuer_retirement'
task['remaining'] = ['Connect the trusted coordinator signer and durable cleanup receipt to authenticated issuer retirement.',
    'Resolve the preserved participant-approval startup failure before the full phase gate.',
    'Qualify the complete M02 path and cumulative profiles on both hosts.']
task['issuer_retirement'] = dict(state='Issuer and client protocol passed focused checks. Coordinator integration remains pending.',
    evidence='evidence/control-recovery-2026-10-09/issuer-retirement-01/sb16/primary-review.json',
    contract='docs/AUTHORITY_RETIREMENT.txt', host='sb16', cases_per_profile=count, new_cases=27,
    profiles=review['profiles'], failed=0, skipped=0, full_phase_gate='pending on both hosts')
write(task_path, task)
driver_path = root / 'local/driver-state.json'; driver = json.loads(driver_path.read_bytes())
driver['current_step'] = task['current_step']
driver['last_turn'] = dict(classification='progress', detail=f'Issuer retirement passed {count} selected cases per profile. Trusted coordinator integration remains pending.')
driver.setdefault('active_retirement_check', {}).update(status='passed_and_collected', host='sb16', profiles=focused['profiles'],
    evidence=task['issuer_retirement']['evidence'])
write(driver_path, driver)
with (root.parent / 'M02_REVOCATION_BARRIER_DEBUG.txt').open('ab') as stream:
    stream.write(f'''\n2026-10-10: Issuer retirement passed {count} selected cases per profile on sb16.
All 27 new issuer/client scenarios passed in Debug and runtime sanitizers. No failures or skips.
Evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/issuer-retirement-01/sb16/primary-review.json.
The protocol baseline rejection remains in evidence/control-recovery-2026-10-09/retirement-baseline01/sb16.
Fixture cleanup digests verify the authenticated issuer boundary. Actual coordinator cleanup-to-retirement remains pending.
The explicit issuer schema migration is 9 to 10. No normal host service or database changed.
The earlier participant-approval failure and complete two-host M02 qualification remain pending.
M02 and Spatial-M1 remain incomplete.
'''.encode())
print(json.dumps(dict(source_files=336, selected_cases_per_profile=count, attachments=len(focused['attachments']))))
