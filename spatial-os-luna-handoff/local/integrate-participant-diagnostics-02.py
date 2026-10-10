"""Verify baseline and focused evidence before recording the diagnostics increment."""
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
for tag, digest in [('01', '947da3d95a22a023d0650dc08a3e61cb7156441c9870cab444ac7a6f69270df5'), ('02', sys.argv[1])]:
    archive = root / ('local/participant-diagnostics-checks-' + tag + '-sb16.tar.gz')
    assert sha(archive.read_bytes()) == digest
    prefix = 'participant-diagnostics-' + tag + '/sb16/'
    target = evidence / prefix; assert not target.exists()
    with tarfile.open(archive) as tar:
        members = tar.getmembers(); assert len(members) == len({member.name for member in members})
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
    assert all(sha((target / name).read_bytes()) == value for name, value in focused['attachments'].items())
    if tag == '01':
        assert run['status'] == 'missing_diagnostics_reproduced' and run['exit_code'] == 1
        assert 'FAILED (failures=2)' in (target / 'checks/baseline.log').read_text()
        assert sha((target / 'test_identity_participant_runtime.py').read_bytes()) == run['source']['tests/core/test_identity_participant_runtime.py']
    else:
        assert run['status'] == focused['status'] == 'passed'
        assert focused['profiles']['debug'] == focused['profiles']['runtime-sanitize']
        assert len(focused['profiles']['debug']['focused']) == 4
        assert focused['startup_repetitions_per_profile'] == 10
        for profile in ('debug', 'runtime-sanitize'):
            assert sha((root / 'local/repeat-participant-startup-02.py').read_bytes()) == run['profiles'][profile]['repeat_script_sha256']

integrated = {name: sha((root / name).read_bytes().replace(b'\r\n', b'\n')) for name in run['source']}
assert integrated == run['source'] and len(integrated) == 344 and len(run['locked']) == 41
assert all(integrated[name] == digest for name, digest in run['locked'].items())
previous = json.loads((evidence / 'broker-replacement-02/sb16/checks/review.json').read_bytes())
changed = {name for name in integrated if integrated[name] != previous['source'][name]}
assert changed == {'src/identity/participant_runtime.cpp', 'tests/core/test_identity_participant_runtime.py',
    'tests/native/test_authority_supervisor.py'}, changed
count = focused['selected_cases_per_profile']
review = dict(status='reviewed_focused_pass_phase_gate_pending', reviewer='primary agent', host='sb16',
    integrated_source=integrated, selected_cases_per_profile=count, new_regressions=2, selected_suites=4,
    startup_repetitions_per_profile=10, failed=0, skipped=0, profiles=['Debug', 'runtime ASan/UBSan'],
    archive_sha256=sys.argv[1], attachments_verified=len(focused['attachments']), post_test_source_changes=0,
    historical_startup_cause='unconfirmed', full_phase_gate='pending on both hosts',
    file_review={
        'src/identity/participant_runtime.cpp': 'Retains operation names and bounded exception causes only in stderr diagnostics. Limits cause output to 240 printable ASCII bytes and replaces control bytes. Synchronized output prevents this diagnostic from interleaving with other synchronized writers. Existing codes, public messages, permanent failure, deadlines, and authority state transitions remain unchanged. Both direct issuer and inherited participant failure paths retain causes.',
        'tests/core/test_identity_participant_runtime.py': 'Two regressions reproduce missing diagnostics using unchanged production binaries. The corrected runtime still uses one connection, creates no activation, and returns the original error. Fault-peer control characters cannot create extra log lines. Runtime stderr survives fixture cleanup.',
        'tests/native/test_authority_supervisor.py': 'Preserves exact supervisor and child stderr bytes before fixture removal. Existing sanitizer assertions and all behavior checks remain.',
        'docs/PARTICIPANT_FAILURE_DIAGNOSTICS.txt': 'Records the actual missing evidence, diagnostic invariant, focused selection, and historical uncertainty without claiming a startup fix.',
        'controller helpers': 'Freeze source, verify locked files, build only needed binaries, run four suites and ten unchanged startup assertions per profile, preserve baseline failures, and verify collected and staged bytes.',
        'status records': 'Keep the original startup cause unconfirmed. Later successful startups are separate evidence and do not erase its failure.'
    })
