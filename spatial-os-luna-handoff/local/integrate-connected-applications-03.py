"""Verify collected evidence and record a focused integration checkpoint."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

root = Path(__file__).resolve().parents[1]
sha = lambda data: hashlib.sha256(data).hexdigest()
write = lambda path, value: path.write_bytes((json.dumps(value, indent=2) + '\n').encode())
archive = root / 'local/connected-applications-focused-03-sb16.tar.gz'
archive_hash = '0cf131c38ad730a48ee6ed73a8cf808b9f5e45743cd76812fbef49826d33f357'
assert sha(archive.read_bytes()) == archive_hash
evidence = root / 'evidence/control-recovery-2026-10-09'
target = evidence / 'connected-applications-03/sb16'
assert not target.exists()
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    names = [m.name for m in members]
    assert len(names) == len(set(names))
    for member in members:
        path = PurePosixPath(member.name)
        assert member.isfile() and not path.is_absolute() and '..' not in path.parts
        assert member.name.startswith('connected-applications-03/sb16/')
        destination = evidence.joinpath(*path.parts)
        assert destination.resolve().is_relative_to(evidence.resolve())
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(tar.extractfile(member).read())
focused = json.loads((target / 'focused-review.json').read_bytes())
run = json.loads((target / 'checks/review.json').read_bytes())
assert focused['selected_cases_per_profile'] == 100 and run['status'] == 'passed'
assert focused['failed'] == focused['skipped'] == 0
assert all(sha((target / name).read_bytes()) == digest for name, digest in focused['attachments'].items())
assert len(run['source']) == 333 and len(run['locked']) == 41
comment_file = 'src/testing/session_application_probe.cpp'
old_comment = b'// Forward management to the real coordinator. Application actions arrive over actor TLS.'
new_comment = b'// Forward management to the real coordinator. Application actions use its local actor channel.'
integrated = {}
for name, digest in run['source'].items():
    data = (root / name).read_bytes().replace(b'\r\n', b'\n')
    integrated[name] = sha(data)
    if name == comment_file:
        assert data.count(new_comment) == 1 and old_comment not in data
        assert sha(data.replace(new_comment, old_comment)) == digest
    else:
        assert sha(data) == digest, name
assert all(sha((root / name).read_bytes().replace(b'\r\n', b'\n')) == digest for name, digest in run['locked'].items())
review = dict(status='reviewed_focused_pass_phase_gate_pending', reviewer='primary agent',
    host='sb16', source_record='checks/review.json', integrated_source=integrated,
    selected_cases_per_profile=100, new_native_application_cases=2, related_cases=98,
    profiles=['Debug', 'runtime ASan/UBSan'], failed=0, skipped=0,
    archive_sha256=archive_hash, attachments_verified=len(focused['attachments']),
    production_files=['src/supervisor/session_coordinator.cpp', 'src/supervisor/session_coordinator.hpp'],
    other_reviewed_files=['CMakeLists.txt', 'src/testing/session_probe.proto', comment_file,
        'tests/native/test_session_world.py', 'tests/native/test_session_application.py'],
    review=[
        'The coordinator creates local actor sockets. It never admits a caller-supplied local endpoint.',
        'Socket pairs are close-on-exec. Only explicit world endpoints enter the supervised launch.',
        'Transfer requires an exact retained live owner and consumes the endpoint once.',
        'RAII closes sockets on allocation errors, failed startup, and world shutdown.',
        'The sixteen-descriptor supervisor limit still applies, including three reserved descriptors and an optional TLS listener.',
        'Signed actor authentication, persistent grants, broker confinement, and semantic action handlers remain authoritative.',
        'The new protobuf transport is test-only and forwards management to production coordinator methods.',
        'The independent actor receives only its actor socket, with a protobuf owner binding and SCM_RIGHTS.',
        'The actor launches the real native probe through the confined application broker.',
        'Native observations verify actual presses and releases. Application resize acknowledgments prove post-crash response.',
        'Live process pins and descriptor inventories check coordinator isolation. Persistent journal contents match the checked receipt.',
        'The failed TLS input assumption is preserved. The corrected local path does not widen remote-input authority.',
        'Evidence collection creates its destination before retaining application logs. Locked files remain unchanged.',
        'Packaging, driver, collector, and integration helpers were reviewed. Candidate02 was packaged but never executed.'
    ],
    post_test_change=dict(file=comment_file, kind='one comment only',
        reason='Correct stale TLS wording to identify the actual local actor channel.',
        verified='Replacing that comment with its tested version reproduces the exact tested file hash.',
        testing='No runtime test repeated for a comment-only correction.'),
    limitations=focused['pending'] + [
        'This increment ran on sb16 only. Full two-host qualification remains pending.',
        'The two new scenarios do not qualify every local-channel count or transfer rejection path.',
        'Physical input, model tasks, performance, and extended native sessions were not run for this increment.',
        'No normal host session adopted the development branch.'
    ],
    baseline='../connected-application-baseline01/sb16/baseline-review.json')
write(target / 'primary-review.json', review)

status = root / 'CURRENT_STATE.md'
text = status.read_text(encoding='utf-8')
heading = '# Implementation status\n'
assert text.startswith(heading)
update = '''
## 2026-10-10: Local actors and confined application crash recovery

SessionCoordinator now creates local actor channels and transfers each endpoint once to an exact retained world owner.
The actor authenticates through the existing signed proof and uses persistent world and workspace authority.
The real actor launches a confined native application through the supervised broker.
Actor cancellation releases held native input and its control reservation.
A world crash also releases native input. The application remains responsive through the surviving broker.
Checked process and native cleanup match the durable journal. The failed world's reservation remains fenced and pending.

All 100 selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These comprise two new native scenarios and 98 related cases across seven suites.
Read evidence/control-recovery-2026-10-09/connected-applications-03/sb16/primary-review.json.
The initial remote-input rejection and fixture evidence-directory error remain preserved in connected-application-baseline01.
The corrected path keeps remote application input disabled. All 41 locked files remain unchanged.
One stale source comment was corrected after testing. Executable source is unchanged.
No normal host session adopted this branch. Full qualification on both hosts remains pending.

Next: release only the checked, fenced world's reservations while preserving unrelated owners and application execution.
Then persist the dependent cleanup receipt and implement issuer retirement.
Resolve the earlier participant-approval startup failure before the M02 phase gate.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.

'''
status.write_bytes((heading + update + text[len(heading):]).encode())
task_path = root / 'tasks/m1/identity.control_recovery.json'
task = json.loads(task_path.read_bytes())
task['current_step'] = 'implement_selective_owned_reservation_recovery'
task['remaining'] = [
    'Release only a fenced owner\'s reservations after checked process and native cleanup.',
    'Verify unrelated owner reservations and application execution remain usable.',
    'Persist a checked dependent receipt before issuer retirement.',
    'Resolve the preserved participant-approval startup failure before the full phase gate.',
    'Qualify the complete M02 path and cumulative profiles on both hosts.'
]
task['connected_application'] = dict(
    state='Focused native recovery passed. Selective reservation release remains unimplemented.',
    evidence='evidence/control-recovery-2026-10-09/connected-applications-03/sb16/primary-review.json',
    cases_per_profile=100, new_cases=2, related_cases=98, failed=0, skipped=0, host='sb16',
    profiles=['Debug', 'runtime ASan/UBSan'], full_phase_gate='pending on both hosts')
write(task_path, task)
driver_path = root / 'local/driver-state.json'
driver = json.loads(driver_path.read_bytes())
driver['current_step'] = task['current_step']
driver['last_turn'] = dict(classification='progress', detail='Local actor endpoints and real confined application recovery passed 100 selected cases per profile on sb16. Selective release remains pending.')
driver['active_application_check'] = dict(candidate='connected-applications-03', session=47262,
    status='passed_and_collected', profiles=focused['profiles'], evidence=task['connected_application']['evidence'])
driver['connected_application'] = task['connected_application']
write(driver_path, driver)
print(json.dumps(dict(attachments=len(focused['attachments']), canonical_source_files=len(integrated),
    comment_only_difference=comment_file, selected_cases_per_profile=100, locked_files=41)))
