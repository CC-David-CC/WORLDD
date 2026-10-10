"""Verify the frozen coordinator source and focused evidence before integration."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

assert len(sys.argv) == 2 and len(sys.argv[1]) == 64
root = Path(__file__).resolve().parents[1]
sha = lambda data: hashlib.sha256(data).hexdigest()
write = lambda path, value: path.write_bytes((json.dumps(value, indent=2) + '\n').encode())
archive = root / 'local/coordinator-retirement-focused-02-sb16.tar.gz'
assert sha(archive.read_bytes()) == sys.argv[1]
evidence = root / 'evidence/control-recovery-2026-10-09'
target = evidence / 'coordinator-retirement-02/sb16'
assert not target.exists()
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    assert len(members) == len({m.name for m in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert member.name.startswith('coordinator-retirement-02/sb16/')
        path = evidence.joinpath(*name.parts)
        assert path.resolve().is_relative_to(evidence.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream: stream.write(tar.extractfile(member).read())
focused = json.loads((target / 'focused-review.json').read_bytes())
run = json.loads((target / 'checks/review.json').read_bytes())
assert run['status'] == 'passed' and focused['failed'] == focused['skipped'] == 0
assert focused['profiles']['debug'] == focused['profiles']['runtime-sanitize']
assert focused['profiles']['debug']['retirement'] == [8]
assert len(focused['profiles']['debug']['focused']) == 10
assert all(sha((target / n).read_bytes()) == h for n, h in focused['attachments'].items())
integrated = {n: sha((root / n).read_bytes().replace(b'\r\n', b'\n')) for n in run['source']}
assert integrated == run['source'] and len(integrated) == 337 and len(run['locked']) == 41
assert all(sha((root / n).read_bytes().replace(b'\r\n', b'\n')) == h for n, h in run['locked'].items())
baseline = evidence / 'coordinator-retirement-baseline01/sb16'
original = json.loads((baseline / 'baseline-review.json').read_bytes())
assert all(sha((baseline / n).read_bytes()) == h for n, h in original['attachments'].items())
failure = evidence / 'coordinator-retirement-failure01/sb16'
failed = json.loads((failure / 'failure-review.json').read_bytes())
assert failed['failed'] == 7 and failed['passed'] == 1
assert all(sha((failure / n).read_bytes()) == h for n, h in failed['attachments'].items())
prior = json.loads((failure / 'checks/review.json').read_bytes())
assert {n for n, h in run['source'].items() if prior['source'].get(n) != h} == {
    'src/testing/session_application_probe.cpp', 'src/supervisor/session_coordinator.hpp'}
count = focused['selected_cases_per_profile']
files = {
    'CMakeLists.txt': 'Registers eight native coordinator scenarios against production processes in a delegated scope. Existing tests stay registered.',
    'src/supervisor/launch.hpp': 'The retirement writer is private. Only SessionCoordinator can call it with retained cleanup and private authorization configuration.',
    'src/supervisor/launch.cpp': 'Reads launch schemas 1 through 3. Validates saved retirement against the exact fenced world and raw dependent-record digest. Requires retained cleanup bytes, the original authorization and signer, and a checked production issuer challenge. Persists the issuer result separately in one schema 2-to-3 transaction. Exact retries preserve bytes and do not need the key again. Opening a journal creates no live ownership.',
    'src/supervisor/session_coordinator.hpp': 'Exposes retirement only on the existing trusted coordinator interface, with an exact owner argument.',
    'src/supervisor/session_coordinator.cpp': 'Retains private authorization, successful durable cleanup, and the returned retirement. Requires an exact registered stopped owner. Caller cannot supply cleanup, digest, issuer destination, or key. Rejects changed saved results.',
    'src/testing/session_probe.proto': 'Adds test-only operation 16 and result 14. Existing fields remain unchanged. Actors never receive this channel.',
    'src/testing/session_application_probe.cpp': 'Installs the SIGPIPE disposition required by IssuerClient, then forwards the test operation directly to the production coordinator.',
    'tests/native/test_coordinator_retirement.py': 'Eight real-process scenarios use independent database reads, SHA-256, native input and resize events, issuer write faults and local transaction contention. They cover success, preserved application authority, exact retries, rejected contexts and tampering, failed storage, and denied replacement from reopened journals.',
    'docs/COORDINATOR_RETIREMENT.txt': 'Records the proof, local durability, retry, ownership, and failure contract without claiming automatic restart recovery.',
    'controller helpers': 'Preserve the actual missing-step baseline. Freeze source and locked files. Check both selected profiles and evidence hashes. No normal session or database is changed.'
}
review = dict(status='reviewed_focused_pass_phase_gate_pending', reviewer='primary agent', host='sb16',
    integrated_source=integrated, selected_cases_per_profile=count, new_cases=8, failed=0, skipped=0,
    profiles=['Debug', 'runtime ASan/UBSan'], archive_sha256=sys.argv[1], attachments_verified=len(focused['attachments']),
    post_test_source_changes=0, file_review=files, limitations=focused['limitations'], baseline=focused['baseline'],
    preserved_failure='../../coordinator-retirement-failure01/sb16/failure-review.json',
    correction='Only driver SIGPIPE setup and the documented coordinator transport precondition changed after candidate01.')
write(target / 'primary-review.json', review)
status = root / 'CURRENT_STATE.md'; text = status.read_text(encoding='utf-8')
heading = '# Implementation status\n'; assert text.startswith(heading)
update = f'''
## 2026-10-10: Actual coordinator cleanup-to-retirement passes focused checks

The retained coordinator now signs retirement after verified world crash cleanup and durable broker reservation recovery.
It hashes the exact saved dependent receipt. Callers cannot supply cleanup, keys, digests, or issuer destinations.
The issuer retires only that fenced generation. The broker and independently authorized application remain usable.
The coordinator reports success after saving the issuer result in launch journal schema 3.
Exact retries preserve the original receipt, including after an issuer commit followed by local storage failure.
Reopened journals cannot recreate ownership or start replacement execution.

All {count} selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These include eight new native scenarios and ten related suites.
All 337 tested source files match. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/coordinator-retirement-02/sb16/primary-review.json.
The missing-step baseline and first integration failure remain preserved.
The first driver omitted required SIGPIPE setup. Its correction leaves the assertions and retirement logic unchanged.
No normal host session or database changed.

Remaining M02 work includes broker retirement, replacement recovery, failure races, cross-host cancellation, and full two-host qualification.
The previously observed participant-approval startup failure remains unresolved.
This path requires retained coordinator ownership. It does not implement automatic coordinator restart recovery.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.

'''
status.write_bytes((heading + update + text[len(heading):]).encode())
contract = root / 'docs/COORDINATOR_RETIREMENT.txt'
text = contract.read_text(encoding='utf-8')
old = 'Status: Integration contract. Coordinator retirement remains unimplemented.'
assert text.count(old) == 1
contract.write_bytes(text.replace(old, 'Status: Implemented with focused Debug and sanitizer checks. Complete M02 qualification remains pending.').encode())
contract = root / 'docs/AUTHORITY_RETIREMENT.txt'; text = contract.read_text(encoding='utf-8')
old = 'Status: Issuer and client protocol implemented with focused checks. Trusted coordinator integration remains pending.'
assert text.count(old) == 1
contract.write_bytes(text.replace(old, 'Status: Issuer, client, and retained coordinator integration pass focused checks. Complete M02 qualification remains pending.').encode())
task_path = root / 'tasks/m1/identity.control_recovery.json'; task = json.loads(task_path.read_bytes())
task['current_step'] = 'replacement_recovery_and_failure_qualification'
task['remaining'] = ['Complete checked broker retirement and verify actual replacement recovery and failure races.',
    'Resolve the preserved participant-approval startup failure before the full phase gate.',
    'Complete cross-host cancellation and full M02 qualification on both hosts.']
task['coordinator_retirement'] = dict(state='Actual cleanup-to-retirement passed focused checks.',
    evidence='evidence/control-recovery-2026-10-09/coordinator-retirement-02/sb16/primary-review.json',
    contract='docs/COORDINATOR_RETIREMENT.txt', host='sb16', cases_per_profile=count, new_cases=8,
    profiles=review['profiles'], failed=0, skipped=0, full_phase_gate='pending on both hosts')
write(task_path, task)
driver_path = root / 'local/driver-state.json'; driver = json.loads(driver_path.read_bytes())
driver['current_step'] = task['current_step']
driver['last_turn'] = dict(classification='progress', detail=f'Actual coordinator retirement passed {count} cases per profile on sb16. M02 remains incomplete.')
driver['coordinator_retirement_check'] = dict(status='passed_and_collected', host='sb16', evidence=task['coordinator_retirement']['evidence'])
write(driver_path, driver)
with (root.parent / 'M02_REVOCATION_BARRIER_DEBUG.txt').open('ab') as stream:
    stream.write(f'''\n2026-10-10: Actual coordinator cleanup-to-retirement passed {count} selected cases per profile on sb16.
Eight new native cases and ten related suites passed in Debug and runtime ASan/UBSan. No failures or skips.
Evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/coordinator-retirement-02/sb16/primary-review.json.
The missing-step baseline remains in evidence/control-recovery-2026-10-09/coordinator-retirement-baseline01/sb16.
Only the exact retained world is retired. Native application response and broker survival are checked.
Broker retirement, replacement recovery, failure races, the earlier startup failure, cross-host cancellation, and full M02 qualification remain pending.
No normal session or database changed. M02 and Spatial-M1 remain incomplete.
'''.encode())
print(json.dumps(dict(source_files=337, selected_cases_per_profile=count, attachments=len(focused['attachments']))))