write(target / 'primary-review.json', review)
status = root / 'CURRENT_STATE.md'; text = status.read_text(encoding='utf-8')
heading = '# Implementation status\n'; assert text.startswith(heading)
update = f'''
## 2026-10-10: Participant failure diagnostics preserve the cause

The participant worker now records the failed operation and a bounded cause on stderr.
Control characters become question marks. Cause text is limited to 240 bytes.
Public errors, deadlines, permanent failure behavior, and authorization rules remain unchanged.
Native supervisor and participant fixtures now preserve stderr before cleanup.
Two baseline tests reproduced missing diagnostics against unchanged production binaries.

All {count} selected cases passed in Debug and runtime sanitizers on sb16, without failures or skips.
The original native startup assertion also passed ten repetitions per profile with complete logs.
All 344 tested source files match. All 41 locked files remain unchanged.
Read evidence/control-recovery-2026-10-09/participant-diagnostics-02/sb16/primary-review.json.
The baseline failures remain in participant-diagnostics-01/sb16.

The historical participant-approval failure did not recur. Its original cause remains unconfirmed.
Successful repetition does not establish a fix for that failure.
Next: controlled approval interruptions, recovery races, and cross-host cancellation.
Full M02 qualification remains pending on both hosts. M02 and Spatial-M1 remain incomplete.
No normal host session or database changed.

'''
status.write_bytes((heading + update + text[len(heading):]).encode())
task_path = root / 'tasks/m1/identity.control_recovery.json'; task = json.loads(task_path.read_bytes())
task['current_step'] = 'approval_interruption_and_recovery_failure_qualification'
task['participant_failure_diagnostics'] = dict(state='Bounded diagnostics and log retention passed focused checks.',
    evidence='evidence/control-recovery-2026-10-09/participant-diagnostics-02/sb16/primary-review.json',
    contract='docs/PARTICIPANT_FAILURE_DIAGNOSTICS.txt', cases_per_profile=count, new_cases=2,
    startup_repetitions_per_profile=10, historical_startup_cause='unconfirmed', host='sb16',
    profiles=review['profiles'], full_phase_gate='pending on both hosts')
write(task_path, task)
driver_path = root / 'local/driver-state.json'; driver = json.loads(driver_path.read_bytes())
driver['current_step'] = task['current_step']
driver['last_turn'] = dict(classification='progress', detail=f'Participant diagnostics passed {count} cases and 10 startup repetitions per profile.')
driver['participant_diagnostics_check'].update(status='passed_and_collected', session=None,
    evidence=task['participant_failure_diagnostics']['evidence'], profiles=focused['profiles'], cases_per_profile=count)
driver['next_recovery_notes'] = [
    'Use bounded participant diagnostics and retained supervisor stderr for controlled startup and approval interruptions.',
    'The historical startup failure remains unconfirmed. Ten Debug and ten sanitizer repetitions passed without reproducing it.',
    'Current passing repetition logs show expected private-channel closure during checked shutdown; do not confuse it with failed startup.',
    'Qualify recovery failure races and cross-host cancellation, then run the complete both-host M02 gate.']
write(driver_path, driver)
with (root.parent / 'M02_REVOCATION_BARRIER_DEBUG.txt').open('ab') as stream:
    stream.write(f'''\n2026-10-10: Participant diagnostics passed {count} selected cases per profile on sb16.
The original startup assertion passed ten Debug and ten runtime-sanitizer repetitions.
Evidence: spatial-os-luna-handoff/evidence/control-recovery-2026-10-09/participant-diagnostics-02/sb16/primary-review.json.
Two missing-diagnostic baseline failures remain preserved in participant-diagnostics-01/sb16.
Worker stderr now identifies the failed operation and a bounded cause. Public errors and deadlines did not change.
Supervisor and participant logs survive test cleanup. The historical startup cause remains unconfirmed.
Current passing startup repetitions show expected private-channel closure during checked shutdown.
Controlled approval interruptions, recovery races, cross-host cancellation, and the full M02 gate remain pending.
No normal session or database changed. M02 and Spatial-M1 remain incomplete.
'''.encode())
print(json.dumps(dict(source_files=344, cases_per_profile=count, repetitions_per_profile=10,
    changed_source_files=sorted(changed), attachments=len(focused['attachments']))))
