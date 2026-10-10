"""Verify selected Linux evidence and record the reviewed durability increment."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

assert len(sys.argv) == 2 and len(sys.argv[1]) == 64
root = Path(__file__).resolve().parents[1]
sha = lambda data: hashlib.sha256(data).hexdigest()
write = lambda path, value: path.write_bytes((json.dumps(value, indent=2) + '\n').encode())
archive = root / 'local/dependent-cleanup-focused-01-sb16.tar.gz'
assert sha(archive.read_bytes()) == sys.argv[1]
evidence = root / 'evidence/control-recovery-2026-10-09'
target = evidence / 'dependent-cleanup-01/sb16'
assert not target.exists()
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    assert len(members) == len({m.name for m in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert member.name.startswith('dependent-cleanup-01/sb16/')
        path = evidence.joinpath(*name.parts)
        assert path.resolve().is_relative_to(evidence.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream: stream.write(tar.extractfile(member).read())
focused = json.loads((target / 'focused-review.json').read_bytes())
run = json.loads((target / 'checks/review.json').read_bytes())
assert run['status'] == 'passed' and focused['failed'] == focused['skipped'] == 0
assert focused['profiles']['debug'] == focused['profiles']['runtime-sanitize']
assert focused['profiles']['debug']['dependent-cleanup'] == [5]
assert len(focused['profiles']['debug']['focused']) == 8
assert all(sha((target / n).read_bytes()) == digest for n, digest in focused['attachments'].items())
integrated = {}
for name, digest in run['source'].items():
    data = (root / name).read_bytes().replace(b'\r\n', b'\n')
    integrated[name] = sha(data)
    assert integrated[name] == digest, name
assert len(integrated) == 334 and len(run['locked']) == 41
assert all(sha((root / n).read_bytes().replace(b'\r\n', b'\n')) == digest for n, digest in run['locked'].items())
baseline = json.loads((evidence / 'dependent-cleanup-baseline01/sb16/baseline-review.json').read_bytes())
assert all(sha((evidence / 'dependent-cleanup-baseline01/sb16' / n).read_bytes()) == digest
    for n, digest in baseline['attachments'].items())
count = focused['selected_cases_per_profile']
review = dict(status='reviewed_focused_pass_phase_gate_pending', reviewer='primary agent', host='sb16',
    source_record='checks/review.json', integrated_source=integrated,
    selected_cases_per_profile=count, new_cases=5, failed=0, skipped=0,
    profiles=['Debug', 'runtime ASan/UBSan'], archive_sha256=sys.argv[1],
    attachments_verified=len(focused['attachments']), post_test_source_changes=0,
    file_review={
        'CMakeLists.txt': 'Links the existing reply validator and adds one native suite in its own delegated scope. Existing checks remain selected.',
        'proto/spatial/supervisor/v1/launch.proto': 'Adds a bounded dependent-cleanup record without changing prior field numbers or launch stages.',
        'src/supervisor/launch.hpp': 'Makes the writer private to SessionCoordinator. No actor or supplied-receipt mutation API is added.',
        'src/supervisor/launch.cpp': 'Validates exact launch, issuer, native server, owner, cleanup, and reservation context. The original launch row stays unchanged. A single transaction creates and fills the receipt table and changes user_version. Locking and directory fsync precede success. Reopened journals create no live ownership. Changed retries fail.',
        'src/supervisor/session_coordinator.cpp': 'Retains the original journal path and supplies its checked world and live broker records. The caller supplies only an owner. Persistence failure remains an error after broker release.',
        'tests/native/test_dependent_cleanup.py': 'Uses actual supervised worlds, crash cleanup, issuer, broker, and confined native application. Covers exact persistence and retry, denied reopen launches, read-only and locked journal repair, and altered records. Explicit test names prevent inherited cases running twice.',
        'docs/DEPENDENT_CLEANUP_PERSISTENCE.txt': 'Records the additive protobuf and SQLite migration contract, ownership boundary, exact retry behavior, and remaining issuer retirement.',
        'docs/SESSION_CONTROL_COORDINATOR.txt': 'Updates the connected coordinator contract to require durable storage before reporting recovery success.',
        'controller helpers and evidence': 'Preserve both baseline attempts, validate exact source and log hashes, and keep runtime databases, keys, archives, and build outputs outside Git.'
    },
    baseline_review='Initial baseline errored because the fixture method journal collided with its inherited Path. The corrected helper reproduced absent durable storage against unchanged production. Both attempts remain preserved.',
    limitations=focused['limitations'] + ['No normal host session adopted this branch. Physical/model/performance acceptance was not run.'],
    baseline=focused['baseline'])
write(target / 'primary-review.json', review)
status = root / 'CURRENT_STATE.md'
text = status.read_text(encoding='utf-8'); heading = '# Implementation status\n'; assert text.startswith(heading)
update = f'''
## 2026-10-10: Durable dependent cleanup after checked broker recovery

SessionCoordinator now saves checked cleanup before reporting successful reservation recovery.
The protobuf receipt binds the stopped world, retained broker, and exact released reservations.
An explicit SQLite migration adds the receipt without changing the original launch row or revision.
Exact retries preserve the saved bytes. Failed storage remains an error after broker release.
Reopening the journal cannot recreate process ownership or authorize replacement execution.

All {count} selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
Five new native cases cover persistence, retries, journal failures, changed records, and denied replacement launches.
The initial baseline fixture error and corrected missing-durability failure remain preserved.
Read evidence/control-recovery-2026-10-09/dependent-cleanup-01/sb16/primary-review.json.
All 334 tested source files match. All 41 locked files remain unchanged.
No normal host session adopted this branch. Full qualification remains pending on both hosts.

Next: authenticate issuer retirement for the exact fenced generation and durable cleanup result.
Then resolve the earlier participant-approval startup failure and run the complete M02 phase gate.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.

'''
status.write_bytes((heading + update + text[len(heading):]).encode())
contract = root / 'docs/DEPENDENT_CLEANUP_PERSISTENCE.txt'
text = contract.read_text(encoding='utf-8')
old = 'Status: Implementation contract. Durable dependent receipts remain unimplemented.'
assert text.count(old) == 1
contract.write_bytes(text.replace(old, 'Status: Implemented on the M02 development branch. Focused checks passed. Full phase qualification remains pending.').encode())
coordinator_contract = root / 'docs/SESSION_CONTROL_COORDINATOR.txt'
text = coordinator_contract.read_text(encoding='utf-8')
old = 'Durable dependent receipt storage and issuer retirement remain unimplemented.'
assert text.count(old) == 1
text = text.replace(old, 'Durable dependent receipt storage is now implemented. Issuer retirement remains unimplemented.')
text += f'''
Durable dependent cleanup implementation, 2026-10-10

Recovery now records checked world cleanup and broker release before reporting success.
The saved protobuf binds the original stopped world to the exact retained, live broker.
The launch journal migrates from user_version 1 to 2 in one transaction.
The original launch row and revision remain unchanged.
Read docs/DEPENDENT_CLEANUP_PERSISTENCE.txt for the storage and retry contract.
All {count} selected cases passed in Debug and runtime sanitizers on sb16.
Read evidence/control-recovery-2026-10-09/dependent-cleanup-01/sb16/primary-review.json.
Reopened journals do not recreate live ownership. Issuer retirement and full phase qualification remain pending.
'''
coordinator_contract.write_bytes(text.encode())
task_path = root / 'tasks/m1/identity.control_recovery.json'; task = json.loads(task_path.read_bytes())
task['current_step'] = 'authenticated_issuer_retirement'
task['remaining'] = [
    'Implement authenticated issuer retirement of the exact fenced generation and durable cleanup result.',
    'Resolve the preserved participant-approval startup failure before the full phase gate.',
    'Qualify the complete M02 path and cumulative profiles on both hosts.'
]
task['dependent_cleanup'] = dict(state='Durable checked recovery receipt implemented and focused checks passed. Issuer retirement remains incomplete.',
    evidence='evidence/control-recovery-2026-10-09/dependent-cleanup-01/sb16/primary-review.json',
    contract='docs/DEPENDENT_CLEANUP_PERSISTENCE.txt', cases_per_profile=count, new_cases=5,
    host='sb16', profiles=review['profiles'], failed=0, skipped=0, full_phase_gate='pending on both hosts')
write(task_path, task)
driver_path = root / 'local/driver-state.json'; driver = json.loads(driver_path.read_bytes())
driver['current_step'] = task['current_step']
driver['last_turn'] = dict(classification='progress', detail=f'Durable dependent cleanup passed {count} selected cases per profile on sb16. Issuer retirement remains pending.')
driver['active_dependent_check'].update(status='passed_and_collected', profiles=focused['profiles'],
    evidence=task['dependent_cleanup']['evidence'])
write(driver_path, driver)
notes = root.parent / 'M02_REVOCATION_BARRIER_DEBUG.txt'
with notes.open('ab') as stream:
    stream.write(f'''\n2026-10-10: Durable dependent cleanup passed {count} selected cases per profile on sb16.
Five new native scenarios passed in Debug and runtime ASan/UBSan. No failures or skips.
The first baseline failed because journal() collided with an inherited Path fixture field.
The corrected world_database() helper reproduced the missing durable receipt with unchanged production.
Both attempts remain in spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/dependent-cleanup-baseline01.
Passing evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/dependent-cleanup-01/sb16/primary-review.json.
No normal host session adopted this branch. Full qualification remains pending on both hosts.
No physical power-loss injection was performed. Issuer retirement and the earlier startup failure remain open.
The tested source is unchanged. M02 and Spatial-M1 remain incomplete.
'''.encode())
print(json.dumps(dict(attachments=len(focused['attachments']), source_files=len(integrated), selected_cases_per_profile=count)))
