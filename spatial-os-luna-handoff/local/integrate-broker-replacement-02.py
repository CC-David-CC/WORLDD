"""Verify exact collected bytes and record focused broker recovery qualification."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

assert len(sys.argv) == 2 and len(sys.argv[1]) == 64
root = Path(__file__).resolve().parents[1]
evidence = root / 'evidence/control-recovery-2026-10-09'
sha = lambda data: hashlib.sha256(data).hexdigest()
write = lambda path, value: path.write_bytes((json.dumps(value, indent=2) + '\n').encode())
for tag, digest in [('01', '242ad2aa05034fbd53760c31d9f903cfee58a7d2062c0e8ee848f104a89e6e15'), ('02', sys.argv[1])]:
    archive = root / ('local/broker-replacement-checks-' + tag + '-sb16.tar.gz')
    assert sha(archive.read_bytes()) == digest
    prefix = 'broker-replacement-' + tag + '/sb16/'
    target = evidence / prefix
    assert not target.exists()
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        assert len(members) == len({member.name for member in members})
        for member in members:
            name = PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            assert member.name.startswith(prefix)
            assert evidence.joinpath(*name.parts).resolve().is_relative_to(evidence.resolve())
        for member in members:
            path = evidence.joinpath(*PurePosixPath(member.name).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream: stream.write(tar.extractfile(member).read())
    focused = json.loads((target / 'collected-review.json').read_bytes())
    run = json.loads((target / 'checks/review.json').read_bytes())
    assert run['status'] == 'passed' and focused['status'] == 'focused_passed_phase_gate_pending'
    assert all(sha((target / name).read_bytes()) == value for name, value in focused['attachments'].items())
    assert sha((target / 'test_broker_replacement.py').read_bytes()) == run['source']['tests/native/test_broker_replacement.py']
    assert focused['profiles']['debug'] == focused['profiles']['runtime-sanitize']
    assert focused['profiles']['debug']['replacement'] == [3]
    assert len(focused['profiles']['debug'].get('focused', [])) == (0 if tag == '01' else 4)

integrated = {name: sha((root / name).read_bytes().replace(b'\r\n', b'\n')) for name in run['source']}
assert integrated == run['source'] and len(integrated) == 344 and len(run['locked']) == 41
assert all(integrated[name] == digest for name, digest in run['locked'].items())
previous = json.loads((evidence / 'session-replacement-03/sb16/checks/review.json').read_bytes())
changed = {name for name in integrated if integrated[name] != previous['source'].get(name)}
assert changed == {'CMakeLists.txt', 'tests/native/test_broker_replacement.py'}, changed
count = focused['selected_cases_per_profile']
assert count == sum(sum(cases) for cases in focused['profiles']['debug'].values())
review = dict(status='reviewed_focused_pass_phase_gate_pending', reviewer='primary agent', host='sb16',
    integrated_source=integrated, selected_cases_per_profile=count, new_cases=3, related_suites=4,
    failed=0, skipped=0, profiles=['Debug', 'runtime ASan/UBSan'], archive_sha256=sys.argv[1],
    attachments_verified=len(focused['attachments']), post_test_source_changes=0,
    production_launch_changed=False, limitations=focused['limitations'],
    exploratory_evidence='../../broker-replacement-01/sb16/collected-review.json',
    file_review={
        'CMakeLists.txt': 'Registers the three native cases in a delegated systemd scope. Uses existing binaries, fixture arguments, sanitizer flags, and the existing 240-second suite deadline.',
        'tests/native/test_broker_replacement.py': 'Crashes real world and broker processes. Uses checked cleanup and issuer retirement before starting a fresh production coordinator. Preserves original data paths. Checks fresh generations, native credentials and launch identities, exact saved objects, actor context, uncommitted files, cleared input, and unchanged old journals. Confirms uncertain execution rejects replay. Explicit new execution is confined and produces real native input with a fresh lease. Revoked credentials and premature replacement are rejected. Original and replacement witness logs are retained separately.',
        'docs/BROKER_REPLACEMENT_CHECKS.txt': 'Records the invariant, selected scenarios, related suites, and remaining qualification. No acceptance contract changed.',
        'controller helpers': 'Freeze 344 source files and verify all 41 locked files. Reuse proven identical compiled inputs only for exploratory checks. Rebuild after CMake registration. Verify source, binaries, collected logs and staged bytes.',
        'status and task records': 'Distinguish the completed focused checks from pending failure qualification and the full both-host phase gate.'
    })
write(target / 'primary-review.json', review)
status = root / 'CURRENT_STATE.md'; text = status.read_text(encoding='utf-8')
heading = '# Implementation status\n'; assert text.startswith(heading)
update = f'''
## 2026-10-10: Actual broker replacement passes focused native checks

Fresh production sessions recover the original world and workspace after checked cleanup and issuer retirement.
Saved geometry, actor context, workspace selection, and uncommitted project files survive.
Interrupted process records become uncertain. Replaying the old launch returns UNAVAILABLE without execution.
An explicit new launch creates a confined application with a fresh native identity and control lease.
Replacement applies current revocation before actor admission. Pending broker retirement prevents replacement execution.
The implementation already provided this path. Only the new test and CMake registration changed.

All {count} selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
These include three new native scenarios and four related suites.
All 344 tested source files match. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/broker-replacement-02/sb16/primary-review.json.
The exploratory three-case runs also passed in both profiles using verified unchanged binaries.
The final candidate was rebuilt after CMake registration and additional confinement assertions.

Next: failure races, the earlier participant-approval startup defect, and cross-host cancellation.
Full M02 qualification remains pending on both hosts.
Automatic application placement restoration and shared compositor lifetime remain required for M1.
No normal host session or database changed. M02 and Spatial-M1 remain incomplete.

'''
status.write_bytes((heading + update + text[len(heading):]).encode())
task_path = root / 'tasks/m1/identity.control_recovery.json'
task = json.loads(task_path.read_bytes())
task['current_step'] = 'failure_qualification_and_startup_diagnosis'
task['remaining'] = ['Qualify failure races and recovery interruptions.',
    'Resolve the preserved participant-approval startup failure before the full phase gate.',
    'Complete cross-host cancellation and full M02 qualification on both hosts.']
task['actual_broker_replacement'] = dict(state='Actual broker replacement passed focused native checks.',
    evidence='evidence/control-recovery-2026-10-09/broker-replacement-02/sb16/primary-review.json',
    selection='docs/BROKER_REPLACEMENT_CHECKS.txt', host='sb16', cases_per_profile=count, new_cases=3,
    profiles=review['profiles'], failed=0, skipped=0, full_phase_gate='pending on both hosts')
write(task_path, task)
driver_path = root / 'local/driver-state.json'; driver = json.loads(driver_path.read_bytes())
driver['current_step'] = task['current_step']
driver['last_turn'] = dict(classification='progress', detail=f'Broker replacement passed {count} cases per profile on sb16.')
driver['next_recovery_notes'] = [
    'Actual world and broker replacement now pass their focused native checks.',
    'Next qualify failure races, issuer restart, lost replies, and supervisor failure at recovery boundaries.',
    'Add bounded participant-approval diagnostics and reproduce the preserved startup failure before claiming it resolved.',
    'Complete cross-host cancellation, then run the full required gate on both hosts.']
driver['broker_replacement_check'].update(status='passed_and_collected', session=None,
    evidence=task['actual_broker_replacement']['evidence'], cases_per_profile=count, profiles=focused['profiles'])
write(driver_path, driver)
with (root.parent / 'M02_REVOCATION_BARRIER_DEBUG.txt').open('ab') as stream:
    stream.write(f'''\n2026-10-10: Broker replacement passed {count} cases per profile on sb16.
Three new scenarios and four related suites passed in Debug and runtime ASan/UBSan.
Evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/broker-replacement-02/sb16/primary-review.json.
The exploratory three-case checks passed in both profiles without rebuilding unchanged compiled inputs.
The final candidate was rebuilt after test registration. Production launch code did not change.
Interrupted execution is uncertain and cannot replay. New applications require an explicit launch.
Failure races, the earlier startup defect, cross-host cancellation, and the full both-host gate remain pending.
Automatic placement restoration and shared compositor lifetime remain required for M1.
No normal host session or database changed. M02 and Spatial-M1 remain incomplete.
'''.encode())
print(json.dumps(dict(source_files=344, selected_cases_per_profile=count,
    changed_source_files=sorted(changed), attachments=len(focused['attachments']))))
