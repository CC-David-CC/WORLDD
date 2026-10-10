"""Match working source to focused evidence before recording this increment."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

assert len(sys.argv) == 2 and len(sys.argv[1]) == 64
root = Path(__file__).resolve().parents[1]
sha = lambda data: hashlib.sha256(data).hexdigest()
write = lambda p, v: p.write_bytes((json.dumps(v, indent=2) + '\n').encode())
archive = root / 'local/session-broker-cleanup-focused-01-sb16.tar.gz'
assert sha(archive.read_bytes()) == sys.argv[1]
evidence = root / 'evidence/control-recovery-2026-10-09'
target = evidence / 'session-broker-cleanup-01/sb16'
assert not target.exists()
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    assert len(members) == len({m.name for m in members})
    for member in members:
        name = PurePosixPath(member.name)
        assert member.isfile() and not name.is_absolute() and '..' not in name.parts
        assert member.name.startswith('session-broker-cleanup-01/sb16/')
    for member in members:
        path = evidence.joinpath(*PurePosixPath(member.name).parts)
        assert path.resolve().is_relative_to(evidence.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream: stream.write(tar.extractfile(member).read())
focused = json.loads((target / 'focused-review.json').read_bytes())
run = json.loads((target / 'checks/review.json').read_bytes())
assert run['status'] == 'passed' and focused['failed'] == focused['skipped'] == 0
assert focused['profiles']['debug'] == focused['profiles']['runtime-sanitize']
assert focused['profiles']['debug']['broker-cleanup'] == [2, 4]
assert len(focused['profiles']['debug']['focused']) == 9
assert all(sha((target / n).read_bytes()) == h for n, h in focused['attachments'].items())
integrated = {n: sha((root / n).read_bytes().replace(b'\r\n', b'\n')) for n in run['source']}
assert integrated == run['source'] and len(integrated) == 340 and len(run['locked']) == 41
assert all(sha((root / n).read_bytes().replace(b'\r\n', b'\n')) == h for n, h in run['locked'].items())
for name in ('session-broker-cleanup-baseline01', 'session-launch-exit-baseline01'):
    baseline = evidence / name / 'sb16'
    record = json.loads((baseline / 'baseline-review.json').read_bytes())
    assert record['status'] in ('expected_missing_step_failure', 'expected_exit_status_failure')
    log = (baseline / 'baseline.log').read_text()
    assert 'Ran 1 test' in log and 'FAILED (failures=1)' in log
    assert all(sha((baseline / n).read_bytes()) == h for n, h in record['attachments'].items())
count = focused['selected_cases_per_profile']
review = dict(status='reviewed_focused_pass_phase_gate_pending', reviewer='primary agent', host='sb16',
    integrated_source=integrated, selected_cases_per_profile=count, new_cases=6, failed=0, skipped=0,
    profiles=['Debug', 'runtime ASan/UBSan'], archive_sha256=sys.argv[1], attachments_verified=len(focused['attachments']),
    post_test_source_changes=0, limitations=focused['limitations'], baselines=focused['baselines'],
    file_review={
        'CMakeLists.txt': 'Registers two focused exit cases and four real native cleanup cases. Existing suites remain unchanged.',
        'src/supervisor/session_launch.cpp': 'Retains actual waitpid status after reaping. Successful stop retries return the checked cached receipt. Failed exits remain subject to their original status.',
        'src/supervisor/session_launch.hpp': 'Documents exact retry and retained rejected-exit semantics.',
        'src/supervisor/session_broker.cpp': 'Uses creator ownership for cleanup, allowing the retained launch channel to drain after supervisor exit. Normal work still requires liveness.',
        'src/supervisor/session_coordinator.cpp': 'Separates retained ownership from broker liveness. Preserves exact world validation. Attempts broker fencing, logs failure, and still checks owned world process/native cleanup. Broker and complete-session cleanup remain separate.',
        'src/supervisor/session_coordinator.hpp': 'Exposes broker cleanup only through the trusted coordinator interface. Does not imply world cleanup or retirement.',
        'src/testing/session_probe.proto': 'Adds test-only stop_broker operation 17. Existing wire fields and actor interfaces remain unchanged.',
        'src/testing/session_application_probe.cpp': 'Forwards the private test request to production stop_broker without exiting the coordinator.',
        'src/testing/session_launch_exit_probe.cpp': 'Independent fault peer uses the actual SessionLaunch library. Deliberately reports cleanup and exits with a selected status. Does not claim native cleanup evidence.',
        'tests/core/test_session_launch_exit.py': 'Rejects exit codes 1, 2, 7 and 255 on both attempts. Requires identical checked results after exit zero.',
        'tests/native/test_session_broker_cleanup.py': 'Uses actual broker/application processes, pinned process handles, private journals, issuer state and disposable native server. Covers delayed native reply, separate world cleanup, independent native survival and supervisor death.',
        'docs/SESSION_BROKER_CLEANUP.txt': 'Defines the ownership, reply, exit, retry and partial-cleanup contract. Keeps issuer retirement separate.',
        'controller helpers': 'Preserve both reproduced failures, freeze source, run selected profiles, verify evidence and exact staged source. No normal session or database changes.'
    })
write(target / 'primary-review.json', review)
status = root / 'CURRENT_STATE.md'; text = status.read_text(encoding='utf-8')
heading = '# Implementation status\n'; assert text.startswith(heading)
update = f'''
## 2026-10-10: Retained broker crash cleanup passes focused checks

The session now retrieves checked broker cleanup after its original supervisor exits.
Exact retries preserve the receipt. Repeated waits retain the actual supervisor exit status.
A rejected nonzero exit cannot become successful cleanup on retry.
The coordinator can stop its exact owned world scopes when the broker is unavailable.
Broker cleanup does not replace world cleanup, reservation recovery, or issuer retirement.

All {count} selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These include four new native cases, two exit-validation cases, and nine related suites.
Native cases cover actual broker/application crashes, delayed native cleanup, independent credentials, and supervisor death.
All 340 tested source files match. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/session-broker-cleanup-01/sb16/primary-review.json.
Both original defect baselines remain preserved. No normal host session or database changed.

Next: broker issuer retirement and dependent recovery with a stopped broker.
Replacement recovery, failure races, cross-host cancellation, and full two-host qualification remain required for M02.
The earlier participant-approval startup failure remains unresolved despite subsequent passing checks.
Shared compositor lifetime across world transitions remains required for M1.
M02 and Spatial-M1 remain incomplete.

'''
status.write_bytes((heading + update + text[len(heading):]).encode())
contract = root / 'docs/SESSION_BROKER_CLEANUP.txt'; text = contract.read_text(encoding='utf-8')
old = 'Status: Implementation contract. Focused qualification remains pending.'; assert text.count(old) == 1
contract.write_bytes(text.replace(old, 'Status: Focused Debug and sanitizer checks passed. Full M02 qualification remains pending.').encode())
task_path = root / 'tasks/m1/identity.control_recovery.json'; task = json.loads(task_path.read_bytes())
task['current_step'] = 'broker_retirement_and_stopped_broker_dependencies'
task['remaining'] = ['Implement checked broker issuer retirement and dependent recovery with a stopped broker.',
    'Verify actual replacement recovery and failure races.',
    'Resolve the preserved participant-approval startup failure before the full phase gate.',
    'Complete cross-host cancellation and full M02 qualification on both hosts.']
task['broker_cleanup'] = dict(state='Checked broker cleanup after supervisor exit passed focused checks.',
    evidence='evidence/control-recovery-2026-10-09/session-broker-cleanup-01/sb16/primary-review.json',
    contract='docs/SESSION_BROKER_CLEANUP.txt', host='sb16', cases_per_profile=count, new_cases=6,
    profiles=review['profiles'], failed=0, skipped=0, full_phase_gate='pending on both hosts')
write(task_path, task)
driver_path = root / 'local/driver-state.json'; driver = json.loads(driver_path.read_bytes())
driver['current_step'] = task['current_step']
driver['last_turn'] = dict(classification='progress', detail=f'Broker cleanup passed {count} cases per profile on sb16. M02 remains incomplete.')
driver['broker_cleanup_check'].update(status='passed_and_collected', evidence=task['broker_cleanup']['evidence'],
    cases_per_profile=count, profiles=focused['profiles'])
write(driver_path, driver)
with (root.parent / 'M02_REVOCATION_BARRIER_DEBUG.txt').open('ab') as stream:
    stream.write(f'''\n2026-10-10: Retained broker crash cleanup passed {count} cases per profile on sb16.
Four new native cases, two exit-validation cases and nine related suites passed in Debug and runtime ASan/UBSan.
Evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/session-broker-cleanup-01/sb16/primary-review.json.
Both original failure baselines remain preserved. Broker cleanup is separate from world cleanup and issuer retirement.
Broker retirement and stopped-broker dependency recovery remain pending.
Replacement recovery, failure races, the earlier startup failure, cross-host cancellation and full M02 qualification remain pending.
No normal session or database changed. M02 and Spatial-M1 remain incomplete.
'''.encode())
print(json.dumps(dict(source_files=340, selected_cases_per_profile=count, attachments=len(focused['attachments']))))
