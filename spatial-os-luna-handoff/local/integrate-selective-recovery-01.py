"""Verify collected Linux evidence before recording focused recovery integration."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

assert len(sys.argv) == 2 and len(sys.argv[1]) == 64
root = Path(__file__).resolve().parents[1]
sha = lambda data: hashlib.sha256(data).hexdigest()
write = lambda path, value: path.write_bytes((json.dumps(value, indent=2) + '\n').encode())
archive = root / 'local/selective-recovery-focused-01-sb16.tar.gz'
assert sha(archive.read_bytes()) == sys.argv[1]
evidence = root / 'evidence/control-recovery-2026-10-09'
target = evidence / 'selective-recovery-01/sb16'
assert not target.exists()
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    assert len(members) == len({m.name for m in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert member.name.startswith('selective-recovery-01/sb16/')
        path = evidence.joinpath(*name.parts)
        assert path.resolve().is_relative_to(evidence.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream: stream.write(tar.extractfile(member).read())
focused = json.loads((target / 'focused-review.json').read_bytes())
run = json.loads((target / 'checks/review.json').read_bytes())
assert run['status'] == 'passed' and focused['failed'] == focused['skipped'] == 0
assert focused['profiles']['debug'] == focused['profiles']['runtime-sanitize']
assert focused['profiles']['debug']['native-application'] == [3]
assert len(focused['profiles']['debug']['focused']) == 9
assert all(sha((target / n).read_bytes()) == digest for n, digest in focused['attachments'].items())
old = b'// Only stop_world can set stopped, after SessionLaunch validates both owned receipts.'
new = b'// Stopped state requires SessionLaunch to validate both owned cleanup receipts.'
comment_file = 'src/supervisor/session_coordinator.cpp'
integrated = {}
for name, digest in run['source'].items():
    data = (root / name).read_bytes().replace(b'\r\n', b'\n')
    integrated[name] = sha(data)
    if name == comment_file:
        assert data.count(new) == 1 and old not in data
        assert sha(data.replace(new, old)) == digest
    else: assert sha(data) == digest, name
assert len(integrated) == 333 and len(run['locked']) == 41
assert all(sha((root / n).read_bytes().replace(b'\r\n', b'\n')) == digest for n, digest in run['locked'].items())
count = focused['selected_cases_per_profile']
review = dict(status='reviewed_focused_pass_phase_gate_pending', reviewer='primary agent', host='sb16',
    source_record='checks/review.json', integrated_source=integrated,
    selected_cases_per_profile=count, new_cases=15, failed=0, skipped=0,
    profiles=['Debug', 'runtime ASan/UBSan'], archive_sha256=sys.argv[1],
    attachments_verified=len(focused['attachments']),
    review=[
        'Additive protobuf recovery fields preserve existing operation numbers. No locked file changes.',
        'Generated process-scope bindings move into the shared protobuf library to support typed broker receipts.',
        'The private administrative channel authenticates the retained coordinator. World and actor channels cannot invoke recovery.',
        'The coordinator accepts an owner only. It derives cleanup from its retained supervisor result after verified stop.',
        'The broker requires the exact registered owner, matching process/native receipts, and a fenced closed channel.',
        'Receipt shape validation does not prove external cleanup. That proof belongs to the authenticated production coordinator.',
        'The broker prepares released leases and map iterators before mutation. Erasure and receipt retention allocate nothing.',
        'The mutation performs no authority refresh, callbacks, process cleanup, or reentrant request pumping.',
        'Only that owner\'s reservations are erased. Applications and unrelated owners remain unchanged.',
        'The cached receipt survives response construction failure. Exact retries and inspections return the original result.',
        'Client validation checks full cleanup matching, continued fencing, empty active reservations, unique released leases, and bounds.',
        'The native test rejects premature recovery and owner substitution. Actual crash cleanup supplies both checked receipts.',
        'The independent native application responds after release. Component tests verify unrelated reservation and application survival.',
        'Missing, mismatched, changed, descriptor-bearing, and world-channel recovery requests are rejected.',
        'Discarded recovery replies and lost acquisition replies are covered without depending on caller-known lease IDs.',
        'Existing native retention checks remain intact. One new native, nine broker, and five reply-validator cases were added.',
        'Packaging, driver, collector, and integration helpers were reviewed. No local model or subagent review was used.'
    ],
    post_test_change=dict(file=comment_file, kind='one comment only',
        reason='Clarify that both normal stop and failed-start cleanup require checked SessionLaunch receipts.',
        verified='Restoring the tested comment reproduces the tested source hash. No executable statement changed.'),
    limitations=focused['limitations'] + ['No normal host session adopted this branch. Physical/model/performance acceptance was not run.'],
    baseline=focused['baseline'])
write(target / 'primary-review.json', review)
status = root / 'CURRENT_STATE.md'
text = status.read_text(encoding='utf-8'); heading = '# Implementation status\n'; assert text.startswith(heading)
update = f'''
## 2026-10-10: Selective reservation recovery through retained world ownership

SessionCoordinator now releases a stopped world's reservations after checked process and native cleanup.
The broker requires the exact fenced owner. It preserves unrelated owners and independently authorized applications.
Exact retries and inspection return the original cleanup receipt. Changed or incomplete receipts are rejected.
World and actor channels cannot invoke this private recovery operation.
The receipt remains in broker memory. Durable dependent evidence and issuer retirement remain unimplemented.

All {count} selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
Fifteen new cases cover broker recovery, reply validation, and an actual coordinator-managed world crash.
The native application remains responsive after its failed world's reservation is released.
Read evidence/control-recovery-2026-10-09/selective-recovery-01/sb16/primary-review.json.
All 41 locked files remain unchanged. One explanatory comment changed after testing; executable statements did not.
No normal host session adopted this branch. Complete qualification remains pending on both hosts.

Next: persist the checked dependent receipt, bound to the exact retained broker and world launches.
Then implement authenticated issuer retirement and resolve the earlier participant-approval startup failure.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.

'''
status.write_bytes((heading + update + text[len(heading):]).encode())
task_path = root / 'tasks/m1/identity.control_recovery.json'; task = json.loads(task_path.read_bytes())
task['current_step'] = 'persist_checked_dependent_cleanup_receipt'
task['remaining'] = [
    'Persist checked world cleanup and broker recovery receipts before reporting issuer retirement.',
    'Implement authenticated issuer retirement of the exact fenced generation.',
    'Resolve the preserved participant-approval startup failure before the full phase gate.',
    'Qualify the complete M02 path and cumulative profiles on both hosts.'
]
task['selective_recovery'] = dict(state='Focused production recovery passed. Durable receipt and issuer retirement remain incomplete.',
    evidence='evidence/control-recovery-2026-10-09/selective-recovery-01/sb16/primary-review.json',
    cases_per_profile=count, new_cases=15, host='sb16', profiles=review['profiles'], failed=0, skipped=0,
    full_phase_gate='pending on both hosts')
write(task_path, task)
driver_path = root / 'local/driver-state.json'; driver = json.loads(driver_path.read_bytes())
driver['current_step'] = task['current_step']
driver['last_turn'] = dict(classification='progress', detail=f'Selective recovery passed {count} selected cases per profile on sb16. Durable dependent receipt and issuer retirement remain pending.')
driver['active_selective_check'].update(status='passed_and_collected', profiles=focused['profiles'],
    evidence=task['selective_recovery']['evidence'])
write(driver_path, driver)
print(json.dumps(dict(attachments=len(focused['attachments']), source_files=len(integrated), selected_cases_per_profile=count)))
